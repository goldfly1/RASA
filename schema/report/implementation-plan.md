# RASA Schema Completion Implementation Plan

> **Goal:** Bring the `/rasa` codebase to parity with the `/schema` implementation documents.  
> **Constraint:** Single-node Windows pilot. No external infrastructure beyond PostgreSQL 16, Redis, and Ollama Cloud.  
> **Baseline:** Phase 1 gates complete (pilot scaffolded end-to-end).  
> **Target:** All schema-specified components functional and wired for the pilot scale.

---

## Overview

The schema describes a mature multi-agent orchestration platform. The current code is a functional prototype with a working core loop (orchestrator → pool → agent → LLM → DB) but lacks safety, recovery, quality assurance, and advanced memory features. This plan closes the gap in **six phases**, each building on the last and ending with a testable gate.

---


> **Note (2026-05-13):** Former `extended-gates.md` (Gates 6–12) has been merged into this document. The Phase 0–6 structure subsumes the extended gate numbering. See the phase → gate mapping below.

## Phase 0 — Foundation & Shared Infrastructure

**Goal:** Fix duplicated code, validate soul sheets, and complete the database schema so later phases don't repeat groundwork.

### 0.1 Unified Database Connection
- **Problem:** `rasa/orchestrator/delegator.py`, `project.py`, and `capabilities.py` each contain a copy-pasted `_dsn()` helper with a hardcoded fallback password.
- **Action:** Replace all ad-hoc `psycopg.connect` calls in the orchestrator package with `rasa.db.conn.with_conn()` or `get_pool()`.
- **Files:** `rasa/orchestrator/delegator.py`, `project.py`, `capabilities.py`
- **Deliverable:** Zero duplicated DSN logic in the orchestrator package.

### 0.2 Soul Sheet JSON Schema Validation
- **Problem:** Souls are loaded with `yaml.safe_load()` and trusted blindly.
- **Action:**
  1. Write `schema/soul_sheet_schema.json` (draft 2020-12) covering required fields: `soul_version`, `soul_id`, `agent_role`, `model.default_tier`, `prompt.system_template`.
  2. Add `validate_soul(path) → dict` to `rasa/agent/runtime.py` that validates before loading. On failure, raise `SoulValidationError` with a clear message.
  3. Add a pytest test that validates every `.yaml` in `souls/` against the schema.
- **Files:** `rasa/agent/runtime.py`, new `schema/soul_sheet_schema.json`, `tests/test_soul_validation.py`
- **Deliverable:** `pytest tests/test_soul_validation.py -v` passes for all current souls; a malformed soul is rejected at load time.

### 0.3 Soul Sheet Inheritance Resolution
- **Problem:** All souls have `inherits: ~` and there is no merge logic.
- **Action:**
  1. Implement `_resolve_inheritance(soul_id: str) → dict` in `rasa/agent/runtime.py`.
  2. Merge parent → child recursively. Child overrides parent. Arrays are replaced, not appended.
  3. Detect inheritance cycles and raise `SoulValidationError`.
- **Files:** `rasa/agent/runtime.py`
- **Deliverable:** Create a `souls/base-coder.yaml` with shared principles; verify `coder-v2-dev.yaml` merges correctly.

### 0.4 Complete Task Table Schema
- **Problem:** The `tasks` table lacks `required_role`, `tags`, and `budget_tier` columns specified in `orchestrator.md`.
- **Action:** Add a migration `015_rasa_orch_task_envelope.sql` that adds:
  - `required_role TEXT`
  - `tags JSONB DEFAULT '[]'`
  - `budget_tier TEXT DEFAULT 'standard'`
  - `prompt_context JSONB DEFAULT '{}'`
- **Files:** `migrations/015_rasa_orch_task_envelope.sql`
- **Deliverable:** `test_smoke.py` still passes after the migration.

### Gate 0 Criteria
- [ ] No duplicated `_dsn()` helpers in `rasa/orchestrator/`
- [ ] All soul sheets validate against JSON Schema
- [ ] Inheritance resolution works with a parent/child test pair
- [ ] Task table has envelope columns; smoke test passes

---

## Phase 1 — Agent Runtime Hardening

**Goal:** Make the agent runtime production-grade: full state machine, tool execution, prompt hashing, and checkpointing.

### 1.1 Complete Agent State Machine
- **Problem:** Only `IDLE`, `WARMING`, `ACTIVE`, `CHECKPOINTED` exist. Missing `PAUSED`, `RESUMING`, `RECOVERING`.
- **Action:**
  1. Add missing states to `AgentState` enum in `rasa/agent/runtime.py`.
  2. Add transition methods: `_transition_to_paused()`, `_transition_to_resuming()`, `_transition_to_recovering()`.
  3. `PAUSED` triggers on a new `checkpoint_requested` flag (set by orchestrator or timeout).
  4. `RESUMING` triggers when a paused session is reactivated.
  5. `RECOVERING` triggers when the agent starts and detects a prior checkpoint for its `agent_id`.
- **Files:** `rasa/agent/runtime.py`
- **Deliverable:** State transitions are logged to stdout with timestamps.

### 1.2 Prompt Assembly Hash
- **Problem:** No SHA-256 hash is computed for LLM Gateway cache lookup.
- **Action:**
  1. In `_render_prompt()`, after final chevron render, compute `hashlib.sha256(f"{prompt}+{model_id}+{temperature}+{max_tokens}".encode()).hexdigest()`.
  2. Include `prompt_version_hash` in the `ModelRequest` envelope sent to `GatewayClient.complete()`.
- **Files:** `rasa/agent/runtime.py`
- **Deliverable:** Two identical tasks produce the same `prompt_version_hash`.

### 1.3 Tool Execution in Agent Runtime
- **Problem:** The runtime calls the LLM but never executes tool calls returned by the model.
- **Action:**
  1. Add a `ToolExecutor` class in `rasa/agent/tools.py` with at minimum:
     - `file_read(path) → str`
     - `file_write(path, content) → None`
     - `shell_exec(command, timeout=30) → str`
     - `git_diff(path="") → str`
  2. After `gateway.complete()` returns, inspect the response for tool calls (OpenAI format).
  3. Execute each tool, append the result to the conversation context, and re-call the LLM (max `MAX_TOOL_TURNS=15`).
  4. Enforce `behavior.tool_policy.allowed_tools` / `denied_tools` before executing. Log denials.
- **Files:** New `rasa/agent/tools.py`, `rasa/agent/runtime.py`
- **Deliverable:** An agent tasked with "read README.md and summarize it" successfully calls `file_read`, gets the content, and returns a summary.

### 1.4 Checkpoint Serialization
- **Problem:** `CHECKPOINTED` state is a no-op.
- **Action:**
  1. On `PAUSED` or `CHECKPOINTED`, serialize:
     - `conversation_messages`
     - `memory_context`
     - `current_task_id`
     - `soul_id`, `soul_version`, `prompt_version_hash`
  2. Write to:
     - **Redis** — key `checkpoint:{agent_id}`, TTL = `2 × max_idle_minutes`
     - **PostgreSQL** — `checkpoints` table (already in migrations)
     - **Flat file** — `data/archive/{task_id}/{checkpoint_id}.json`
  3. On agent startup, check Redis for `checkpoint:{agent_id}`. If found, restore state and enter `RECOVERING`.
- **Files:** `rasa/agent/runtime.py`, `migrations/060_rasa_recovery.sql` (verify `checkpoints` table)
- **Deliverable:** Kill an agent mid-task, restart it, and verify it resumes from the checkpoint (task continues to `COMPLETED`).

### Gate 1 Criteria
- [ ] All 7 states exist and are reachable in a test
- [ ] `prompt_version_hash` is computed and stable
- [ ] Agent can execute `file_read` and `shell_exec` tools end-to-end
- [ ] Checkpoint save + restore works across process restarts

---

## Phase 2 — Control Plane Maturation

**Goal:** Move control-plane logic out of Python stubs and into the Go binaries where specified. Add retry, DAG validation, and pool state machine.

### 2.1 Go Orchestrator: Capability Matching & Retry
- **Problem:** The Go orchestrator CLI only inserts rows. The Python `OrchestratorRuntime` handles delegation. The schema says Go owns scheduling.
- **Action:**
  1. In `cmd/orchestrator/main.go`, after inserting a task, query `agent_capabilities` (already seeded by `100_rasa_capabilities.sql`).
  2. Implement the capability match algorithm from `orchestrator.md` §2.3:
     - Filter by `required_role`
     - Score by tag overlap
     - Filter by `budget_tier`
     - Select highest score
  3. Add retry loop: on `tasks_assigned` NOTIFY, if Pool Controller NACKs (no agent available), sleep 5s and retry up to 3 times.
  4. Expose a `submit --wait` path that polls the `tasks` table until `COMPLETED`/`FAILED`/`CANCELLED`.
- **Files:** `cmd/orchestrator/main.go`, `internal/db/db.go`
- **Deliverable:** `orchestrator.exe submit --soul coder-v2-dev --title "Test" --wait` returns the task result.

### 2.2 Go Orchestrator: Task DAG Cycle Detection
- **Problem:** No DAG validation; `parent_id` could create cycles.
- **Action:**
  1. Before updating `parent_id`, run a DFS from the proposed parent to verify it does not reach the child.
  2. If a cycle is detected, reject the insert/update and log `CYCLE_DETECTED`.
- **Files:** `cmd/orchestrator/main.go`
- **Deliverable:** A test inserts tasks A→B→C→A; the final insert is rejected.

### 2.3 Go Pool Controller: Agent Registry & State Machine
- **Problem:** The Python pool controller is a minimal listener. The Go stub exists but lacks the registry and state machine.
- **Action:**
  1. In `cmd/pool-controller/main.go`, use the existing `internal/pool/agent_registry.go` to:
     - Track `agent_id`, `soul_id`, `current_state`, `task_id`, `last_heartbeat`, `memory_usage_bytes`
     - Write heartbeats to `rasa_pool.heartbeats` and `rasa_pool.agents`
  2. Implement the state machine from `pool_controller.md` §2.1:
     - `UNDERLOADED` — all agents idle
     - `STEADY` — active agents within capacity
     - `BACKPRESSURE` — all agents busy or no agent for soul
     - `DRAINING` / `STANDBY` — shutdown handling
  3. On `BACKPRESSURE`, insert a row into `backpressure_events`.
  4. Timeout dead agents after `3 × heartbeat_interval` and notify orchestrator.
- **Files:** `cmd/pool-controller/main.go`, `internal/pool/controller.go`, `internal/pool/agent_registry.go`
- **Deliverable:** Run the Go pool controller. Start 2 agents. Submit 3 tasks for `coder-v2-dev`. Verify the 3rd task triggers a `backpressure_events` row.

### 2.4 Go Pool Controller: Soul-Aware Task Routing
- **Problem:** The pool controller spawns a subprocess for every task instead of routing to a pre-warmed agent.
- **Action:**
  1. On `tasks_assigned`, look up an idle agent with matching `soul_id` in the registry.
  2. If found, mark it `ASSIGNED` and do nothing else — the agent's own poll loop will pick up the task.
  3. If not found, insert `backpressure_events` and NACK.
  4. Remove the Python pool controller from `Procfile` once the Go version is validated.
- **Files:** `cmd/pool-controller/main.go`, `Procfile`
- **Deliverable:** Tasks are routed to idle agents without spawning new processes.

### Gate 2 Criteria
- [ ] Go orchestrator matches capabilities and retries assignment
- [ ] Cycle detection rejects cyclic parent_id inserts
- [ ] Go pool controller tracks agent registry and writes heartbeat rows
- [ ] Backpressure events are generated when pool is saturated

---

## Phase 3 — Safety, Sandbox & Recovery

**Goal:** Wire the Policy Engine to the agent runtime, replace the regex scanner, and make recovery functional.

### 3.1 Policy Engine Integration with Agent Runtime
- **Problem:** `internal/policy/` exists in Go but the Python agent runtime does not consult it before executing tools.
- **Action:**
  1. Add a lightweight HTTP admin endpoint to `cmd/policy-engine/main.go` (e.g., `:8304/evaluate`) that accepts `{tool, args, soul_id}` and returns `{decision: allow|deny|review, reason}`.
  2. In `rasa/agent/tools.py`, before executing any tool, POST to the policy endpoint.
  3. If `deny`, raise `PolicyDeniedError` and abort the task.
  4. If `review`, pause and emit a CLI prompt (pilot) or log a review request.
  5. Write every decision to `rasa_policy.audit_log`.
- **Files:** `cmd/policy-engine/main.go`, `rasa/agent/tools.py`, `internal/policy/engine.go`
- **Deliverable:** An agent with `denied_tools: ["shell_exec:sudo"]` is blocked when it tries `sudo`.

### 3.2 Scanner Rule Overlays
- **Problem:** `scanners/` is empty. `rasa/sandbox/scanner.py` uses hardcoded regex.
- **Action:**
  1. Create `scanners/base-rules.yaml` with the patterns from `scanner.py` (AWS keys, private keys, API keys, passwords, connection strings).
  2. Create `scanners/coder-overlay.yaml`, `reviewer-overlay.yaml`, `planner-overlay.yaml`, `architect-overlay.yaml` as specified in `sandbox_pipeline.md` §2.2.
  3. Rewrite `rasa/sandbox/scanner.py` to load YAML rule files and apply overlays based on `soul_id` / `agent_role`.
  4. Keep the existing regex engine for the pilot (Semgrep is the upgrade path, but the rule loader should be ready for it).
- **Files:** `scanners/*.yaml`, `rasa/sandbox/scanner.py`
- **Deliverable:** A sandbox run for `coder-v2-dev` loads `base-rules.yaml` + `coder-overlay.yaml`. A run for `reviewer-v1` skips build/test per `reviewer-overlay.yaml`.

### 3.3 Orphan Sandbox Reaping
- **Problem:** Temp directories in `data/sandbox/` accumulate if the pipeline crashes.
- **Action:**
  1. In `rasa/sandbox/pipeline.py`, add a background `asyncio` task that scans `data/sandbox/` every 5 minutes.
  2. Delete directories older than 30 minutes.
  3. Log `ORPHAN_SANDBOX_DESTROYED` with count.
- **Files:** `rasa/sandbox/pipeline.py`
- **Deliverable:** Create an old directory in `data/sandbox/`. Start the pipeline. Verify it is deleted within 5 minutes.

### 3.4 Recovery Controller: Full Checkpoint Replay
- **Problem:** Checkpoints are not being written (Phase 1.4 fixes that). Recovery Controller has a ledger but no replay logic.
- **Action:**
  1. In `internal/recovery/controller.go`, on heartbeat miss > threshold:
     - Query `rasa_recovery.checkpoints` for the dead `agent_id`.
     - If found, read `soul_id` and `prompt_version_hash`.
     - Re-read the soul sheet from `souls/`.
     - If `soul_version` differs, follow the mismatch rules from `recovery_controller.md` §2.4 (minor → migrate, major → fail).
     - Reconstruct the prompt and validate the hash.
     - Publish `session.restored` via PG NOTIFY so the Orchestrator updates task state to `RUNNING`.
  2. If no checkpoint, re-queue the task to `PENDING` and write to `recovery_log`.
- **Files:** `internal/recovery/controller.go`, `internal/recovery/ledger.go`
- **Deliverable:** Kill an agent mid-task. Verify the Recovery Controller detects the miss, finds the checkpoint, and restores the session within 5 seconds.

### Gate 3 Criteria
- [ ] Policy Engine blocks denied tools in real time
- [ ] Scanner loads role-specific YAML overlays
- [ ] Orphan sandboxes older than 30 min are auto-deleted
- [ ] Recovery Controller replays a checkpoint and resumes a killed agent

---

## Phase 4 — Memory, Retrieval & Context Assembly

**Goal:** Make the Memory Subsystem do what the schema promises: semantic retrieval, canonical model updates, and full context assembly.

### 4.1 Context Assembly Endpoint
- **Problem:** `memory-controller` Go stub listens on `:8300` but the actual assembly logic is missing.
- **Action:**
  1. In `cmd/memory-controller/main.go` or `internal/memory/assembler.go`, implement `POST /assemble`:
     - Accept `{soul_id, task_id, variables: ["short_term_summary", "graph_excerpt", "semantic_matches"]}`.
     - Query Redis for `short_term_summary` (last N conversation turns).
     - Query `rasa_memory.canonical_nodes` for `graph_excerpt` (recursive CTE, depth-limited by `memory.graph_traversal_depth`).
     - Query `rasa_memory.embeddings` via pgvector for `semantic_matches` (top-k by cosine similarity).
     - Return `{variables: {...}}`.
  2. Ensure the response is deterministic for cache hits.
- **Files:** `internal/memory/assembler.go`, `cmd/memory-controller/main.go`
- **Deliverable:** `curl -X POST http://127.0.0.1:8300/assemble ...` returns all three context variables.

### 4.2 pgvector HNSW Index & Chunking
- **Problem:** `rasa/memory/pgvector.py` exists but there is no evidence of HNSW index creation or document chunking.
- **Action:**
  1. Add a migration `045_rasa_memory_vector_index.sql`:
     - `CREATE INDEX ON embeddings USING hnsw (embedding vector_cosine_ops);`
  2. In `rasa/memory/pgvector.py`, implement `chunk_and_embed(text: str, chunk_size=512, overlap=64) → list[Embedding]`.
  3. Respect file boundaries — do not split mid-line.
  4. Batch insert chunks into `rasa_memory.embeddings`.
- **Files:** `migrations/045_rasa_memory_vector_index.sql`, `rasa/memory/pgvector.py`
- **Deliverable:** Embed a 2000-token file. Verify 4–5 chunks are created and searchable via pgvector.

### 4.3 Canonical Model Reconciler
- **Problem:** `canonical_nodes` is seeded once by `080_seed_lore.sql`. No updates.
- **Action:**
  1. Add a background goroutine to `cmd/memory-controller/main.go` that runs every 6 hours.
  2. Diff current `canonical_nodes` against a fresh AST scan of the repo.
  3. Upsert new facts. Never overwrite a row with a newer timestamp from an agent write.
  4. Log `RECONCILER_DIFF` with counts.
- **Files:** `internal/memory/canonical.go`, `cmd/memory-controller/main.go`
- **Deliverable:** After adding a new module to `rasa/`, the reconciler detects it within 6 hours and adds a canonical node.

### 4.4 Session Store Eviction
- **Problem:** Redis session data has no TTL or eviction policy.
- **Action:**
  1. In `rasa/agent/runtime.py`, when checkpointing, set Redis key TTL to `2 × behavior.session.max_idle_minutes`.
  2. In `cmd/memory-controller/main.go`, add a periodic eviction scan that promotes expired sessions to PostgreSQL before deleting.
- **Files:** `rasa/agent/runtime.py`, `internal/memory/session_store.go`
- **Deliverable:** Redis keys for sessions expire correctly; no data loss because PostgreSQL has the durable copy.

### Gate 4 Criteria
- [ ] `/assemble` endpoint returns all three context types
- [ ] pgvector HNSW index exists and semantic search returns relevant chunks
- [ ] Canonical model is updated by both agent checkpoints and the background reconciler
- [ ] Redis session TTLs are set and honored

---

## Phase 5 — Observability, Evaluation & Replay

**Goal:** Structured logging, replay bundles, drift detection, and benchmark regression.

### 5.1 Structured JSON Logs
- **Problem:** Components print ad-hoc strings to stdout. The schema specifies a strict JSON schema.
- **Action:**
  1. Add `rasa/observability/logger.py` with a `StructuredLogger` class.
  2. Every log line must be JSON with fields: `level`, `timestamp`, `component`, `event`, `soul.id`, `soul.role`, `soul.prompt_hash`, `task.id`, `agent.id`, `message`.
  3. Replace `print()` statements in `rasa/agent/runtime.py`, `rasa/sandbox/pipeline.py`, and `rasa/pool/controller.py` with the structured logger.
  4. In Go, use `internal/observability/logger.go` (create it) with the same schema.
- **Files:** New `rasa/observability/logger.py`, `internal/observability/logger.go`
- **Deliverable:** `honcho start` output is parseable by `jq '.event'`. Every line has `component` and `soul.id` where applicable.

### 5.2 Replay Bundles
- **Problem:** No replay artifacts are captured.
- **Action:**
  1. On `CHECKPOINTED`, create `data/replays/{task_id}/` with:
     - `soul_sheet.yaml` — exact soul used
     - `prompt_template.txt` — before substitution
     - `prompt_final.txt` — after substitution
     - `reasoning_trace.jsonl` — tool calls + LLM responses
     - `memory_context.json` — Memory Subsystem output
     - `policy_decisions.json` — allow/deny log
     - `sandbox_results.json` — gate results
     - `metadata.json` — task_id, soul_id, prompt_version_hash, model_id, token_count
  2. Gzip the directory after 24 hours.
- **Files:** `rasa/agent/runtime.py`, `rasa/observability/replay.py`
- **Deliverable:** After any task completes, `data/replays/{task_id}/` exists with all 8 files.

### 5.3 Drift Detection Alerting
- **Problem:** `drift_snapshots` table and `v_latest_drift` exist, but no active drift math or alerts.
- **Action:**
  1. In `internal/eval/aggregator.go`, maintain a 20-task rolling window per `soul_id`.
  2. Compute rolling mean, std, and pass rate every 60 seconds.
  3. Flag if:
     - Pass rate < 95%
     - p99 latency > 2× baseline
     - Token consumption > 1.5× baseline
  4. Write `drift_snapshots` rows with `flagged = true`.
  5. If flagged, emit a PG NOTIFY on `drift_alert` channel.
- **Files:** `internal/eval/aggregator.go`
- **Deliverable:** A deliberately bad soul sheet (e.g., very low `max_tokens`) triggers a drift alert within 20 tasks.

### 5.4 Prompt Regression Benchmark
- **Problem:** `benchmarks/` is empty.
- **Action:**
  1. Create `benchmarks/syntax_tasks.json` and `benchmarks/security_tasks.json` with 5 tasks each.
  2. Add `rasa/eval/benchmark.py` that:
     - Loads a candidate soul and its parent
     - Runs each benchmark task through the standard pipeline
     - Compares `score`, `cycle_time_ms`, `tokens_consumed`
     - Blocks promotion if any metric regresses > 5%
  3. Add a CLI: `python -m rasa.eval.benchmark --soul coder-v2-dev --candidate souls/coder-v2-dev-candidate.yaml`.
- **Files:** `benchmarks/*.json`, `rasa/eval/benchmark.py`
- **Deliverable:** Changing a soul sheet's `temperature` from 0.2 to 0.9 causes the benchmark to flag regression.

### Gate 5 Criteria
- [ ] All component stdout is structured JSON
- [ ] Every completed task leaves a replay bundle in `data/replays/`
- [ ] Drift detection flags under-performing souls within 20 tasks
- [ ] Benchmark suite blocks a regressed soul sheet promotion

---

## Phase 6 — Bootstrap & Onboarding Automation

**Goal:** Replace manual canonical model seeding with automated repo ingestion.

### 6.1 Bootstrap CLI Module
- **Problem:** No `rasa.bootstrap` module exists.
- **Action:**
  1. Create `rasa/bootstrap/__main__.py` with CLI:
     `python -m rasa.bootstrap --repo /path/to/target-repo`
  2. Steps:
     - Extract AST & deps using `tree-sitter` (Python bindings) for Go, Python, TypeScript.
     - Build canonical model: write to `rasa_memory.canonical_nodes` and `canonical_edges`.
     - Embed files: chunk, call `embedder.py`, store in `rasa_memory.embeddings`.
     - Load souls: validate, resolve inheritance, store in `soul_sheets`.
     - Baseline freeze: snapshot `canonical_model` as `baseline_v1`.
  3. Emit `souls.loaded` PG NOTIFY on completion.
- **Files:** New `rasa/bootstrap/`, `rasa/bootstrap/ast_extractor.py`, `rasa/bootstrap/embedder_pipeline.py`
- **Deliverable:** Running bootstrap on the RASA repo itself populates `canonical_nodes` and `embeddings` without errors.

### 6.2 Baseline Freezing
- **Problem:** No concept of a frozen baseline.
- **Action:**
  1. Add `baselines` table to `rasa_memory` with `version`, `snapshot_jsonb`, `created_at`.
  2. At the end of bootstrap, dump `canonical_nodes` + `canonical_edges` into `snapshot_jsonb`.
  3. Lock soul sheets from editing until explicitly unfrozen (or use Git tags as the real lock).
- **Files:** `migrations/041_rasa_memory_baselines.sql`, `rasa/bootstrap/baseline.py`
- **Deliverable:** After bootstrap, `SELECT * FROM baselines` returns one row with a populated snapshot.

### Gate 6 Criteria
- [ ] `python -m rasa.bootstrap --repo .` runs end-to-end on the RASA repo
- [ ] `canonical_nodes`, `canonical_edges`, and `embeddings` are populated
- [ ] A baseline snapshot is stored in PostgreSQL
- [ ] `souls.loaded` NOTIFY is emitted on completion

---

## Rollout Strategy

| Phase | Estimated Effort | Risk |
|---|---|---|
| 0 — Foundation | 2–3 days | Low |
| 1 — Agent Runtime | 4–5 days | Medium (checkpointing is tricky) |
| 2 — Control Plane | 4–5 days | Medium (Go binary maturity) |
| 3 — Safety & Recovery | 3–4 days | Medium (cross-language integration) |
| 4 — Memory & Retrieval | 4–5 days | High (pgvector performance tuning) |
| 5 — Observability & Eval | 3–4 days | Low |
| 6 — Bootstrap | 3–4 days | Medium (tree-sitter dependency) |

**Total:** ~23–30 days of focused implementation.

**Recommended order:** Do not skip Phase 0. Phases 1 and 2 can be done in parallel by different developers (one on Python, one on Go). Phase 3 depends on Phase 1 (checkpoints). Phase 4 depends on Phase 1 (memory context). Phase 5 can begin once Phase 1 is done (replay bundles need checkpoints). Phase 6 is independent but benefits from Phase 4 (pgvector readiness).

---

## Testing Cadence

- **Unit tests:** Every new module gets pytest or `go test` coverage before merge.
- **Integration tests:** After each phase, run `pytest tests/test_smoke.py -v` plus a phase-specific integration test.
- **End-to-end demo:** After Phase 3, the system should survive an agent crash and recover. After Phase 5, drift alerts should fire on a bad soul.
- **Performance gate:** After Phase 4, semantic search p99 latency < 100ms for a 10K-embedding pilot dataset.

---

*This plan closes all gaps identified in `schema-vs-implementation-report.md`. Each phase ends with a testable gate so progress is verifiable, not theoretical.*
