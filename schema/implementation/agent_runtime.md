# Agent Runtime

> **Architectural Reference:** `architectural_schema_v2.1.md` §2.1 / §3.2  
> **Status:** Updated 2026-05-13 — core runtime functional  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Implements the physical agent process: session lifecycle management, heartbeat emission, tool binding invocation, and conversation log maintenance. The runtime loads a **soul sheet** (see [`agent_configuration.md`](agent_configuration.md)) at session start to determine identity, prompt templates, tool policies, and model preferences.

---

## 2. Internal Design

### 2.1 Internal State Machine

Maps to Agent Session Lifecycle in architecture:
`IDLE → WARMING → ACTIVE → PAUSED → RESUMING → ACTIVE → CHECKPOINTED → RECOVERING`

**State details:**

| State | Trigger | Side Effects |
|-------|---------|--------------|
| **IDLE** | Agent process starts, no task assigned | Load soul sheet; register with Pool Controller; begin heartbeat |
| **WARMING** | Task envelope received from Orchestrator | Resolve soul sheet version; assemble prompt from template + context; warm model connection via LLM Gateway |
| **ACTIVE** | Prompt sent to LLM Gateway | Execute tool calls per soul sheet `behavior.tool_policy`; stream reasoning traces to Observability Stack |
| **PAUSED** | Orchestrator checkpoint signal or sandbox gate pending | Persist working memory to Redis; emit `SESSION_PAUSED` event |
| **RESUMING** | Checkpoint validated or sandbox gate passed | Reload memory from Redis; resume conversation context |
| **CHECKPOINTED** | Task completed successfully | Write full state dump + evaluation metadata to PostgreSQL; archive conversation log to flat files at `<data_root>/archive/{task_id>/` |
| **RECOVERING** | Crash detected by Recovery Controller | Replay from last checkpoint; validate idempotency ledger; resume or escalate |

### 2.2 Soul Sheet Loading

On transition from `IDLE` to `WARMING`:

1. Receive `soul_id` from Task envelope.
2. Query `<project_root>/souls/{soul_id}.yaml`; fall back to Memory Subsystem (PostgreSQL + flat files) if not found locally.
3. Validate YAML against JSON Schema; reject → `SESSION_FAILED` with `SOUL_VALIDATION_FAILED`.
4. Resolve inheritance chain (`inherits` field); merge parent → child.
5. Bind CLI overrides and environment variables per `cli.argument_binding` and `cli.environment_injection`.
6. Compute `prompt.assembly_hash` for cache key generation.
7. Register resolved `soul_id` and `prompt_version_hash` in session metadata for Observability.

### 2.3 Heartbeat Protocol

- **Interval:** Configurable via soul sheet `behavior.session.heartbeat_interval_seconds` (default: 5s).
- **Payload:** `agent_id`, `soul_id`, `current_state`, `task_id` (if active), `memory_usage_bytes`.
- **Timeout:** Pool Controller declares agent dead after `3 × heartbeat_interval` without ACK.
- **Transport:** Redis Pub/Sub channel `agents.heartbeat.{agent_id}` — sent directly from the Python process via `redis-py`.

### 2.4 Session Checkpointing (Full Dump)

On `PAUSED` or `CHECKPOINTED` transitions, the runtime writes a **full state dump** to:

1. **Redis** — hot copy of working memory (conversation turns, tool call results, current file buffers). TTL set to `2 × behavior.session.max_idle_minutes`.
2. **PostgreSQL** — durable checkpoint record with `agent_id`, `soul_id`, `prompt_version_hash`, `task_id`, `sequence_number`, and pointer to flat-file archive.
3. **Flat file** (`<data_root>/archive/{task_id}/{checkpoint_id}.json`) — full conversation log, reasoning traces, and memory context. The file path is stored in PostgreSQL `checkpoint_refs`.

> **Rationale for full dump:** Simplicity and safety. Incremental deltas can be introduced as an optimization when checkpoint size or write latency becomes a concern.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Python 3.12+** | LLM SDK ecosystem; agent logic iteration speed. No Go sidecar for pilot — asyncio + redis-py handles heartbeat publishing at single-machine scale. |
| **Soul loading** | **YAML 1.2** + **JSON Schema** (draft 2020-12) | Declarative, human-readable, validated at load time via `go-playground/validator` or Python `jsonschema`. |
| **Template engine** | **Mustache/Handlebars** (Python: `chevron`) | Deterministic, portable, aligns with `agent_configuration.md`. |
| **Session state** | **Redis** (hot) + **PostgreSQL** (durable checkpoint) + **flat files** (archive) | Redis for sub-ms working memory; PostgreSQL for checkpoint records; flat files for full conversation logs. |
| **Redis client** | **redis-py** (Python) | Official Redis Python client, async-native (asyncio), used for Pub/Sub heartbeats and checkpoint event publishing. |
| **LLM Gateway client** | **GatewayClient** (`rasa.llm_gateway.client`) | Internal abstraction over tier routing, Redis SHA-256 caching, seed bypass, and fallback chain. Communicates with LLM Cloud API. |

> **Sidecar note:** The Go sidecar (gRPC server, Redis subscriber offload) is dropped for the pilot. It can be reintroduced if profiling reveals Python GIL contention on Redis I/O or CPU-bound tool execution.

---

## 4. Deployment Topology

- **Process:** Native Python process, started via Procfile:
  ```
  agent-coder: python -m rasa.agent --soul souls/coder-v2-dev.yaml --mode daemon
  agent-reviewer: python -m rasa.agent --soul souls/reviewer-v1.yaml --mode daemon
  ```
- **Soul sheet path:** `<project_root>/souls/{soul_id}.yaml` — loaded from the local filesystem at session start.
- **Dependencies:** Local Redis, local PostgreSQL,  local LLM Gateway, local Ollama Cloud desktop app.
- **Startup order:** Redis → PostgreSQL → → LLM Gateway → Pool Controller → Agent Runtime.
- **Multiple agents:** Run multiple Procfile entries for different roles (coder, reviewer, planner). Each is a separate Python process with its own soul sheet.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Session start latency (p99) | Timed from IDLE → WARMING | > 2s — slow soul load or LLM Gateway warm-up |
| Heartbeat miss rate | Counted by Pool Controller | > 1% in 60s window — agent overloaded or hung |
| Soul sheet load failures | Logged at session start | > 0 in any window — check YAML syntax |
| Tool policy violations | Logged per denial | > 0 — investigate agent behavior or policy config |
| Checkpoint write latency (p99) | Timed per CHECKPOINTED transition | > 500ms — Redis or PostgreSQL contention |
| Checkpoint file size | Logged per dump | > 10 MB — review full-dump verbosity |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Full memory dump vs. incremental delta for checkpointing? | **Resolved:** Full dump for pilot simplicity. Incremental deltas as future optimization. |
| 2 | What is the heartbeat interval and timeout behavior under high GC pressure in Python? | **Open:** Needs load testing. Use `uvloop` as a zero-cost event loop optimization. |
| 3 | Should the Go sidecar be mandatory or opt-in per soul sheet? | **Resolved:** Dropped for pilot. Reintroduce if profiling shows GIL contention on Redis I/O. |
| 4 | How do we handle soul sheet hot-reload without dropping active sessions? | **Resolved:** Filesystem watcher. Agent drains current task, re-reads soul sheet, resumes in IDLE. Active task is not interrupted — new soul takes effect on next task assignment. |

---

## 7. Change Log


| 2026-05-13 | Post-gate hardening: full state machine (7 states), tool-calling loop in `_execute_task`, checkpoint serialization via `checkpoint.py`, `SoulLoader` with JSON Schema validation and inheritance, control channel listener, `reload_soul()` hot-reload. Daemon polling fixed (NOTIFY bug). Dispatcher patched for tool execution. | Codex |
| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: dropped Go sidecar, replaced S3 archive with flat files, replaced K8s Pod with native Python process, added full-dump checkpointing, added startup order and Procfile entries. | Codex |
| 2026-04-25 | Added soul sheet loading, CLI binding, and heartbeat details | ? |
| 2026-04-28 | Removed JetStream reference from Redis client rationale; all messaging via PG LISTEN/NOTIFY + Redis Pub/Sub. | Goldf |

---

*This document implements the agent process contract defined in `architectural_schema_v2.1.md` §2.1 and §3.2. Soul loading aligns with `agent_configuration.md` §2.2. Checkpoint format aligns with `recovery_controller.md` §2.2.*
