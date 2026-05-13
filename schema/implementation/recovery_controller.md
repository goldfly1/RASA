# Recovery Controller

> **Architectural Reference:** `architectural_schema_v2.1.md` §11  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — session recovery must restore soul_id and re-resolve prompt state   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Owns crash recovery, deterministic replay, and restart hygiene. Coordinates with the Orchestrator, State Store, and Sandbox to restore agent sessions and the task graph after failure. Ensures that recovered agents resume with the correct `soul_id`, prompt template, and conversation context.

---

## 2. Internal Design

### 2.1 Internal State Machine

`STANDBY → DETECTING → REPLAYING → VALIDATING → RESTORED / FAILED`

**Soul-aware transitions:**

| State | Trigger | Pilot Action |
|-------|---------|--------------|
| **STANDBY** | Normal operation | Monitor heartbeat topic; maintain heartbeat ledger per `agent_id` + `soul_id`. |
| **DETECTING** | Heartbeat miss > threshold | Look up last checkpoint for `agent_id`; retrieve `soul_id` and `prompt_version_hash` from checkpoint metadata in PostgreSQL. |
| **REPLAYING** | Checkpoint found | Re-read soul sheet from `<project_root>/souls/` directory (in case of hot reload since checkpoint). Validate `soul_version` in checkpoint matches current soul sheet; mismatch → upgrade path or fallback. |
| **VALIDATING** | Replay complete | Reconstruct prompt from template + context; validate `prompt_version_hash` matches checkpoint. If hash mismatch, emit `PROMPT_DRIFT` alert. |
| **RESTORED** | Validation passes | Resume agent session in `RECOVERING` state; notify Orchestrator task is back in `IN_PROGRESS`. |
| **FAILED** | Validation fails or checkpoint missing | Escalate to Orchestrator; Orchestrator may reschedule task with same or different `soul_id`. |

**Recovery latency target:** 5 seconds max from heartbeat miss → RESTORED or FAILED.

### 2.2 Checkpoint Structure

Each checkpoint is a JSON blob in PostgreSQL with flat-file pointers:

```json
{
  "checkpoint_id": "uuid",
  "agent_id": "agent-42",
  "soul_id": "coder-v2-dev",
  "soul_version": "1.0.0",
  "prompt_version_hash": "a3f2...",
  "task_id": "0195f...",
  "session_state": { },
  "conversation_log_pointer": "file://data/archive/0195f.../conversation.json",
  "memory_context_pointer": "file://data/archive/0195f.../context.json",
  "idempotency_sequence": 42
}
```

> **Upgrade path:** Replace `file://` pointers with S3 URIs when deploying across hosts.

### 2.3 Idempotency Ledger

The ledger is a **PostgreSQL table** (durable by design):

- Columns: `task_id`, `agent_id`, `soul_id`, `sequence_number`, `action_hash`, `result_status`
- Unique constraint on `(task_id, sequence_number)` — prevents duplicate replay of the same action
- On replay, the Recovery Controller skips actions whose `action_hash` is already `SUCCESS` in the ledger
- Failed actions are recorded with `FAILED` status and can be retried

### 2.4 Soul Version Mismatch Handling

If checkpoint `soul_version` ≠ available soul sheet version:

- **Minor/patch diff:** Attempt forward-migration — re-read the updated soul sheet from `<project_root>/souls/`. The new `prompt_version_hash` is computed; the old checkpoint's prompt hash is logged but not required to match.
- **Major diff:** Fail recovery. Orchestrator reschedules the task with a fresh agent using the current soul sheet version.
- **Migration format:** Declarative YAML patches for minor/patch upgrades (simpler to implement and audit than imperative migration code).

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Go 1.24+** | Redis Pub/Sub heartbeat monitoring, PostgreSQL ledger queries, checkpoint replay — single compiled binary. |
| **Checkpoint store** | **PostgreSQL** (`checkpoints` table) | JSONB for flexible state; indexed by `agent_id` and `task_id`. |
| **Archive store** | **Flat files on disk** (`<data_root>/archive/`) | Full conversation logs and memory context. Pointers stored in checkpoint JSON. |
| **Idempotency Ledger** | **PostgreSQL** (`idempotency_ledger` table) | Durable, ACID-guaranteed. Unique constraint prevents duplicate execution. |
| **Heartbeat monitoring** | **Redis Pub/Sub** (`agents.heartbeat.*` pattern subscription) | Recovery Controller consumes all heartbeat topics; detects miss by absence. |

---

## 4. Deployment Topology

- **Process:** Native Go binary, started via Procfile:
  ```
  recovery: recovery-controller --db postgres://localhost/rasa_recovery 
  ```
- **Dependencies:** Local PostgreSQL, local Redis.
- **Startup:** Runs alongside the Orchestrator and Pool Controller. Consumes heartbeat topics from all agents.
- **Data access:** Reads checkpoint state from PostgreSQL; reads archive blobs from local flat files.
- **Agent recovery:** On successful recovery, the Recovery Controller publishes a `session.restored` notification via PostgreSQL LISTEN/NOTIFY. The Orchestrator listens for this to update task state.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Recovery duration (DETECTING → RESTORED) | Timed per recovery event | > 5s — review checkpoint read latency |
| Recovery failure rate | Counted per recovery attempt | > 0 — investigate checkpoint integrity |
| PROMPT_DRIFT alerts | Counted per validation | > 0 — soul sheet changed since checkpoint |
| Idempotency ledger size | Rows in table | > 10K rows — archive old entries |
| Heartbeat miss rate | Counted per agent | > 1% in 60s — agent overloaded or hung |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Is the Idempotency Ledger an in-memory structure or durable store? | **Resolved:** PostgreSQL table — durable, ACID, unique constraint on (task_id, sequence_number). |
| 2 | What is the maximum acceptable recovery latency? | **Resolved:** 5 seconds for pilot. Can be tightened as the system matures. |
| 3 | Should soul sheet migrations be declarative (YAML patch) or imperative (code)? | **Resolved:** Declarative YAML patches for pilot. Imperative code for complex migrations as upgrade. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: replaced S3 archive pointers with local flat-file paths, added recovery latency target (5s), added declarative YAML patch migration strategy, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul-aware checkpoint structure, prompt hash validation, and soul version mismatch handling | ? |

---

*This document implements the recovery contract defined in `architectural_schema_v2.1.md` §11. Checkpoint format aligns with `agent_runtime.md` §2.4. Idempotency ledger aligns with `orchestrator.md` task lifecycle.*

