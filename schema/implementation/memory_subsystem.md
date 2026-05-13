# Memory Subsystem

> **Architectural Reference:** `architectural_schema_v2.1.md` §5  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — soul-derived context window and graph traversal   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Provides layered persistence: session store (short-term), vector index (semantic retrieval), canonical model store (JSONB), and archive/cold storage. Retrieves and formats context for agent prompt assembly based on parameters in the active soul sheet.

---

## 2. Internal Design

### 2.1 Context Assembly Pipeline

When the Agent Runtime requests context for prompt assembly (see [`agent_configuration.md`](agent_configuration.md) §3.2), the Memory Subsystem resolves:

| Variable | Soul Sheet Field | Memory Layer | Resolution |
|----------|------------------|--------------|------------|
| `memory.short_term_summary` | `memory.short_term_window` | Redis session store | Last N conversation turns, truncated to token budget |
| `memory.semantic_matches` | `memory.long_term_retrieval_k` | PostgreSQL + pgvector | Top-k semantic matches from embedding index |
| `memory.graph_excerpt` | `memory.graph_traversal_depth` | PostgreSQL (JSONB + indexed FKs) | Canonical model subgraph via recursive CTE or application-level traversal |
| `memory.archive_refs` | — | Flat files on disk | Pointers to historical sessions stored at `<data_root>/archive/{task_id>/` |

The pipeline is deterministic: the same `soul_id` + `task_id` + `session_id` always produces the same context hash, enabling LLM Gateway cache hits.

> **Upgrade note:** `memory.graph_excerpt` uses JSONB for the pilot. Migrate to pg_graph / Apache AGE when the canonical model requires multi-hop queries that exceed recursive-CTE performance.

### 2.2 Session Store Eviction

The session store (Redis, single-node) is ephemeral. Eviction policy:

- **LRU** for generic keys.
- **TTL** for session keys: `2 × behavior.session.max_idle_minutes` from the soul sheet.
- **Checkpoint promotion:** Before eviction, the session is serialized to PostgreSQL and conversation log archived to flat files at `<data_root>/archive/{task_id>/`.

### 2.3 Canonical Model Consistency

The canonical model (JSONB table `canonical_model`) is updated by three paths:

1. **Bootstrap Ingestion** — initial dependency graph and architecture facts extracted during repo onboarding.
2. **Agent Runtime** (on CHECKPOINTED) — new facts extracted from agent reasoning traces, upserted into the JSONB store.
3. **Background reconciler** — diffs canonical model state against current codebase AST every 6 hours.

**Conflict resolution:** Last-writer-wins with an audit log. The reconciler has the lowest priority and never overwrites a fact with a newer timestamp from an Agent Runtime write.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Controller language** | **Go 1.24+** | PostgreSQL LISTEN/NOTIFY subscriber, Redis client, PostgreSQL driver — single compiled binary for the memory control loop. |
| **Embedder language** | **Python 3.12+** | Ecosystem access to embedding models (sentence-transformers, OpenAI SDK). |
| **Vector index** | **PostgreSQL 16 + pgvector** | Already installed. Co-located with primary durable store. HNSW index for approximate nearest-neighbor search. |
| **Embedding model** | **OpenAI `text-embedding-3-small`** (API) | 1536-dim vectors, known quality, negligible cost at pilot scale. Local model fallback (Ollama / sentence-transformers) is a documented learning track. |
| **Session store** | **Redis** (single-node, native binary) | Sub-ms reads/writes for heartbeat state and short-term conversation turns. |
| **Canonical model** | **PostgreSQL JSONB** + indexed foreign keys | Avoids graph-extension complexity at pilot scale. Recursive CTEs handle depth-limited traversals. |
| **Blob / archive** | **Flat files on disk** (`<data_root>/archive/`) | Checkpoint snapshots, conversation logs, replay bundles. Pointers stored in PostgreSQL `checkpoint_refs` table. |

---

## 4. Deployment Topology

- **PostgreSQL 16** — Already running on localhost. Separate database `rasa_memory` for memory subsystem tables (sessions, embeddings, canonical model, checkpoints).
- **Redis** — Native process, started via Procfile:
  ```
  redis: redis-server --port 6379
  ```
- **Memory Controller** (Go) — Native binary. Subscribes to PostgreSQL LISTEN/NOTIFY channels, manages eviction and background reconciler schedules.
- **Embedder** (Python) — Called by the Memory Controller via subprocess or local HTTP. Batches embedding requests to minimize API calls.

**Data layout:**
```
<project_root>/
  data/
    archive/         # Session snapshots, replay bundles
    
```

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Redis memory usage | Set `maxmemory` to 1 GB (pilot) | > 80% of maxmemory |
| pgvector index build time | Monitor on first ingestion | > 10 minutes — downgrade to IVFFlat |
| Embedding API error rate | Logged per batch | > 5% in 1-hour window |
| Archive disk usage | Cleanup sessions older than 30 days | > 10 GB |
| Canonical model drift | Reconciler logs diff counts | > 100 unmatched facts per cycle |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How is the graph store kept consistent with code changes? | **Resolved:** Three-path mutation (bootstrap, agent, reconciler) with last-writer-wins and reconciler priority floor. |
| 2 | What is the eviction policy for the session store? | **Resolved:** LRU + TTL (2× max_idle_minutes) + checkpoint promotion before eviction. |
| 3 | Should context assembly be streamed or batched? | **Open:** Recommend batched (all-at-once) for pilot simplicity. Streaming can be introduced if prompt assembly latency exceeds 500ms p99. |
| 4 | What chunking strategy is used for code embedding (token count, overlap, file boundaries)? | **Open:** Recommend 512-token chunks with 64-token overlap, respecting file boundaries. Tune based on retrieval quality. |
| 5 | Is OpenAI text-embedding-3-small rate-limited at pilot call volume? | **Open:** Needs testing. Fallback to batch delay with exponential backoff. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: replaced AGE graph store with JSONB + FKs, replaced S3 with flat-file archive, added embedding model selection (OpenAI API), filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul-driven context assembly pipeline, eviction policy tied to soul sheet params | ? |

---

*This document implements the memory contract defined in `architectural_schema_v2.1.md` §5. Storage layout aligns with `top_level_decisions.md` §2.2.*

