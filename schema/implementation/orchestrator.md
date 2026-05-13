# Orchestrator

> **Architectural Reference:** `architectural_schema_v2.1.md` §1 / §3  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — soul-aware task assignment   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Central scheduler: decomposes work into Tasks, assigns them to agents via the Pool Controller, drives the Task Lifecycle state machine, and enforces canonical model constraints before diff application.

**Soul-aware:** The Orchestrator embeds `soul_id` into every `Task` envelope so the Pool Controller and Agent Runtime know which personality, prompt template, and tool policy to load.

---

## 2. Internal Design

### 2.1 Internal State Machine

Derived from Task Lifecycle:
`PENDING → ASSIGNED → IN_PROGRESS → VERIFICATION → COMPLETE / ESCALATED`

**Soul-aware transitions:**

| Transition | Trigger | Soul-Specific Action |
|------------|---------|---------------------|
| **PENDING → ASSIGNED** | Task decomposed, capability match found | Orchestrator selects `soul_id` based on `task.required_role` and `task.tags`. See §2.3. |
| **ASSIGNED → IN_PROGRESS** | Agent heartbeat ACK with matching `soul_id` | Pool Controller confirms agent has pre-loaded the requested soul sheet. |
| **IN_PROGRESS → VERIFICATION** | Agent emits `TASK_OUTPUT` | Sandbox Pipeline gates the output (temp-dir jail in pilot); soul sheet `behavior.tool_policy` is validated by Policy Engine. |
| **VERIFICATION → COMPLETE** | All gates pass | Evaluation Engine records `soul_id` + `prompt_version_hash` for regression tracking. |
| **VERIFICATION → ESCALATED** | Gate fails or budget exhausted | Recovery Controller may reschedule with a different `soul_id` (e.g., escalate from CODER to ARCHITECT). |

### 2.2 Task Envelope (soul-aware fields)

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `task_id` | UUID | Orchestrator | Unique identifier |
| `soul_id` | string | Orchestrator | Resolved from capability matching; references [`agent_configuration.md`](agent_configuration.md) §2.2 |
| `required_role` | enum | Orchestrator | `PLANNER`, `CODER`, `REVIEWER`, `ARCHITECT` |
| `tags` | string[] | Task metadata | Matched against `metadata.tags` in soul sheets |
| `budget_tier` | enum | Orchestrator | Override or fallback if soul sheet tier is unavailable |
| `prompt_context` | JSON | Memory Subsystem | Injected into soul sheet template variables |

### 2.3 Capability Matching

The Orchestrator maintains a **Capability Index** (PostgreSQL table `agent_capabilities`) mapping:

- `soul_id` → `agent_role`, `metadata.tags`, `model.default_tier`, `behavior.tool_policy.allowed_tools`
- `task.required_role + task.tags` → ranked list of compatible `soul_id`s

The index is populated at startup by scanning `<project_root>/souls/*.yaml` and loading soul sheets into PostgreSQL. It is incrementally updated when the filesystem watcher detects a soul sheet change.

**Assignment logic:**
1. Filter by `required_role` exact match.
2. Score by tag overlap with `metadata.tags`.
3. Filter out `soul_id`s whose `model.default_tier` exceeds current budget ceiling.
4. Select highest score; fallback to next if no warm agent with that `soul_id` is idle.
5. Publish to `tasks_assigned` channel.
6. If Pool Controller NACKs (no agent available), retry after **5 seconds**. Retry up to 3 times, then escalate.

### 2.4 Cyclic Dependency Detection

The Task DAG is validated before assignment. If a cycle is detected:
- Decompose the cycle into a serial sub-plan.
- Assign all sub-tasks to the same `soul_id` to preserve context.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Go 1.24+** | Goroutines/channels for concurrent task lifecycle management, subscription, and retry timers. |
| **Task state** | **PostgreSQL** (`tasks` table) | ACID guarantees for the state machine. Each task has a current state, assigned `soul_id`, and transition history. |
| **Capability Index** | **PostgreSQL** (`agent_capabilities` table) | Populated from soul sheet scan at startup; incrementally updated on file change. |
| **Assignment transport** | **PostgreSQL LISTEN/NOTIFY** (channel `tasks_assigned`) | Orchestrator publishes; Pool Controller consumes and routes to agents. |
| **Input interface** | **JSON over stdin** (pilot CLI) or the **PostgreSQL** `tasks_submit` channel | Tasks can be submitted directly via CLI or via a row insert + NOTIFY for automated pipelines. |

---

## 4. Deployment Topology

- **Process:** Native Go binary, started via Procfile:
  ```
  orchestrator: orchestrator --db postgres://localhost/rasa_orch 
  ```
- **Startup order:** Redis → PostgreSQL → **Orchestrator** → Pool Controller → Agent Runtime.
- **Dependencies:** Local PostgreSQL, local Redis.
- **Task submission:** Tasks can be submitted via:
  - CLI: `orchestrator submit --soul coder-v2-dev --task '{"title": "..."}'`
  - PG LISTEN/NOTIFY: Insert a task row and NOTIFY to the `tasks_submit` channel.
- **No external API exposure in pilot** — the Orchestrator accepts tasks via CLI or PostgreSQL LISTEN/NOTIFY only. REST API (via gRPC-Gateway) is the documented upgrade path.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Task assignment latency (PENDING → ASSIGNED) | Timed per task | > 2s — capability index query slow |
| Assignment retry count | Counted per task | > 3 retries — no agent available for soul_id |
| Task lifecycle duration | Tracked per state transition | Escalate if any task stays > 10 min in ASSIGNED |
| Capability index staleness | Diff soul files vs. PostgreSQL | Any mismatch — filesystem watcher may have missed an event |
| Cycle detection hits | Counted per task DAG | > 0 — flag for workflow design review |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How does the Orchestrator detect and break cyclic task dependencies? | **Resolved:** DAG validation before assignment. Cycles decomposed into serial sub-plans with same `soul_id`. |
| 2 | What is the assignment retry interval? | **Resolved:** 5 seconds, max 3 retries, then escalate. |
| 3 | Should the Capability Index be rebuilt on every soul sheet change, or incrementally updated? | **Resolved:** Incremental update via filesystem watcher. Full rebuild on Orchestrator restart. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: added retry interval (5s, 3 attempts), capability index population from local souls/ directory, native Go binary deployment, task submission via CLI/PostgreSQL, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul-aware task envelope, capability matching, and cyclic dependency handling | ? |

---

*This document implements the scheduling contract defined in `architectural_schema_v2.1.md` §1 and §3. Task envelope aligns with `pool_controller.md` §2.2 and `agent_configuration.md` §2.2.*

