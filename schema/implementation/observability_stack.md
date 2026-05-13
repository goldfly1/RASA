# Observability Stack

> **Architectural Reference:** `architectural_schema_v2.1.md` §7  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — soul_id and prompt_version_hash tagging   
> **Status:** Implemented — Gates 1–5 complete  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Captures reasoning traces, tool telemetry, diffs, and metrics. Tags every signal with `soul_id` and `prompt_version_hash` to enable per-personality debugging and prompt regression analysis.

The pilot uses a **two-tier observability approach**:
- **Database-backed metrics** — Every component writes durable rows to its PostgreSQL database. SQL views aggregate into queryable dashboards. `scripts/observe.py` provides a live terminal dashboard.
- **Structured JSON logs to stdout** — Supplementary. Each component writes JSON lines; captured by honcho/Procfile. Useful for ad-hoc debugging and tailing.

No OTel collector, no Prometheus, no Grafana in pilot. The DB schema and JSON log schema are designed so the upgrade path is a storage-backend swap, not a schema migration.

---

## 2. Database-Backed Metrics (Primary)

### 2.1 Metric Tables Per Database

Every component writes structured rows to its domain database. These are the **durable source of truth** for KPIs.

| Database | Table | Written By | Contents |
|----------|-------|------------|----------|
| `rasa_orch` | `tasks` | Orchestrator, Agent Runtime, Pool Controller | Full task lifecycle with timestamps (`created_at`, `assigned_at`, `started_at`, `completed_at`, `failed_at`, `retry_after`) |
| `rasa_orch` | `bus_messages` | All PG publishers | Message delivery audit trail |
| `rasa_pool` | `heartbeats` | Pool Controller | Every agent heartbeat with seq_num, payload, received_at |
| `rasa_pool` | `agents` | Pool Controller | Agent registration, state changes, last_heartbeat, disconnects |
| `rasa_pool` | `backpressure_events` | Pool Controller | Saturation events when no agent available for a soul |
| `rasa_eval` | `evaluation_records` | Eval Aggregator | Per-task scores, pass/fail, duration, benchmark metadata |
| `rasa_eval` | `drift_snapshots` | Eval Aggregator | Rolling window materialized every 60s (window_size, mean_score, std_score, flagged) |
| `rasa_recovery` | `recovery_log` | Recovery Controller | Every recovery action: agent death detected, task re-queued, checkpoint found |
| `rasa_recovery` | `idempotency_ledger` | Recovery Controller | ON CONFLICT UPSERT by key_hash — prevents duplicate recovery actions |
| `rasa_policy` | `audit_log` | Policy Engine | Every policy decision (allow/deny/review) with metadata |

### 2.2 SQL Views (Queryable Aggregates)

Created by `migrations/070_metrics_views.sql`:

| Database | View | Aggregation |
|----------|------|-------------|
| `rasa_orch` | `v_task_latency` | Queue/pickup/exec/total seconds per task (30d window) |
| `rasa_orch` | `v_daily_summary` | Tasks completed/failed/pending per day, avg latency (30d) |
| `rasa_eval` | `v_soul_performance` | Avg score, pass rate, avg duration, low-score count per soul (7d) |
| `rasa_eval` | `v_latest_drift` | Most recent drift snapshot per soul |
| `rasa_pool` | `v_agent_uptime` | Heartbeat count, span, liveness (ACTIVE/UNRESPONSIVE) per agent (24h) |
| `rasa_pool` | `v_recent_backpressure` | Saturation events grouped by reason (1h) |
| `rasa_policy` | `v_recent_decisions` | Decision counts per hour (24h) |
| `rasa_recovery` | `v_recent_recoveries` | Recovery action counts per hour (24h) |

### 2.3 Live Dashboard

`scripts/observe.py` queries the SQL views every 30 seconds and prints a human-readable dashboard to stdout:

```
RASA OBSERVABILITY DASHBOARD  |  2026-04-29 12:00:00 UTC
======================================================================
  TASKS (last 24h)
  COMPLETED       12  ############
  FAILED           1  #
  ...

  AGENTS
  agent-coder-v2-dev-a1b2c3d4   coder-v2-dev    REGISTERED    5s ago

  SOUL PERFORMANCE (7d)
  Soul                 Tasks     Avg    Pass%    AvgMs    Low
  coder-v2-dev            15   0.823   86.7%     2345      2

  Drift status:
  coder-v2-dev         n=20 mean=0.823 std=0.12 [OK]
  ...
======================================================================
```

No web UI — runs in a terminal alongside `honcho start`.

---

## 3. Structured JSON Logs (Supplementary)

### 3.1 Trace Schema (soul-aware)

Every component also writes a JSON line per significant event to stdout. The schema fields are identical to the enterprise OTel specification — only the transport changes:

```json
{
  "level": "info",
  "timestamp": "2026-04-25T12:00:00.000Z",
  "component": "agent_runtime",
  "event": "SESSION_START",
  "soul.id": "coder-v2-dev",
  "soul.role": "CODER",
  "soul.prompt_hash": "a3f2...",
  "soul.model_tier": "standard",
  "task.id": "0195f...",
  "agent.id": "agent-42",
  "message": "Agent session started"
}
```

**Event categories emitted by each component:**

| Component | Events |
|-----------|--------|
| Agent Runtime | `SESSION_START`, `SESSION_END`, `HEARTBEAT`, `TOOL_CALL`, `TOOL_RESULT`, `CHECKPOINT_SAVED` |
| LLM Gateway | `MODEL_REQUEST`, `CACHE_HIT`, `CACHE_MISS`, `FALLBACK_TRIGGERED`, `BUDGET_EXHAUSTED` |
| Orchestrator | `TASK_PENDING`, `TASK_ASSIGNED`, `TASK_COMPLETE`, `TASK_ESCALATED`, `RETRY_SCHEDULED` |
| Pool Controller | `AGENT_REGISTERED`, `HEARTBEAT_MISS`, `BACKPRESSURE_ACTIVE`, `DRAINING` |
| Policy Engine | `POLICY_ALLOW`, `POLICY_DENY`, `POLICY_REQUIRE_REVIEW` |
| Sandbox Pipeline | `SANDBOX_START`, `SCAN_PASS`, `BUILD_PASS`, `TEST_PASS`, `GATE_FAILURE`, `PROMOTED` |
| Recovery Controller | `RECOVERY_START`, `RECOVERY_SUCCESS`, `RECOVERY_FAILED`, `PROMPT_DRIFT` |
| Evaluation Engine | `BENCHMARK_START`, `BENCHMARK_COMPLETE`, `DRIFT_ALERT`, `PROMOTION_BLOCKED` |
| Memory Subsystem | `CONTEXT_ASSEMBLED`, `EMBEDDING_STORED`, `EVICTION_RUN`, `RECONCILER_DIFF` |

### 3.2 Replay Bundles

A replay bundle is an immutable artifact at `<data_root>/replays/{task_id}/` capturing a complete agent session:

```
replay-{task_id}-{timestamp}/
  soul_sheet.yaml          # Exact soul sheet used
  prompt_template.txt      # Assembled prompt before variable substitution
  prompt_final.txt         # Final prompt sent to LLM Gateway
  reasoning_trace.jsonl    # Streaming tool calls + LLM streamed completions
  memory_context.json      # Snapshot of Memory Subsystem output
  policy_decisions.json    # Policy Engine allow/deny log
  sandbox_results.json     # Scanner + test gate output
  metadata.json            # task_id, soul_id, prompt_version_hash, model_id, token_count
```

Replay bundles are referenced by `checkpoint_id` in PostgreSQL. They are compressed with gzip after 24 hours.

---

## 4. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Primary metrics** | **PostgreSQL tables + SQL views** | Durable, transactional, queryable with standard SQL. Already in the stack. |
| **Live dashboard** | **Python script** (`scripts/observe.py`) | Queries SQL views every 30s. Pure stdout — no web UI. |
| **Log format** | **JSON lines** (newline-delimited JSON) | Machine-parseable, human-readable, standard `jq`/`ConvertFrom-Json` compatibility. |
| **Log output** | **stdout** per component process | Zero infrastructure. Procfile captures output; can be redirected to a file or pipe. |
| **Log sink** | **Rotating file** (`logs/rasa-{component}-{date}.log`) | Simple file rotation. 7-day retention. |
| **Replay bundle storage** | **Flat files on disk** (`<data_root>/replays/`) | Same as archive storage. Referenced by checkpoint ID in PostgreSQL. |

> **Upgrade path:** Replace SQL views + observe.py with Grafana dashboards powered by OpenTelemetry + Prometheus/Loki/Tempo. The DB table schemas and JSON log schemas stay the same — only the query/visualization layer changes.

---

## 5. Deployment Topology

- **Metrics storage:** PostgreSQL tables across 6 databases. Backfilled in real-time by each component.
- **Dashboard:** `scripts/observe.py` in Procfile: `logs: python scripts/observe.py --interval 30`
- **Log directory:** `<project_root>/logs/{component}-{date}.log` — one file per component per day.
- **Replay bundle directory:** `<project_root>/data/replays/{task_id>/` — written by Agent Runtime on CHECKPOINTED.
- **No external services:** No OTel collector, no Prometheus, no Grafana. Everything lives on local PostgreSQL + filesystem.

---

## 6. Operational Concerns

| Metric | Pilot Action | Concern |
|--------|--------------|---------|
| DB metrics storage | Accumulates indefinitely in pilot | At pilot scale (~10 tasks/day), years of data fit in MB. Add TTL migration later. |
| Log disk usage | 7-day rolling rotation | `~10 MB/day per component` at pilot scale — negligible for 250 GB free |
| Replay bundle disk usage | Per task, cleaned up manually | `~1-5 MB per task` — monitor total in `<data_root>/replays/` |
| Dashboard overhead | Queries 5 databases every 30s | Negligible load at pilot scale |

---

## 7. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How long are raw reasoning traces retained? | **Resolved:** 7 days on disk. |
| 2 | What is the aggregation window for KPI rollups? | **Resolved:** Real-time via SQL views queried every 30s by observe.py. |
| 3 | Should replay bundles include the full LLM response stream or just the final completion? | **Resolved:** Full streamed response. |
| 4 | When to add TTL-based cleanup for metric tables? | **Deferred** — not needed at pilot scale. |

---

## 8. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-29 | Gate 5: Replaced stdout-only approach with dual-tier observability. Added database-backed metrics (10 tables across 6 databases), SQL views (8 views for latency/performance/drift/uptime), live observe.py terminal dashboard. JSON stdout logs retained as supplementary channel. | Goldf |
| 2026-04-25 | Pilot provisioning: replaced OTel/Prometheus/Grafana stack with structured JSON logs to stdout, replaced S3 replay bundle storage with local flat files, added CLI query patterns for pilot dashboards, added per-component event catalog. | Codex |
| 2026-04-25 | Added soul-aware trace schema, replay bundle structure, and dashboard organization | ? |

---

*This document implements the observability contract defined in `architectural_schema_v2.1.md` §7. Trace schema aligns with `agent_configuration.md` §2.2 soul metadata fields.*
