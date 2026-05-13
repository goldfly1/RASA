# Pool Controller

> **Architectural Reference:** `architectural_schema_v2.1.md` §12  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — warm pool pre-warming with soul sheets   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Sits between the Orchestrator and the agent processes. Monitors agent heartbeats, routes tasks to available agents by matching `soul_id`, enforces concurrency ceilings, and watches for soul sheet changes. In the pilot, the agent pool is statically defined — the Pool Controller manages **awareness and routing**, not dynamic scaling.

---

## 2. Internal Design

### 2.1 Internal State Machine

`UNDERLOADED → STEADY → BACKPRESSURE → DRAINING → STANDBY`

| State | Trigger | Pilot Action |
|-------|---------|--------------|
| **UNDERLOADED** | All agents idle, no tasks queued | No action — agents are pre-started in IDLE via Procfile. Report idle state to Orchestrator. |
| **STEADY** | Active agents within capacity | Route tasks to available agents by `soul_id` match. Monitor heartbeats. |
| **BACKPRESSURE** | All agents busy or budget > 80% | Reject new tasks; emit alert. No K8s HPA to trigger — operator must add another agent process manually or wait for an agent to free up. |
| **DRAINING** | Shutdown signal received | Send drain event to all agents; wait for active tasks to checkpoint; allow agents to exit gracefully. |
| **STANDBY** | All agents exited | Ready for operator restart via Procfile. |

> **Upgrade path:** In a dynamic environment, BACKPRESSURE triggers K8s HPA scale-out, and DRAINING coordinates rolling updates. The state machine interface stays the same; only the actions change.

### 2.2 Agent Registry

The Pool Controller maintains an in-memory registry backed by Redis and PostgreSQL:

| Field | Source | Description |
|-------|--------|-------------|
| `agent_id` | Agent Runtime (auto-generated) | Unique ID per process |
| `soul_id` | Soul sheet | Which personality this agent runs |
| `current_state` | Heartbeat | IDLE, WARMING, ACTIVE, PAUSED, CHECKPOINTED |
| `task_id` | Heartbeat | Currently assigned task (null if idle) |
| `last_heartbeat` | Heartbeat timestamp | Used for timeout detection |
| `memory_usage_bytes` | Heartbeat | Health signal |

**Heartbeat timeout:** Agent declared dead after `3 × behavior.session.heartbeat_interval_seconds` without ACK. Timed-out agents are removed from the registry; the Orchestrator is notified to reassign the task.

### 2.3 Warm Pool (Static)

For the pilot, the warm pool is a fixed set of Procfile entries:

```
# Procfile
agent-coder: python -m rasa.agent --soul souls/coder-v2-dev.yaml --mode daemon
agent-coder-2: python -m rasa.agent --soul souls/coder-v2-dev.yaml --mode daemon
agent-reviewer: python -m rasa.agent --soul souls/reviewer-v1.yaml --mode daemon
agent-planner: python -m rasa.agent --soul souls/planner-v1.yaml --mode daemon
```

The Pool Controller discovers agents as they start and register via heartbeat. No scaling orchestration needed.

**Soul Distribution Map:** Defined in `config/pool.yaml`:

```yaml
pool:
  souls:
    coder-v2-dev:
      count: 2           # Two coder agents
      max_concurrent: 2
    reviewer-v1:
      count: 1
      max_concurrent: 1
    planner-v1:
      count: 1
      max_concurrent: 1
```

### 2.4 Soul Sheet Change Detection

The Pool Controller watches for soul sheet changes via two paths:

1. **Filesystem watcher** — Detects changes to `<project_root>/souls/*.yaml`. On change, publishes a `souls.reload` via PostgreSQL LISTEN/NOTIFY for interested agents.
2. **`souls.loaded` PostgreSQL LISTEN/NOTIFY notification** — Received from the Bootstrap & Ingestion process on initial startup. Triggers registry warm-up.

On soul change:
- The new soul version is stored in PostgreSQL.
- Running agents are **not** interrupted — the change applies to their *next* task assignment.
- The Pool Controller updates its capability index (in-memory, flushed from PostgreSQL).

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Go 1.24+** | PostgreSQL subscriber, heartbeat timeout loop, Redis client — goroutines/channels map cleanly to concurrent agent monitoring. |
| **Agent registry** | **Redis** (hot, TTL-backed) + **PostgreSQL** (durable) | Redis for heartbeat lookups (< 1ms); PostgreSQL for restart recovery. |
| **Task routing transport** | **PostgreSQL LISTEN/NOTIFY** (channel `tasks_assigned`) | Orchestrator publishes; Pool Controller consumes and matches agents. |
| **Heartbeat transport** | **Redis Pub/Sub** (channel `agents.heartbeat.{agent_id}`) | Each agent publishes to its own topic; Pool Controller fans in via wildcard subscription. |
| **Soul distribution config** | **YAML** (`config/pool.yaml`) | Human-editable static config. Can be replaced by a dynamic scheduler in an upgrade. |

---

## 4. Deployment Topology

- **Process:** Native Go binary, started via Procfile before agent processes:
  ```
  pool-controller: pool-controller --config config/pool.yaml --db postgres://localhost/rasa_pool
  ```
- **Startup order:** Redis → PostgreSQL → → LLM Gateway → **Pool Controller** → Agent Runtime.
- **Dependencies:** Local Redis, local PostgreSQL, .
- **Agent discovery:** Agents register automatically via their first heartbeat. No static agent list needed — the Pool Controller builds the registry dynamically.
- **Concurrency ceiling:** Enforced by `max_concurrent` in `config/pool.yaml`. If all agents of a given `soul_id` are busy, the Pool Controller NACKs the `tasks_assigned` message, and the Orchestrator queues or escalates.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Heartbeat miss rate | Counted per agent | > 1% in 60s — agent may be hung |
| Task routing latency | Timed from task.assigned → agent match | > 100ms p99 — registry lookup contention |
| BACKPRESSURE duration | Counted per event | > 5 min — operator should add another agent process |
| Soul distribution drift | Diff between configured vs. available agents | Any mismatch — check Procfile entries |
| Dead agent count | Counted per timeout | > 0 — investigate agent crash |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How is the warm pool pre-warmed? | **Resolved:** Static Procfile entries for pilot. Agents start in IDLE and register via heartbeat. |
| 2 | What is the scale-down delay to avoid thrashing? | **Resolved (N/A):** No dynamic scaling in pilot. Graceful shutdown on DRAINING respects `graceful_shutdown_seconds`. |
| 3 | Should the Soul Distribution Map be reactive or predictive? | **Resolved:** Static YAML config for pilot. Predictive allocation is an upgrade. |
| 4 | How does the Pool Controller handle a version mismatch between a heartbeat's `soul_id` and the current soul sheet? | **Open:** Recommend rejecting the heartbeat and logging the mismatch. Agent must be restarted with the correct soul version. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: replaced K8s Deployment / HPA scaling with static Procfile pool, replaced rolling-update pre-warming with filesystem watcher + reload notification, added pool config YAML, simplified state machine for static pool, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul-aware warm pool, pre-warming, and scale-down logic | ? |

---

*This document implements the pool management contract defined in `architectural_schema_v2.1.md` §12. Soul distribution aligns with `agent_configuration.md` §2.2. Heartbeat protocol aligns with `agent_runtime.md` §2.3.*

