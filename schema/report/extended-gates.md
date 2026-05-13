# Extended Implementation Gates (Gate 6 – Gate 12)

> **Location:** `schema/report/extended-gates.md`  
> **Purpose:** Formalize the gates for completing the schema-specified architecture. These gates are derived from `schema-vs-implementation-report.md` and `implementation-plan.md`. They extend the existing Gate 0–5 series defined in `schema/implementation/top_level_decisions.md`.

---

## Current Status (Baseline)

| Gate | Name | Status | As Of |
|------|------|--------|-------|
| Gate 0 | Planning | Complete | 2026-04-25 |
| Gate 1 | Foundation | Complete | 2026-04-25 |
| Gate 2 | Core Services | Complete | 2026-04-25 |
| Gate 3 | Agent Lifecycle | Complete | 2026-04-28 |
| Gate 4 | Safety & Quality | Complete | 2026-04-29 |
| Gate 5 | Integration | Complete | 2026-04-29 |

---

## Proposed Gates

### Gate 6 — Foundation Hardening

| Criterion | Deliverable |
|-----------|-------------|
| Unified DB connection logic | No duplicated `_dsn()` helpers in `rasa/orchestrator/` |
| Soul sheet JSON Schema validation | `pytest tests/test_soul_validation.py -v` passes for all `.yaml` in `souls/` |
| Soul sheet inheritance resolution | Parent/child merge works; cycle detection rejects loops |
| Complete task envelope schema | `tasks` table has `required_role`, `tags`, `budget_tier`, `prompt_context` |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` still passes after all changes |

**Maps to:** Phase 0 of `implementation-plan.md`
**Estimated effort:** 2–3 days
**Risk:** Low

---

### Gate 7 — Agent Runtime Complete

| Criterion | Deliverable |
|-----------|-------------|
| Full state machine | All 7 states (`IDLE`, `WARMING`, `ACTIVE`, `PAUSED`, `RESUMING`, `CHECKPOINTED`, `RECOVERING`) are reachable |
| Prompt assembly hash | `prompt_version_hash` is computed and stable across identical inputs |
| Tool execution | Agent can execute `file_read`, `file_write`, `shell_exec`, `git_diff` end-to-end |
| Tool policy enforcement | Denied tools are blocked; allowed tools execute; results feed back to LLM context |
| Checkpoint save + restore | Kill an agent mid-task; restart it; verify it resumes and completes the task |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` passes |

**Maps to:** Phase 1 of `implementation-plan.md`
**Estimated effort:** 4–5 days
**Risk:** Medium (checkpointing is complex)

---

### Gate 8 — Control Plane Mature

| Criterion | Deliverable |
|-----------|-------------|
| Go orchestrator capability matching | `orchestrator.exe submit --soul coder-v2-dev --title "Test" --wait` returns the task result |
| Orchestrator retry logic | After 3 NACKs (5s interval), task escalates or re-queues |
| Task DAG cycle detection | Inserting A→B→C→A is rejected with `CYCLE_DETECTED` |
| Go pool controller agent registry | `rasa_pool.agents` and `rasa_pool.heartbeats` are updated in real time |
| Pool state machine | `UNDERLOADED`, `STEADY`, `BACKPRESSURE`, `DRAINING`, `STANDBY` are observable |
| Backpressure events | Submitting more tasks than agents triggers a `backpressure_events` row |
| Soul-aware routing | Tasks routed to idle agents with matching `soul_id`; no subprocess spawning per task |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` passes |

**Maps to:** Phase 2 of `implementation-plan.md`
**Estimated effort:** 4–5 days
**Risk:** Medium (Go binary maturity)

---

### Gate 9 — Safety & Recovery Live

| Criterion | Deliverable |
|-----------|-------------|
| Policy Engine HTTP endpoint | `POST /evaluate` accepts `{tool, args, soul_id}` and returns `{decision, reason}` |
| Real-time policy enforcement | An agent with `denied_tools: ["shell_exec:sudo"]` is blocked at execution time |
| Audit logging | Every allow/deny/review decision is written to `rasa_policy.audit_log` |
| Scanner rule overlays | `scanners/base-rules.yaml` + role overlays load correctly per `soul_id` |
| Orphan sandbox reaping | Directories in `data/sandbox/` older than 30 minutes are auto-deleted |
| Recovery Controller checkpoint replay | Kill an agent; Recovery Controller detects miss, finds checkpoint, resumes session within 5 seconds |
| Soul version mismatch handling | Minor version drift → forward-migrate; major drift → fail and re-queue |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` passes |

**Maps to:** Phase 3 of `implementation-plan.md`
**Estimated effort:** 3–4 days
**Risk:** Medium (cross-language integration)

---

### Gate 10 — Memory & Retrieval

| Criterion | Deliverable |
|-----------|-------------|
| Context assembly endpoint | `POST http://127.0.0.1:8300/assemble` returns `short_term_summary`, `graph_excerpt`, `semantic_matches` |
| pgvector HNSW index | `migrations/045_rasa_memory_vector_index.sql` creates index; search returns relevant chunks |
| Document chunking | 512-token chunks, 64-token overlap, file-boundary-aware |
| Semantic search performance | p99 latency < 100ms for 10K-embedding pilot dataset |
| Canonical model reconciler | Background gorifier detects new modules within 6 hours and updates `canonical_nodes` |
| Agent-driven canonical updates | Agent checkpoints write new facts to `canonical_nodes` |
| Session store eviction | Redis session keys have TTL = `2 × max_idle_minutes`; expired sessions promoted to PostgreSQL |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` passes |

**Maps to:** Phase 4 of `implementation-plan.md`
**Estimated effort:** 4–5 days
**Risk:** High (pgvector performance tuning)

---

### Gate 11 — Observability & Quality

| Criterion | Deliverable |
|-----------|-------------|
| Structured JSON logs | All stdout from Go and Python components is parseable by `jq '.event'` |
| Soul-aware trace schema | Every log line includes `component`, `soul.id`, `soul.role`, `soul.prompt_hash` where applicable |
| Replay bundles | Every completed task leaves `data/replays/{task_id}/` with all 8 files specified in `observability_stack.md` |
| Gzip archival | Replay bundles are compressed after 24 hours |
| Drift detection math | 20-task rolling window; flags pass-rate < 95%, p99 latency > 2× baseline, tokens > 1.5× baseline |
| Drift alerting | `drift_snapshots.flagged = true` triggers a PG NOTIFY on `drift_alert` |
| Benchmark regression suite | `python -m rasa.eval.benchmark --soul coder-v2-dev` runs and blocks regressed prompts |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` passes |

**Maps to:** Phase 5 of `implementation-plan.md`
**Estimated effort:** 3–4 days
**Risk:** Low

---

### Gate 12 — Bootstrap Automation

| Criterion | Deliverable |
|-----------|-------------|
| Bootstrap CLI module | `python -m rasa.bootstrap --repo /path/to/repo` runs end-to-end |
| AST extraction | `tree-sitter` parses Go, Python, TypeScript; produces dependency graph |
| Canonical model population | `canonical_nodes` and `canonical_edges` are auto-populated |
| Embedding pipeline | Files are chunked, embedded via `embedder.py`, and stored in `rasa_memory.embeddings` |
| Soul sheet ingestion | All `souls/*.yaml` are validated, resolved, and stored in `soul_sheets` table |
| Baseline freezing | `baselines` table has a snapshot JSONB row tagged `baseline_v1` |
| Coordination notify | `souls.loaded` PG NOTIFY emitted on completion |
| Self-test | Running bootstrap on the RASA repo itself succeeds without errors |
| End-to-end smoke test | `pytest tests/test_smoke.py -v` passes |

**Maps to:** Phase 6 of `implementation-plan.md`
**Estimated effort:** 3–4 days
**Risk:** Medium (tree-sitter dependency)

---

## Gate Dependency Graph

```
Gate 6 ──→ Gate 7 ──→ Gate 9
              │         │
              ↓         ↓
           Gate 8 ←───┘
              │
              ↓
           Gate 10 ──→ Gate 11 ──→ Gate 12
```

- **Gate 6** is independent and can start immediately.
- **Gate 7** depends on Gate 6 (soul validation must work before runtime hardening).
- **Gate 8** can be developed in parallel with Gate 7 (Go control plane vs. Python runtime).
- **Gate 9** depends on Gate 7 (recovery needs checkpoints) and Gate 8 (policy integration needs stable routing).
- **Gate 10** depends on Gate 7 (memory context assembly needs a mature runtime).
- **Gate 11** depends on Gate 7 (replay bundles need checkpoints) and Gate 10 (drift needs semantic retrieval data).
- **Gate 12** is independent but benefits from Gate 10 (pgvector readiness for embeddings).

---

## Summary

| Gate | Phase | Effort | Risk | Status |
|------|-------|--------|------|--------|
| Gate 6 | Foundation Hardening | 2–3 days | Low | Not Started |
| Gate 7 | Agent Runtime Complete | 4–5 days | Medium | Not Started |
| Gate 8 | Control Plane Mature | 4–5 days | Medium | Not Started |
| Gate 9 | Safety & Recovery Live | 3–4 days | Medium | Not Started |
| Gate 10 | Memory & Retrieval | 4–5 days | High | Not Started |
| Gate 11 | Observability & Quality | 3–4 days | Low | Not Started |
| Gate 12 | Bootstrap Automation | 3–4 days | Medium | Not Started |

**Total estimated effort:** ~23–30 days  
**Total risk profile:** Medium — the highest-risk items (pgvector performance, checkpointing) are spread across different phases so no single gate is a blocker for all others.

---

*These gates extend the series defined in `schema/implementation/top_level_decisions.md` §5. When a gate is completed, update both this document and `top_level_decisions.md` with the completion date and author.*
