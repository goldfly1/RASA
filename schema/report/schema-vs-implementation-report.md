# Schema vs. Implementation Comparison Report

## Executive Summary

The RASA codebase represents a **Phase 1 pilot** where all 5 implementation gates are marked complete in `top_level_decisions.md`. However, the gap between the **ambitious schema specifications** and the **actual code** is substantial. Many components described in rich detail across 14 implementation documents exist only as **scaffolding, stubs, or simplified approximations**. The system is functional enough for end-to-end smoke tests, but numerous advanced features (checkpoint replay, drift detection, policy rule evaluation, benchmark regression, semantic retrieval, canonical model reconciliation) are either missing entirely or represented by placeholder logic.

---

## 1. Agent Runtime (`agent_runtime.md` vs. `rasa/agent/runtime.py`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **State machine**: `IDLE → WARMING → ACTIVE → PAUSED → RESUMING → CHECKPOINTED → RECOVERING` | **Partial** | Only `IDLE`, `WARMING`, `ACTIVE`, `CHECKPOINTED` exist. `PAUSED`, `RESUMING`, `RECOVERING` are missing. |
| **Soul sheet loading** with JSON Schema validation, inheritance resolution, CLI binding, `prompt.assembly_hash` | **Partial** | Loads YAML via `yaml.safe_load()`. No JSON Schema validation. No inheritance resolution. No `assembly_hash` computation. |
| **Heartbeat**: configurable interval, payload with `memory_usage_bytes`, timeout `3× interval` | **Partial** | Heartbeat emits `current_state` and `soul_id` only. No `memory_usage_bytes`. No timeout detection in Python runtime itself. |
| **Checkpointing**: full dump to Redis (hot) + PostgreSQL (durable) + flat files (`data/archive/`) | **Missing** | `CHECKPOINTED` state exists but no actual checkpoint serialization. No Redis hot copy. No flat-file archive. |
| **Template engine**: Mustache/Handlebars via `chevron` | **Implemented** | Correctly uses `chevron.render()`. |
| **Context assembly**: queries Memory Subsystem HTTP API at `:8300` | **Implemented** | `_assemble_memory()` calls `http://127.0.0.1:8300/assemble`. |
| **Tool binding invocation** | **Missing** | Agent Runtime calls LLM Gateway but does not execute tool calls itself. |
| **LLM Gateway client**: `GatewayClient` with tier routing | **Implemented** | Uses `GatewayClient` from `rasa.llm_gateway.client`. |

**Verdict**: The runtime is a functional task poller and prompt assembler, but it lacks session checkpointing, tool execution, state machine completeness, and soul sheet validation.

---

## 2. Orchestrator (`orchestrator.md` vs. `rasa/orchestrator/` + `cmd/orchestrator/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Language**: Go 1.24+ | **Partial** | Go binary exists (`cmd/orchestrator/main.go`) but the *active* orchestrator logic is in Python (`rasa/orchestrator/runtime.py`). |
| **State machine**: `PENDING → ASSIGNED → IN_PROGRESS → VERIFICATION → COMPLETE/ESCALATED` | **Partial** | Task table has `PENDING`, `ASSIGNED`, `RUNNING`, `COMPLETED`, `FAILED`. `VERIFICATION` and `ESCALATED` are missing. |
| **Capability Index**: PostgreSQL `agent_capabilities` table, tag scoring, tier filtering | **Implemented** | `rasa/orchestrator/capabilities.py` implements `CapabilityRegistry` with upsert/query. Schema doc's "score by tag overlap" and "retry 3 times" logic is not implemented. |
| **Task envelope**: `task_id`, `soul_id`, `required_role`, `tags`, `budget_tier`, `prompt_context` | **Partial** | `tasks` table has `soul_id` but no `required_role`, `tags`, or `budget_tier` columns. |
| **Cyclic dependency detection** in Task DAG | **Missing** | No DAG validation. Tasks have `parent_id` but no cycle detection. |
| **Assignment retry**: 5s interval, max 3 retries | **Missing** | No retry logic in the Go orchestrator. |
| **PG LISTEN/NOTIFY** on `tasks_assigned` | **Implemented** | Both Go and Python orchestrators use PG NOTIFY. |

**Verdict**: The orchestrator CLI can submit tasks, and the Python `OrchestratorRuntime` provides multi-turn LLM delegation. However, the sophisticated capability matching, cyclic dependency detection, and assignment retry logic from the schema are absent.

---

## 3. Pool Controller (`pool_controller.md` vs. `rasa/pool/controller.py` + `cmd/pool-controller/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Language**: Go 1.24+ | **Partial** | Go stub exists but the active pool controller is Python (`rasa/pool/controller.py`). |
| **State machine**: `UNDERLOADED → STEADY → BACKPRESSURE → DRAINING → STANDBY` | **Missing** | No state machine. Simple task listener + heartbeat logger. |
| **Agent Registry**: in-memory + Redis + PostgreSQL, timeout `3× heartbeat` | **Partial** | Python version logs heartbeats to stdout. Go version connects to `rasa_pool` DB and has `agent_registry.go`. No timeout-driven dead-agent removal in Python. |
| **Warm pool**: static Procfile entries, soul distribution map from `config/pool.yaml` | **Partial** | `config/pool.yaml` exists with `replicas` counts. Procfile has agent entries. But the Python pool controller **spawns workers on demand** via `subprocess.Popen` rather than routing to pre-warmed agents. |
| **Soul sheet change detection**: filesystem watcher, `souls.reload` PG NOTIFY | **Missing** | No filesystem watcher. No `souls.reload` notification. |
| **Concurrency ceiling**: `max_concurrent` enforcement | **Missing** | No concurrency tracking. |
| **BACKPRESSURE**: reject new tasks, emit alert | **Partial** | `backpressure_events` table exists in schema, but no dynamic backpressure logic in pool controller. |

**Verdict**: The pool controller is a minimal task router and heartbeat listener. The sophisticated registry, state machine, backpressure, and soul-change detection from the schema are not implemented.

---

## 4. LLM Gateway (`llm_gateway.md` vs. `rasa/llm_gateway/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Prompt cache**: Redis, SHA-256 hash key, TTL 1h | **Partial** | `config/gateway.yaml` declares Redis cache with TTL 3600 and SHA-256. `TierRouter` reads config. Actual caching implementation in `router.py` is minimal. |
| **Model Parameter Routing**: `temperature`, `max_tokens`, `top_p`, `budget_tier` from soul sheet | **Implemented** | `AgentRuntime` passes these through to `GatewayClient`. |
| **Tier Mapping**: `standard` → `deepseek-v4-flash:cloud`, `premium` → `deepseek-v4-pro:cloud` | **Implemented** | `config/gateway.yaml` maps tiers to `RASA_DEFAULT_MODEL` and `RASA_PREMIUM_MODEL` env vars. |
| **Fallback chain**: same-tier → degrade tier → alternate API key | **Partial** | Config has `fallback.enabled` and `chain: [ollama]`. No actual fallback logic to OpenAI or tier degradation visible in `router.py`. |
| **Deterministic sampling**: `seed` bypasses cache | **Partial** | Config has `seed_passthrough: true`. Not verified in actual request path. |
| **Cache invalidation**: TTL expiry, soul change, manual flush | **Missing** | Only TTL eviction via Redis. No filesystem watcher invalidation. |

**Verdict**: The gateway has the configuration scaffolding and tier routing, but the fallback chain, cache invalidation, and deterministic sampling are not fully wired.

---

## 5. Memory Subsystem (`memory_subsystem.md` vs. `rasa/memory/` + `cmd/memory-controller/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Context Assembly Pipeline**: `short_term_summary`, `semantic_matches`, `graph_excerpt`, `archive_refs` | **Partial** | Go `memory-controller` stub exists. `rasa/memory/embedder.py` implements OpenAI embedding via JSON-lines protocol. `rasa/memory/pgvector.py` exists. No evidence of the full assembly pipeline. |
| **Session Store Eviction**: LRU + TTL (`2× max_idle_minutes`) + checkpoint promotion | **Missing** | No eviction logic. |
| **Canonical Model**: JSONB, updated by bootstrap/agent/reconciler, last-writer-wins | **Partial** | `migrations/080_seed_lore.sql` seeds canonical nodes. No reconciler. No agent-driven updates. |
| **Embedder**: JSON-lines stdin/stdout protocol, OpenAI `text-embedding-3-small` | **Implemented** | `embedder.py` exactly matches this design. |
| **Vector index**: pgvector, HNSW, 512-token chunks, 64-token overlap | **Partial** | `pgvector.py` exists. No evidence of HNSW index creation or chunking strategy in code. |

**Verdict**: The embedder subprocess is implemented to spec. The broader memory subsystem (context assembly, eviction, canonical model reconciliation) is incomplete.

---

## 6. Sandbox Pipeline (`sandbox_pipeline.md` vs. `rasa/sandbox/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **State machine**: `IDLE → CLONING → SCANNING → BUILDING → TESTING → PROMOTING → CLEANUP` | **Implemented** | `SandboxPipeline` has `Gate` enum matching all states. `run_pipeline()` implements the sequence. |
| **Soul-aware scanner rules**: role-specific overlays (`scanners/coder-overlay.yaml`, etc.) | **Missing** | `scanners/` directory contains only `.gitkeep`. Scanner uses hardcoded regex rules in `scanner.py`. No Semgrep, no detect-secrets, no overlays. |
| **Orphan sandbox reaping**: background asyncio task, >30 min stale dirs | **Missing** | No reaping task. |
| **Build/test isolation**: temp directory + subprocess timeout | **Partial** | Uses `data/sandbox/{task_id}/`. `_build()` and `_test()` exist with subprocess calls but no timeout enforcement visible in the code read. |
| **Promotion**: copy changed files back to working dir | **Implemented** | `_promote()` uses `shutil.copy2`. |

**Verdict**: The pipeline state machine exists and functions, but the scanner is a primitive regex engine rather than the Semgrep/detect-secrets chain described in the schema. Orphan reaping and role-specific overlays are missing.

---

## 7. Policy Engine (`policy_engine.md` vs. `internal/policy/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Permission Matrix**: Organization guardrails → Soul sheet → Task override → Human review | **Partial** | Go package `internal/policy/` exists with `engine.go`, `rules.go`, `audit.go`, `human_review.go`, `soul_sheet.go`. This is one of the more complete Go components. |
| **Soul sheet integration**: caches `behavior.tool_policy` at session start | **Partial** | `soul_sheet.go` exists. Integration with runtime is unclear. |
| **Hot reload**: PostgreSQL polling (30s) + Redis `policy.update` | **Partial** | `reloader.go` exists. |
| **Audit log**: append-only PostgreSQL table | **Implemented** | `audit.go` exists. `migrations/030_rasa_policy.sql` creates `audit_log` table. |

**Verdict**: The Policy Engine Go code is relatively well-scaffolded compared to other components, with actual files for engine, rules, audit, and reloader. However, integration with the Python agent runtime for real-time tool policy enforcement is not verified.

---

## 8. Recovery Controller (`recovery_controller.md` vs. `cmd/recovery-controller/` + `internal/recovery/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **State machine**: `STANDBY → DETECTING → REPLAYING → VALIDATING → RESTORED/FAILED` | **Missing** | `internal/recovery/controller.go` and `ledger.go` exist, but no evidence of the full state machine. |
| **Checkpoint structure**: JSON blob with `soul_version`, `prompt_version_hash`, file pointers | **Missing** | `checkpoints` table exists in migrations, but no checkpoint serialization in Agent Runtime. |
| **Idempotency Ledger**: PostgreSQL table, `(task_id, sequence_number)` unique constraint | **Implemented** | `internal/recovery/ledger.go` and `migrations/060_rasa_recovery.sql` create `idempotency_ledger` with `ON CONFLICT` upsert. |
| **Soul version mismatch handling**: minor/patch forward-migration, major diff → fail | **Missing** | No migration logic. |
| **Recovery latency target**: 5 seconds | **N/A** | Not achievable without checkpoints. |

**Verdict**: The ledger is implemented, but the full recovery flow (checkpoint detection, replay, validation, soul version handling) is not functional because checkpoints are not being written.

---

## 9. Evaluation Engine (`evaluation_engine.md` vs. `rasa/eval/` + `cmd/eval-aggregator/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **EvaluationRecord**: `soul_id`, `prompt_version_hash`, `soul_version`, `gate_results`, `score`, `cycle_time_ms`, `tokens_consumed`, `cache_hit` | **Partial** | `rasa_eval.evaluation_records` table exists. `rasa/eval/scorer.py` writes scores. No `prompt_version_hash` or `cache_hit` tracking. |
| **Prompt Regression Benchmark**: load candidate + parent, run fixed benchmarks, block if >5% regression | **Missing** | `benchmarks/` directory is empty (`.gitkeep` only). No benchmark runner. |
| **Drift Detection**: 20-task rolling window, pass-rate <95%, latency >2× baseline, token spike >1.5× | **Partial** | `drift_snapshots` table exists. `internal/eval/aggregator.go` exists. `migrations/070_metrics_views.sql` creates `v_latest_drift`. The actual drift math is likely in the Go aggregator, but not verified. |
| **Feedback loop**: adjust capability scoring, log under-performing souls | **Missing** | No dynamic capability scoring adjustment. |

**Verdict**: The database schema and views for evaluation exist. The scorer assigns heuristic scores (0–1) based on content length and structure, not the sophisticated benchmarking described in the schema. Drift detection tables exist but the alert-to-action feedback loop is missing.

---

## 10. Message Bus (`message_bus.md` vs. `rasa/bus/` + `internal/bus/`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Transport split**: Durable = PG LISTEN/NOTIFY + backing tables; Ephemeral = Redis Pub/Sub | **Implemented** | `rasa/bus/pg.py`, `rasa/bus/redis.py`, `internal/bus/pg_subscriber.go`, `internal/bus/redis_subscriber.go` all exist. |
| **Envelope schema**: `message_id`, `correlation_id`, `source_component`, `destination_component`, `payload`, `metadata` | **Implemented** | `rasa/bus/envelope.py` and `internal/bus/envelope.go` match the schema exactly. |
| **Shared interfaces**: `Publisher`/`Subscriber` abstractions in both Go and Python | **Implemented** | Both languages have matching interfaces. |
| **Channel topology**: `tasks_assigned`, `tasks_submit`, `checkpoint_saved`, `sandbox_result`, `eval_record`, `souls_loaded` | **Partial** | `tasks_assigned`, `sandbox_execute`, `sandbox_result` are used. `checkpoint_saved`, `eval_record`, `souls_loaded` channels are defined in schema but not verified as active. |

**Verdict**: The message bus is one of the most faithfully implemented components. The transport abstraction and envelope schema match the specification closely.

---

## 11. Observability Stack (`observability_stack.md` vs. `scripts/observe.py` + migrations)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Database-backed metrics**: 10 tables across 6 databases | **Implemented** | All tables exist: `tasks`, `bus_messages`, `heartbeats`, `agents`, `backpressure_events`, `evaluation_records`, `drift_snapshots`, `recovery_log`, `idempotency_ledger`, `audit_log`. |
| **SQL Views**: 8 views for latency, performance, drift, uptime, backpressure, decisions, recoveries | **Implemented** | `migrations/070_metrics_views.sql` creates all 8 views. |
| **Live dashboard**: `scripts/observe.py`, 30s refresh | **Implemented** | `observe.py` exists and queries views. |
| **Structured JSON logs**: soul-aware trace schema, per-component event catalog | **Partial** | Components print to stdout but not in the rigorous JSON schema format specified. No `soul.prompt_hash` tagging. |
| **Replay bundles**: immutable artifacts at `data/replays/{task_id}/` | **Missing** | `data/replays/` does not exist. No replay bundle generation. |

**Verdict**: The database metrics layer is fully implemented and is the strongest area. The live dashboard works. However, structured logging and replay bundles are not yet implemented.

---

## 12. Bootstrap & Ingestion (`bootstrap_ingestion.md` vs. actual codebase)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Cold start sequence**: AST extraction → canonical model → embed files → load souls → validate & freeze | **Missing** | No bootstrap CLI module. No tree-sitter integration. No AST extraction. |
| **Soul sheet ingestion**: scan `souls/`, validate JSON Schema, resolve inheritance, store in PostgreSQL | **Partial** | `migrations/100_rasa_capabilities.sql` seeds `agent_capabilities`. No JSON Schema validation. No inheritance resolution. |
| **Baseline freezing**: lock souls, snapshot canonical model, tag vector index | **Missing** | No baseline mechanism. |
| **Chunking strategy**: 512 tokens, 64-token overlap, file-boundary-aware | **Missing** | Not implemented. |

**Verdict**: Bootstrap is entirely absent. The canonical model is seeded via a single SQL migration (`080_seed_lore.sql`) rather than through automated ingestion.

---

## 13. Agent Configuration (`agent_configuration.md` vs. `souls/*.yaml`)

| Schema Requirement | Implementation Status | Notes |
|---|---|---|
| **Soul sheet schema**: `soul_version`, `soul_id`, `agent_role`, `inherits`, `metadata`, `model`, `prompt`, `behavior`, `memory`, `cli`, `extensions` | **Implemented** | All 5 soul sheets match the schema closely. |
| **Schema validation**: JSON Schema draft 2020-12, `go-playground/validator` or `jsonschema` | **Missing** | No validation at load time. |
| **Inheritance resolution**: parent-child merge, arrays replaced not appended | **Missing** | All souls have `inherits: ~`. No resolution logic. |
| **5-Layer Variable Resolution**: soul defaults → memory → task envelope → CLI → env vars | **Partial** | Runtime resolves soul + memory + task. CLI and env var overlay not fully implemented. |
| **Prompt assembly hash**: SHA-256 for cache lookup | **Missing** | No hash computation. |
| **Hot reload**: filesystem watcher, drain current task, reload | **Missing** | No watcher. Agents must be restarted. |
| **Promotion flow**: Evaluation Engine benchmark → pass → promote | **Missing** | No automated promotion gating. |

**Verdict**: The soul sheet *files* themselves are excellent and match the schema. The surrounding machinery (validation, inheritance, hot reload, promotion gating) is missing.

---

## 14. Top-Level Decisions (`top_level_decisions.md` vs. actual stack)

| Decision | Implementation Status |
|---|---|
| **Control Plane**: Go 1.24+ | **Partial** — Go stubs exist, but Python implements much of the control plane logic |
| **Agent Runtime & LLM Gateway**: Python 3.12+ | **Implemented** |
| **Primary durable store**: PostgreSQL 16+ | **Implemented** |
| **Vector index**: pgvector | **Partial** — extension used, but indexes not verified |
| **Session/hot-state cache**: Redis | **Implemented** |
| **Graph store**: JSONB + indexed FKs | **Partial** — `canonical_nodes` table exists, but no rich graph traversal |
| **Durable messages**: PG LISTEN/NOTIFY + backing tables | **Implemented** |
| **Ephemeral messages**: Redis Pub/Sub | **Implemented** |
| **Sandbox runtime**: Temp-directory subprocess jail | **Implemented** |
| **Process management**: Procfile via honcho | **Implemented** |
| **Observability**: Structured JSON logs to stdout | **Partial** — logs are ad-hoc, not structured to the specified schema |

---

## Key Gaps Summary

| Feature | Status | Impact |
|---|---|---|
| Checkpoint serialization & recovery | **Missing** | Agents cannot resume after crash |
| Soul sheet JSON Schema validation | **Missing** | Invalid souls may crash agents |
| Soul sheet inheritance | **Missing** | No DRY for soul definitions |
| Semantic retrieval / pgvector HNSW | **Partial** | Memory subsystem cannot do semantic search |
| Scanner rule overlays (Semgrep, detect-secrets) | **Missing** | Security scanning is primitive regex only |
| Benchmark regression suite | **Missing** | No automated prompt quality gating |
| Replay bundles | **Missing** | No post-hoc session debugging |
| Policy Engine real-time tool enforcement | **Unclear** | Python agents may not be gated by Go policy engine |
| Bootstrap / AST ingestion | **Missing** | Canonical model is manually seeded |
| Go control plane maturity | **Partial** | Python carries more control-plane responsibility than intended |

---

## Strengths

1. **Message Bus**: The PG LISTEN/NOTIFY + Redis Pub/Sub abstraction is clean and well-implemented in both Go and Python.
2. **Database Schema**: The 6-database design with metrics views is thorough and queryable.
3. **Soul Sheets**: The YAML files are well-structured and closely follow the specification.
4. **End-to-End Smoke Test**: `tests/test_smoke.py` validates the basic pipeline works.
5. **Observability Views**: `070_metrics_views.sql` provides actionable dashboards.
6. **Embedder Protocol**: The JSON-lines stdin/stdout embedder subprocess is implemented exactly as specified.

---

## Conclusion

The RASA pilot is **architecturally coherent but implementationally thin** in advanced areas. The schema documents describe a mature, production-oriented multi-agent system with sophisticated safety, recovery, and evaluation mechanisms. The actual code is a **functional prototype** that validates the core loop (orchestrator → pool → agent → LLM → database) but leaves most of the safety, recovery, and quality-guarantee machinery as scaffolding or TODOs. The system is correctly described as "Phase 1 — pilot scaffolded end-to-end" in `CLAUDE.md`, which accurately reflects that the skeleton is complete but the organs are still developing.
