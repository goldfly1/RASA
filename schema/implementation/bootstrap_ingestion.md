# Bootstrap & Ingestion

> **Architectural Reference:** `architectural_schema_v2.1.md` §8  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — initial soul sheet loading during cold start   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Handles onboarding of a new repository into the agentic system: dependency graph extraction, initial vector index construction, baseline establishment, canonical model seeding (JSONB), and initial soul sheet loading from the local `souls/` directory.

---

## 2. Internal Design

### 2.1 Cold Start Sequence

```
Repository cloned
        |
        v
  +---------------------+
  | Extract AST & deps  |   Python: tree-sitter for multi-language parsing
  +---------------------+
        |
        v
  +---------------------+
  | Build canonical     |   Write to PostgreSQL JSONB + indexed FKs
  | model (JSONB)       |   Tables: canonical_model.nodes, canonical_model.edges
  +---------------------+
        |
        v
  +---------------------+
  | Embed files         |   OpenAI text-embedding-3-small via API
  | → pgvector          |   512-token chunks, 64-token overlap, file-boundary-aware
  +---------------------+
        |
        v
  +---------------------+
  | Load soul sheets    |   Scan <project_root>/souls/*.yaml
  +---------------------+
        |
        v
  +---------------------+
  | Validate & freeze   |   JSON Schema validation + baseline snapshot
  +---------------------+
```

### 2.2 Soul Sheet Ingestion

During bootstrap, the system:

1. Discovers all YAML files in `<project_root>/souls/` matching `*.yaml`.
2. Validates each against JSON Schema (see [`agent_configuration.md`](agent_configuration.md) §2.3).
3. Resolves inheritance chains and detects cycles (e.g., A inherits B, B inherits A).
4. Stores resolved soul sheets in PostgreSQL `soul_sheets` table with `soul_id` as primary key.
5. **Publishes `souls.loaded` via PostgreSQL LISTEN/NOTIFY** — Pool Controller subscribes to begin pre-warming agents with the available souls.
6. Computes baseline `prompt_version_hash` for each soul and stores in `soul_baselines` table.

> **Fallback:** As a fallback, the Pool Controller can also scan `<project_root>/souls/` directly on startup. The `souls.loaded` notification is the primary path for coordinated startup.

### 2.3 Baseline Freezing

Once bootstrapped, the system enters a **baseline frozen** state:

- **Soul sheets** are locked until explicitly updated via the Evaluation Engine promotion gate.
- **Canonical model** is snapshotted as `baseline_v1` — a JSONB dump of the `canonical_model` tables.
- **Vector index** is tagged with the baseline version in the pgvector metadata.
- **Baseline record** is stored in PostgreSQL `baselines` table with timestamp and version.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Ingestion driver** | **Python 3.12+** | tree-sitter for AST extraction, OpenAI SDK for embeddings, YAML parsing — all Python-native. |
| **Coordination controller** | **Go 1.24+** | Manages the pipeline sequence, publishes LISTEN/NOTIFY notifications, writes to PostgreSQL. |
| **AST parsing** | **tree-sitter** (Python bindings) | Multi-language grammar support. Single tool for Go, Python, TypeScript, Rust, etc. |
| **Embedding model** | **OpenAI `text-embedding-3-small`** | 1536-dim vectors, known quality. API key in `.env`. |
| **Chunking strategy** | 512 tokens, 64-token overlap, file-boundary-aware | Balances retrieval granularity with embedding API call volume. |
| **Canonical model storage** | **PostgreSQL JSONB** + indexed FKs | Matches `top_level_decisions.md` §2.2. Simple recursive CTEs for depth-limited traversals. |

---

## 4. Deployment Topology

- **Process:** Bootstrap is a one-shot CLI command, not a daemon:
  ```
  python -m rasa.bootstrap --repo /path/to/target-repo --db postgres://localhost/rasa_memory
  ```
- **Dependencies:** Local PostgreSQL, PostgreSQL LISTEN/NOTIFY, `.env` with `OPENAI_API_KEY`.
- **Lifecycle:** Run once after the repo is cloned. Re-run when the target repo has significant structural changes (new module, dependency refactor).
- **Output:** Populated `canonical_model`, `soul_sheets`, `soul_baselines`, and pgvector index in PostgreSQL. Notification emitted on completion.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Embedding pipeline duration | Logged per file batch | > 30 min for pilot repo — review chunk size or API rate limits |
| AST extraction errors | Logged per file | > 5% of files — unsupported language or malformed syntax |
| Soul sheet validation failures | Logged per file | > 0 — fix YAML before proceeding |
| PostgreSQL write throughput | Monitored during ingestion | Batch writes to avoid connection pool exhaustion |
| API token consumption | Tracked total | > 1M tokens per bootstrap — optimize chunking strategy |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How long does initial embedding pipeline take for a 1M LOC repo? | **Deferred** — pilot repo is expected to be < 100K LOC. Revisit when scaling to larger repos. |
| 2 | What happens when static analysis fails (human fix loop mechanics)? | **Open:** For pilot, log the failure and continue with available data. A retry/repair loop is an upgrade. |
| 3 | Should soul sheet validation be a blocking gate for repo onboarding? | **Resolved:** Yes — invalid soul sheets block bootstrap. No agent can run without a valid soul. |
| 4 | Should the canonical model include only dependency edges, or also data-flow and call-graph edges? | **Open:** Start with dependency edges only (imports, function calls). Add data-flow edges as an upgrade. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: replaced AGE graph with JSONB canonical model, added OpenAI embedding pipeline details (chunking, model), replaced S3 with local file storage, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul sheet ingestion step to cold start sequence and baseline freezing | ? |

---

*This document implements the bootstrap contract defined in `architectural_schema_v2.1.md` §8. Storage and embedding decisions align with `memory_subsystem.md` §3 and `top_level_decisions.md` §2.2.*

