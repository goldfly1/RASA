# Top-Level Decisions

> **Architectural Reference:** `architectural_schema_v2.1.md` (cross-cutting)
> **Status:** Updated 2026-05-13 — reflects all Gates 0–5 verified
> **Hardware Environment:** Omen 16 — Intel Ultra 7 255, 64 GB RAM, RTX 5060 8 GB VRAM, 1 TB SSD (~250 GB free). PostgreSQL 16 + pgvector installed locally. Single-node lab machine.
> **Owner:** TBD
> **Last Updated:** 2026-04-28

---

## 1. Purpose

Records global implementation choices that affect multiple components: programming languages, deployment platform, persistence strategy, message transport, container strategy, networking, and standards.

These decisions are **provisional by design**. Each entry includes a review date. When a decision changes, this document is updated first; downstream component docs follow.

---

## 2. Decisions

### 2.1 Languages

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Control Plane** | **Go 1.24+** | Goroutines/channels map cleanly to the Orchestrator, Pool Controller, Message Bus, and Recovery Controller. Static binaries compile to a single executable per service. | 2026-07-23 |
| **Agent Runtime & LLM Gateway** | **Python 3.12+** | Ecosystem dominance for LLM SDKs (OpenAI, Anthropic, transformers). Agents require rapid iteration; Python minimizes glue code for model calls. | 2026-07-23 |
| **Inter-language contracts** | **JSON over localhost** (pilot); **Protobuf + gRPC** (upgrade) | JSON avoids codegen overhead during rapid iteration. gRPC can be introduced when components are deployed across hosts. | 2026-07-23 |

### 2.2 Persistence

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Primary durable store** | **PostgreSQL 16+** | ACID guarantees for the Task Lifecycle state machine, Idempotency Ledger, and Evaluation Records. JSONB accommodates polymorphic checkpoint metadata without schema migrations. | 2026-07-23 |
| **Vector index** | **pgvector** (PostgreSQL extension) | Single operational surface with the primary store. Simplifies backups, transactions, and access control. Acceptable latency trade-off for the current scale; a dedicated vector DB can be introduced later without changing the architecture boundary. | 2026-07-23 |
| **Session / hot-state cache** | **Redis** (single-node) | Sub-millisecond TTL-based heartbeats, warm pool pre-initialization, and ephemeral checkpoint buffers. Volatile by design; durability is PostgreSQL's job. Single-node for pilot; Redis Cluster is the documented upgrade path. | 2026-07-23 |
| **Graph store** | **JSONB columns + indexed foreign keys** (pilot) | The canonical model for a single-repo pilot fits comfortably in JSONB columns. Multi-hop graph traversal is not needed at this scale. Migrate to pg_graph / Apache AGE when the model outgrows a few JOINs. | 2026-07-23 |
| **Blob / snapshot store** | **Flat files on disk** (pilot: `<data_dir>/snapshots/`) | Agent session snapshots, replay bundles, and conversation archives stored as files under a project-local directory. Pointers stored in PostgreSQL `checkpoint_refs` table. Migrate to MinIO / S3 when cross-host access is required. | 2026-07-23 |

### 2.3 Message Transport

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Durable messages** (tasks, checkpoints, results) | **PostgreSQL LISTEN/NOTIFY** + backing tables | Zero new dependencies — PostgreSQL is already in the stack. Transaction-safe. At-least-once delivery via backing table status columns. Adequate throughput for pilot scale (≤ 1 msg/sec per channel). | 2026-07-23 |
| **Ephemeral messages** (heartbeats, policy updates) | **Redis Pub/Sub** | Already in the stack. Sub-millisecond publish. Loss-tolerant — heartbeats are refreshed every 5s; policy polling catches missed updates within 30s. | 2026-07-23 |
| **Envelope format** | **JSON** | Human-readable, no codegen step, sufficient for localhost pilot. Protobuf + gRPC as the documented upgrade path for cross-host deployment. | 2026-07-23 |

> **Rationale for the change from NATS:** On a single-machine pilot with ~10 components, the operational complexity of adding a third infrastructure service (NATS) outweighs the benefits of JetStream durability. PostgreSQL + Redis already provide the needed durability and pub/sub patterns. JetStream is the documented upgrade path for multi-node deployments.

### 2.4 Sandbox & Execution

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Sandbox runtime** | **Temp-directory subprocess jail** (pilot) | Each build/test runs in a disposable temp folder with a timeout and process-tree kill on failure. Proves the Sandbox Pipeline state machine without OS-level virtualization. gVisor (Linux host) is the documented upgrade path. | 2026-07-23 |
| **Scanner chain** | **Python** (Semgrep, detect-secrets, custom AST rules) | Reuse the agent-runtime language to share security rule libraries and reduce toolchain sprawl. | 2026-07-23 |

### 2.5 Deployment & Infrastructure

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Process management** | **Procfile** via `honcho` (Python) or `launch.ps1` | Single command starts all native processes. Ctrl-C shuts down the constellation. No orchestration dependency for the pilot. | 2026-07-23 |
| **Component lifecycle** | **Native binaries + Python venvs** | Go components built with `go build`, Python components run in a project-local virtual environment. No container images. | 2026-07-23 |
| **Observability** | **Structured JSON logs to stdout/stderr** (pilot); **OpenTelemetry → Prometheus/Grafana** as upgrade path | Distributed tracing adds infrastructure overhead. For the pilot, structured JSON logs captured from each component's stdout provide sufficient debugging capability. | 2026-07-23 |
| **Secret & config distribution** | **`.env` file** + OS environment variables (pilot) | Short-lived LLM API keys and database credentials loaded from a `.env` file in the project root, not checked into version control. | 2026-07-23 |

### 2.6 Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `RASA_DEFAULT_MODEL` | `Deepseek-v4-flash:cloud` | Standard tier LLM |
| `RASA_PREMIUM_MODEL` | `Deepseek-v4-pro:cloud` | Premium tier LLM |
| `RASA_REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `RASA_DB_PASSWORD` | — | PostgreSQL password |
| `FALLBACK_API_KEY` | — | OpenAI API key for last-resort fallback |

> **Upgrade Path:** The architecture boundaries are deployment-agnostic. When the pilot outgrows a single machine, the migration path is: native Procfile → Docker Compose → Kubernetes. No component requires K8s-specific APIs in its current design; all interfaces (PostgreSQL connections, Redis connections, gRPC endpoints) are host/port-addressable.

### 2.6 API Exposure

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Internal communication** | **JSON over localhost** (pilot); **gRPC** (upgrade) | PostgreSQL LISTEN/NOTIFY + Redis Pub/Sub for messaging; JSON REST for admin operations. gRPC streamlines when components leave localhost. | 2026-07-23 |
| **External / human-facing** | **REST** via CLI or HTTP | Operational dashboards and manual interventions use REST/JSON. Implemented as lightweight HTTP server per component in pilot. | 2026-07-23 |

### 2.7 Agent Configuration & Prompting

| Decision | Selection | Rationale | Review Date |
|----------|-----------|-----------|-------------|
| **Soul sheet format** | **YAML 1.2** | Human-readable, diff-friendly in Git, widely understood by ops and dev teams. | 2026-07-23 |
| **Schema validation** | **JSON Schema** (draft 2020-12) | Standard, well-understood, generates clear error messages at load time. | 2026-07-23 |
| **Template engine** | **Mustache/Handlebars** (Python: `chevron`) | Logic-light, deterministic output (same inputs = same hash). Prompt assembly happens in the Python Agent Runtime; no Go-side rendering needed. | 2026-07-23 |
| **Inheritance resolution** | **Parent-child merge** (child overrides) | DRY principle for soul sheets; base `coder` soul, specialized `coder-python` child. Arrays replaced, not appended. | 2026-07-23 |
| **Soul storage (source of truth)** | **Git** | History, code review, and branching for soul sheet changes. | 2026-07-23 |
| **Soul storage (runtime)** | **Local `souls/` directory** (pilot); **PostgreSQL + S3** (upgrade) | Flat YAML files loaded directly from disk. Fast iteration — edit, save, filesystem watcher triggers reload. | 2026-07-23 |
| **Hot reload** | **Filesystem watcher** (pilot); **Redis Pub/Sub** `souls.update` channel (upgrade) | On a native install with soul sheets as local YAML files, a filesystem watcher (`fsnotify` in Go, `watchdog` in Python) is simpler than a message bus for hot-reload. | 2026-07-23 |

---

## 3. Decision Impact Matrix

| Component | Primary Language | Key Persistence | Transport |
|-----------|------------------|-----------------|-----------|
| Orchestrator | Go | PostgreSQL | PG LISTEN/NOTIFY (tasks) |
| Pool Controller | Go | PostgreSQL + Redis | PG LISTEN/NOTIFY (tasks) + Redis Pub/Sub (heartbeats) |
| Recovery Controller | Go | PostgreSQL + flat files | PG LISTEN/NOTIFY (checkpoints) |
| Agent Runtime | Python | Redis + PostgreSQL | Redis Pub/Sub (heartbeats) + PG LISTEN/NOTIFY (checkpoints) |
| Agent Configuration | Go (validator), Python (loader) | Flat files (souls/ directory) + PostgreSQL | Filesystem watcher |
| LLM Gateway | Python | Redis (cache) | Direct HTTP to Ollama Cloud |
| Sandbox Pipeline | Python | Flat files (artifacts) | PG LISTEN/NOTIFY (results) |
| Memory Subsystem | Go (controller), Python (embedder) | PostgreSQL + pgvector + Redis | Direct DB calls |
| Observability Stack | Go | JSON log files (pilot) | stdout |
| Evaluation Engine | Go (aggregator), Python (scorer) | PostgreSQL | PG LISTEN/NOTIFY (eval records) |
| Policy Engine | Go | PostgreSQL (rules) | Redis Pub/Sub (policy.update) + PostgreSQL poll |

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1-4 |/K8s/S3/AGE scaling questions | **Resolved:** Pilot uses PG LISTEN/NOTIFY + Redis Pub/Sub, native processes, flat files, JSONB. |
| 5 | pgvector index type: HNSW vs. IVFFlat | **Open** — HNSW recommended for pilot. IVFFlat for larger datasets. |
| 6 | Max tolerable cold-start time? | **Deferred** — depends on pilot repo size. |
| 7 | Per-soul budget quota? | **Open** — not implemented in pilot. Global ceiling only. |

---

## 5. Implementation Gates

| Gate | Phase | Criteria | Status |
|------|-------|----------|--------|
| **Gate 0** | Planning | All implementation docs provisioned. Tech stack decisions locked. Pilot bootstrap artifacts defined. | ✅ Complete |
| **Gate 1** | Foundation | Go module + Python package scaffolded. PostgreSQL schemas created. Shared messaging interfaces working (PG LISTEN/NOTIFY + Redis Pub/Sub). Config files, Procfile, soul sheets on disk. | ✅ Complete |
| **Gate 2** | Core Services | Policy Engine evaluates rules end-to-end. Memory Subsystem stores/retrieves context. LLM Gateway routes requests to Ollama Cloud. Each independently testable. | ✅ Complete |
| **Gate 3** | Agent Lifecycle | Agent Runtime loads soul, assembles prompt, calls LLM. Pool Controller tracks heartbeats, routes tasks. Orchestrator submits task → agent processes → output promoted. First end-to-end flow works. | ✅ Complete |
| **Gate 4** | Safety & Quality | Sandbox Pipeline scans/builds/tests output. Recovery Controller replays checkpoint. Evaluation Engine detects drift. | ✅ Complete |
| **Gate 5** | Integration | Observability captures metrics from all components into database tables with queryable SQL views. Smoke test verifies end-to-end task submission and pipeline. observe.py provides live terminal dashboard. | ✅ Complete |


> **Note (2026-05-13):** Post-gate work has further hardened the runtime (NOTIFY fix, tool-calling loop, checkpoint serialization), policy engine (7 rules seeded), dispatcher (tool execution), and added bootstrap ingestion, scanner overlays, and drift detection. See `schema/report/schema-vs-implementation-report.md` for current status.
---

## 6. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-28 | Replaced JetStream with PostgreSQL LISTEN/NOTIFY + Redis Pub/Sub in §2.3. Updated Decision Impact Matrix transport column. Added Implementation Gates (§5). | Codex |
| 2026-04-28 | Removed WSL references; everything runs natively on Windows. Claude Code is the orchestrator. Replaced NATS upgrade path references with Redis Pub/Sub. Cleaned up gVisor note to remove WSL2. | Goldf |
| 2026-04-28 | Gate 3: Agent Runtime (state machine + chevron + GatewayClient + Memory API + heartbeats), Pool Controller (agent registry + heartbeat monitoring + task routing), Orchestrator CLI (submit --wait). Removed --nats from pool-controller + orchestrator. | Goldf |
| 2026-04-29 | Gate 4: Sandbox Pipeline (clone/scan/build/test/promote state machine, regex secret scanner), Recovery Controller (heartbeat monitoring, dead agent detection, task re-queue, idempotency ledger), Evaluation Engine (eval_record subscriber, 20-task drift window, Python scorer). Removed --nats from recovery-controller + eval-aggregator. Created scanners/, benchmarks/, data/sandbox/ dirs. | Goldf |
| 2026-04-29 | Gate 5: Fixed timestamp gaps (assigned_at, failed_at, retry_after). Wired durable writes to heartbeats, backpressure_events, agents, drift_snapshots, and recovery_log tables. Created 070_metrics_views.sql with SQL views (v_task_latency, v_daily_summary, v_soul_performance, v_latest_drift, v_agent_uptime, v_recent_backpressure, v_recent_decisions, v_recent_recoveries). Built observe.py live terminal dashboard. Added test_smoke.py end-to-end test. | Goldf |
| 2026-04-25 | Pilot provisioning: initial native-process decisions. | Codex |
| 2026-04-23 | Initial populated draft from architectural_schema_v2.1.md | ? |
