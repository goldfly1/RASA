# Evaluation Engine

> **Architectural Reference:** `architectural_schema_v2.1.md` §13  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — prompt regression benchmarking, soul sheet promotion gates   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Consumes Task gate results and ReasoningTrace records to compute KPIs, benchmark scores, and drift signals. Feeds back into model selection and prompt governance. Evaluates prompt version changes before soul sheet promotion.

---

## 2. Internal Design

### 2.1 Evaluation Record (soul-aware)

| Field | Type | Source |
|-------|------|--------|
| `eval_id` | UUID | Generated |
| `soul_id` | string | Agent Runtime session metadata |
| `prompt_version_hash` | string | SHA-256 of final assembled prompt |
| `soul_version` | string | Semantic version of the soul sheet |
| `agent_role` | enum | `agent_role` from soul sheet |
| `model_id` | string | LLM Gateway routing decision |
| `gate_results` | JSON | Sandbox Pipeline output |
| `score` | float [0–1] | Human review sampling or automated heuristic |
| `cycle_time_ms` | int | Wall time from `ASSIGNED` to `COMPLETE` |
| `tokens_consumed` | int | Budget consumed |
| `cache_hit` | bool | LLM Gateway cache status |

### 2.2 Prompt Regression Benchmark

Before a soul sheet is promoted (see [`agent_configuration.md`](agent_configuration.md) §5.3), the Evaluation Engine runs a **Prompt Regression Benchmark**:

1. Load the candidate soul sheet and its parent (if `inherits` is set).
2. Run a fixed set of benchmark tasks (stored in `<project_root>/benchmarks/`) against both versions.
3. Compare `score`, `cycle_time_ms`, and `tokens_consumed` across the task set.
4. If any metric regresses > 5% from baseline, block promotion and alert.

Benchmark tasks are versioned independently and cover:
- **Syntax tasks:** Simple refactor, type annotation
- **Semantic tasks:** Cross-file dependency resolution
- **Security tasks:** Secret detection, protected path access

### 2.3 Drift Detection

The Drift Detector monitors rolling windows of `EvaluationRecord`s grouped by `soul_id` and `prompt_version_hash`:

| Signal | Threshold | Window | Action |
|--------|-----------|--------|--------|
| Pass-rate drop | < 95% | 20 tasks | Alert + flag `soul_id` for review |
| Latency spike (p99) | > 2× baseline | 20 tasks | Alert + consider model tier degradation |
| Token consumption spike | > 1.5× baseline | 20 tasks | Alert + flag prompt verbosity regression |
| Score divergence (human vs. auto) | > 0.2 delta | 20 tasks | Increase human sampling rate |

### 2.4 Feedback Loop

Evaluation results feed back into:

- **Orchestrator:** Adjust capability scoring for `soul_id` (lower score if pass-rate drops).
- **Pool Controller:** Log under-performing `soul_id` for operator review. (No automatic scaling action — static pool.)
- **LLM Gateway:** Deprioritize model assignments for souls with high failure rates.
- **Agent Configuration:** Block soul sheet updates until benchmark passes.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Aggregator language** | **Go 1.24+** | PostgreSQL LISTEN/NOTIFY subscriber for eval.record channel; periodic aggregation window timers. |
| **Scorer language** | **Python 3.12+** | Benchmark task execution and scoring logic. |
| **Evaluation store** | **PostgreSQL** (`evaluation_records` table) | Co-located with primary durable store. JSONB for flexible `gate_results`. |
| **Benchmark storage** | **Local directory** (`<project_root>/benchmarks/`) | Versioned task definitions (JSON). Can be migrated to Git for collaborative editing. |
| **Drift detection** | **In-memory rolling window** (Go) | 20-task window kept in Redis for fast queries; flushed to PostgreSQL for persistence. |

---

## 4. Deployment Topology

- **Process:** Go binary (aggregator + drift detector) + Python process (scorer, benchmark runner). Both via Procfile:
  ```
  eval-aggregator: evaluation-engine --mode aggregator --db postgres://localhost/rasa_eval 
  eval-scorer: evaluation-engine --mode scorer --benchmarks benchmarks/
  ```
- **Dependencies:** Local PostgreSQL, , local Redis (for drift window).
- **Startup:** Runs alongside the Orchestrator and Sandbox Pipeline. Consumes `sandbox_result` and `eval_record` PostgreSQL channels.
- **Benchmark execution:** The scorer runs benchmark tasks by submitting them through the Orchestrator via the standard task pipeline — no special path.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Benchmark duration | Timed per run | > 5 min — review task complexity |
| Drift detection latency | Time from task completion to alert | > 30s — aggregation window too large |
| Evaluation record write rate | Per task completion | > 100/hr at pilot scale — normal |
| Benchmark regression count | Per soul sheet promotion | > 0 — block promotion |
| Human score sampling rate | % of tasks manually scored | > 10% — consider automated heuristic |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How large is the evaluation window for drift detection? | **Resolved:** 20 tasks for pilot. Can be tuned upward as task volume grows. |
| 2 | Where are benchmark tasks stored and versioned? | **Resolved:** `<project_root>/benchmarks/` — local JSON files. Git-tracked for version history. |
| 3 | Should benchmark tasks include adversarial examples (prompt injection, jailbreak attempts)? | **Resolved:** Out of scope for pilot. Add as a security benchmark upgrade. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: lowered drift window to 20 tasks, changed benchmark storage to local directory, removed Pool Controller scaling feedback (static pool), filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added prompt regression benchmark, soul-aware EvaluationRecord, drift signals, and feedback loop | ? |

---

*This document implements the evaluation contract defined in `architectural_schema_v2.1.md` §13. EvaluationRecord schema aligns with `orchestrator.md` §2.2 task lifecycle.*

