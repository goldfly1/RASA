# Architectural Schema: Agentic AI Coding Team

> **Status:** Draft v2.1 — formatting and interface boundary fixes applied; open for implementation planning  
> **Scope:** Computational and data architecture derived from `strategic_considerations_ver2.md`. Does not include non-technical governance (see Addendum).

---

## 1. System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                        Orchestrator                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │  Planner │  │  Coder   │  │ Reviewer │  │Architect │        │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
│       └──────────────┴─────────────┴──────────────┘              │
│                           │                                     │
│                    Message Bus                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
         ┌──────────────────┼───────────────────┐
         │                  │                   │
    ┌────┴────┐       ┌─────┴─────┐      ┌─────┴──────┐
    │   LLM   │       │   State   │      │   Memory   │
    │ Gateway │       │  Store    │      │   / RAG    │
    └────┬────┘       └─────┬─────┘      └────┬──────┘
         │                  │                   │
    ┌────┴────┐       ┌──────┴──────┐     ┌─────┴─────┐
    │ Sandbox │       │  Checkpoints│     │ Vector DB │
    │   / VM  │       │    & Locks  │     │           │
    └─────────┘       └─────────────┘     └───────────┘
          ▲
          │
┌─────────┴────────────────────────────────────────────────────────┐
│                   Recovery Controller                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Idempotency  │  │ Partial-Write│  │   Restart    │          │
│  │   Ledger     │  │   Detector   │  │   Hygiene    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              Agent Pool & Backpressure Controller                  │
│         (Assignment, Scale Signals, Budget/Model Routing)       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   Evaluation Engine                             │
│         (Gates + Traces → Records → Benchmarks / Alerts)        │
└─────────────────────────────────────────────────────────────────┘
```

> **Note on presentation:** The boxes above are architectural subsystems. Implementation details (e.g., whether a subsystem runs as a single process, a sidecar, or a distributed service) are intentionally left unspecified and will be resolved during implementation planning against this schema.

---

## 2. Entity Model

### 2.1 Agent
The unit of computation. Every agent is a stateful process with a defined role.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `agent_id` | UUID | 1 | Globally unique identifier. |
| `name` | string | 1 | Human-readable identifier (e.g., `coder-alpha`). |
| `role` | enum | 1 | `PLANNER`, `CODER`, `REVIEWER`, `ARCHITECT`, `ORCHESTRATOR`. |
| `permission_tier` | enum | 1 | `READ_ONLY`, `WRITE`, `DEPLOY`, `ADMIN`. |
| `model_request` | ModelRequest | 1 | Desired model(s) and fallback chain for inference. |
| `session_state` | SessionState | 0..1 | Ephemeral context for the current run. |
| `tool_bindings` | ToolBinding[] | 0..n | List of tools (by `tool_id`) this agent may invoke. |
| `created_at` | timestamp | 1 | Instant of agent instantiation. |
| `last_heartbeat` | timestamp | 1 | Last observed activity; triggers timeout logic. |

### 2.2 Task
The atomic unit of work. Tasks are nodes in a decomposition graph.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `task_id` | UUID | 1 | Globally unique. |
| `parent_id` | UUID | 0..1 | Reference to decomposing task; null for root. |
| `agent_id` | UUID | 0..1 | Agent currently assigned; null when unassigned. |
| `status` | enum | 1 | See §3 Task Lifecycle. |
| `role_target` | enum | 1 | Required role to execute (`CODER`, `REVIEWER`, etc.). |
| `input_artifacts` | ArtifactRef[] | 0..n | URIs / hashes of files or data required. |
| `output_artifacts` | ArtifactRef[] | 0..n | Artifacts produced on completion. |
| `interface_contract` | Contract | 0..1 | Expected I/O schema for this task. |
| `verification_gates` | VerificationGate[] | 0..n | Required checks (test, lint, type-check). |
| `budget` | Budget | 0..1 | Token / time / retry limits for this task. |
| `deadline` | timestamp | 0..1 | Absolute timeout for the task. |
| `checkpoint_id` | UUID | 0..1 | Checkpoint snapshot taken at start of processing. |
| `created_at` | timestamp | 1 | Enqueue time. |
| `started_at` | timestamp | 0..1 | Time picked up by an agent. |
| `completed_at` | timestamp | 0..1 | Terminal time (success, failure, or escalation). |

### 2.3 Message
Inter-agent communication payload. All communication is asynchronous via the Message Bus.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `message_id` | UUID | 1 | Unique message identifier. |
| `thread_id` | UUID | 1 | Conversation / negotiation thread identifier. |
| `sender_id` | UUID | 1 | Originating agent. |
| `recipient_id` | UUID | 0..1 | Target agent; null for broadcast / topic. |
| `message_type` | enum | 1 | `REQUEST`, `RESPONSE`, `REJECT`, `ESCALATE`, `HEARTBEAT`, `CHECKPOINT`. |
| `intent` | string | 1 | Semantic goal (what the sender wants). |
| `action` | ToolCall | 0..1 | Concrete tool invocation (what the sender does). |
| `payload` | JSON | 1 | Polymorphic payload validated against schema per `message_type`. |
| `context_refs` | ContextPointer[] | 0..n | Pointers to relevant session state, memory, or artifacts. |
| `timestamp` | timestamp | 1 | Send time. |
| `ttl` | seconds | 0..1 | Time-to-live; message is dead-lettered after expiry. |

### 2.4 Artifact
Any persistent file or data object produced or consumed by the system.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `artifact_id` | UUID | 1 | Unique identifier. |
| `uri` | string | 1 | Storage location (object-store path, git blob ref, sandbox path). |
| `kind` | enum | 1 | `SOURCE_FILE`, `DIFF`, `TEST_RESULT`, `BUILD_OUTPUT`, `LOG`, `EMBEDDING`. |
| `hash` | string (SHA-256) | 1 | Integrity hash; used in diff logging and rollback. |
| `provenance` | Provenance | 1 | Agent, task, and timestamp of creation. |
| `retention_policy` | enum | 0..1 | `EPHEMERAL`, `SESSION`, `LONG_TERM`. |
| `metadata` | JSON | 0..1 | Kind-specific metadata (loc, coverage percentage, etc.). |

### 2.5 SessionState
Ephemeral context for a single agent run. Survives tool calls but not crashes.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `session_id` | UUID | 1 | Unique per run. |
| `agent_id` | UUID | 1 | Owning agent. |
| `root_task_id` | UUID | 1 | Task that initiated this session. |
| `conversation_log` | Message[] | 0..n | Ordered history of messages in this session (truncated/summarized). |
| `working_memory` | JSON | 0..1 | Key-value scratch pad for intermediate results. |
| `open_files` | FileBuffer[] | 0..n | In-memory file snapshots the agent is currently editing. |
| `tool_results` | ToolResult[] | 0..n | Cached results from this session to avoid redundant calls. |
| `budget_remaining` | Budget | 0..1 | Live counter of tokens, time, retries left. |
| `created_at` | timestamp | 1 | Session start. |
| `expires_at` | timestamp | 1 | Automatic eviction time. |

### 2.6 Budget
Spending guardrail attached to a task, session, or agent pool.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `budget_id` | UUID | 1 | Unique identifier. |
| `scope` | enum | 1 | `TASK`, `SESSION`, `AGENT`, `ORGANIZATION`, `MONTHLY`. |
| `resource_type` | enum | 1 | `TOKENS`, `API_CALLS`, `WALL_TIME`, `RETRIES`, `COMPUTE_SECONDS`. |
| `limit` | int64 | 1 | Hard ceiling. |
| `consumed` | int64 | 1 | Running tally. |
| `action_on_exceeded` | enum | 1 | `ESCALATE`, `HALT`, `DEGRADE_MODEL`, `REJECT`. |

### 2.7 Checkpoint
Recoverable snapshot of orchestrator and agent state.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `checkpoint_id` | UUID | 1 | Unique identifier. |
| `task_id` | UUID | 1 | Task whose execution this snapshot captures. |
| `agent_states` | AgentSnapshot[] | 0..n | Serialized `SessionState`s of participating agents. |
| `task_graph_state` | JSON | 1 | Full task graph at moment of snapshot (statuses, assignments). |
| `artifact_manifest` | ArtifactRef[] | 1 | All artifacts known to exist at this point. |
| `bus_watermark` | string | 1 | Message-bus offset / watermark; replay starts here. |
| `hash` | string (SHA-256) | 1 | Integrity over the entire checkpoint bundle. |
| `created_at` | timestamp | 1 | Snapshot time. |

### 2.8 MemoryRecord
Long-term knowledge stored in the vector/graph layer.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `record_id` | UUID | 1 | Unique identifier. |
| `kind` | enum | 1 | `EPISODE`, `FACT`, `CONTRACT`, `PATTERN`. |
| `embedding` | vector | 0..1 | Dense vector for semantic retrieval. |
| `content` | text | 1 | Raw text or structured JSON content. |
| `scope` | enum | 1 | `PROJECT`, `AGENT`, `TASK`. |
| `tags` | string[] | 0..n | Faceted labels for filtered retrieval. |
| `created_at` | timestamp | 1 | Record creation. |
| `last_accessed` | timestamp | 1 | LRU prioritization for summarization. |
| `confidence` | float [0–1] | 1 | Source reliability (model confidence or human rating). |

### 2.9 ToolCall
Concrete invocation emitted by an agent and routed to a tool provider.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `tool_call_id` | UUID | 1 | Unique per invocation. |
| `tool_id` | UUID | 1 | Tool being invoked. |
| `operation` | string | 1 | Named operation on the tool. |
| `arguments` | JSON | 1 | Bound parameters. |
| `idempotency_key` | UUID | 1 | Deterministic key for replay deduplication. |
| `agent_id` | UUID | 1 | Originating agent. |
| `timestamp` | timestamp | 1 | Emission time. |

### 2.10 ToolResult
Outcome of a tool invocation, cached in session or forwarded to the bus.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `tool_call_id` | UUID | 1 | References the invocation. |
| `status` | enum | 1 | `SUCCESS`, `ERROR`, `TIMEOUT`, `REJECTED`. |
| `payload` | JSON | 0..1 | Structured result. |
| `raw_output` | text | 0..1 | Unstructured stdout / stderr. |
| `latency_ms` | int | 1 | Wall time. |
| `timestamp` | timestamp | 1 | Completion time. |

### 2.11 Contract
Schema that governs inputs and outputs of a task.

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `contract_id` | UUID | 1 | Unique identifier. |
| `schema_in` | JSON (JSON Schema) | 1 | Expected input shape. |
| `schema_out` | JSON (JSON Schema) | 1 | Expected output shape. |
| `canonical_ref` | UUID | 0..1 | Link to `CanonicalModel` if this contract is project-wide. |

---

## 3. State Machines

### 3.1 Task Lifecycle

```
                         ┌─────────────────┐
      ┌─────────────────►│    PENDING      │◄──────────────────┐
      │    unassigned    │  (queued)       │                   │
      │                  └────────┬────────┘                   │
      │                           │ assign                     │
      │                           ▼                            │
      │                  ┌─────────────────┐                    │
      │    fail gate    │   ASSIGNED      │──► checkpoint      │
      │◄────────────────│  (agent picked) │                   │
      │                 └────────┬────────┘                   │
      │                          │ execute                    │
      │                          ▼                            │
      │                 ┌─────────────────┐                   │
      │    reject       │  IN_PROGRESS    │                   │
      │◄────────────────│  (working)      │                   │
      │                 └────────┬────────┘                   │
      │                          │ submit                     │
      │                          ▼                            │
      │                 ┌─────────────────┐                   │
      │◄───────────────►│ VERIFICATION    │──► retry limit    │
      │   pass/fail     │  (gates running)│    exceeded       │
      │                 └────────┬────────┘                   │
      │                          │ all pass                   │
      │                          ▼                            │
      │                 ┌─────────────────┐                   │
      │                 │    COMPLETE     │───────────────────┘
      │                 └─────────────────┘    downstream enqueue
      │
      │   fail irreversible / timeout / budget / anomaly
      ▼
┌─────────────────┐
│    ESCALATED    │──► Human Handoff Queue (§4.2)
│ (needs human)   │
└─────────────────┘
```

**Transitions:**
| From | To | Trigger | Guard |
|------|----|---------|-------|
| `PENDING` | `ASSIGNED` | Orchestrator assignment | Agent with matching `role_target` and available budget |
| `ASSIGNED` | `IN_PROGRESS` | Agent start signal | Agent heartbeat OK, sandbox ready |
| `IN_PROGRESS` | `VERIFICATION` | Agent submits artifacts | At least one artifact produced |
| `VERIFICATION` | `COMPLETE` | All gates pass | Test, lint, type-check return zero exit |
| `VERIFICATION` | `IN_PROGRESS` | Any gate fails | Retry count < budget limit |
| `VERIFICATION` | `ESCALATED` | Retry limit hit OR anomaly detected | Kill switch fired |
| `*` | `ESCALATED` | Budget exceeded / timeout / circuit breaker | — |

### 3.2 Agent Session Lifecycle

```
IDLE ──► WARMING ──► ACTIVE ──► PAUSED ──► RESUMING ──► ACTIVE
            │          │           ▲                      │
            │          │           └──── soft timeout     │
            │          │                                   │
            │          └──► CHECKPOINTED ──► RECOVERING ──┘
            │               (on heartbeat loss)            │
            └──────────────────────────────────────────────┘
                        graceful drain / shutdown
```

---

## 4. Protocol Definitions

### 4.1 Inter-Agent Message Schema (JSON envelope)

```json
{
  "schema_version": "1.0",
  "message_id": "uuid",
  "thread_id": "uuid",
  "sender_id": "uuid",
  "recipient_id": "uuid | null",
  "message_type": "REQUEST | RESPONSE | REJECT | ESCALATE | HEARTBEAT | CHECKPOINT",
  "intent": "string (semantic goal)",
  "action": {
    "tool_id": "uuid",
    "operation": "string",
    "arguments": {},
    "idempotency_key": "uuid"
  },
  "payload": {},
  "context_refs": [
    {"type": "SESSION | MEMORY | ARTIFACT", "ref": "uuid"}
  ],
  "timestamp": "ISO-8601",
  "ttl": 300
}
```

### 4.2 Human Handoff Queue

The Human Handoff Queue is implemented as the `human_reviews` table in the `rasa_policy` database. The orchestrator uses two dedicated tools:
- `request_human_input(reason, payload?)` — creates a `pending` review record, returns review ID
- `check_human_response(review_id)` — polls for status change to `answered`, returns guidance text

The dashboard polls `GET /api/reviews` and submits responses via `POST /api/reviews/{id}/respond`. A 10-second auto-refresh in the NiceGUI panel keeps the review queue current without WebSockets.

When a task hits `ESCALATED`, the following structure is enqueued:

```json
{
  "handoff_id": "uuid",
  "task_id": "uuid",
  "escalation_reason": "RETRY_EXHAUSTED | BUDGET_BREACH | ANOMALY | CONFLICT_DEADLOCK | HUMAN_REQUEST",
  "agent_trace_uri": "uri",       // Full reasoning trace
  "proposed_diff_uri": "uri",     // ArtifactRef to diff if applicable
  "context_summary": "string",    // LLM-generated summary of situation
  "blocking": true,               // Halts downstream dependent tasks
  "human_actions": ["APPROVE", "REJECT", "REWRITE", "REASSIGN"],
  "enqueued_at": "ISO-8601",
  "sla_deadline": "ISO-8601"      // Max time before auto-reject or page
}
```

### 4.3 Conflict Resolution & Arbitration

Agent-to-agent negotiation is bounded. If a `REJECT` message is issued in response to a `REQUEST`, the thread enters a negotiation loop with a maximum of **3 rounds**. If no `RESOLVED` message is produced, the Orchestrator automatically transitions the associated `Task` to `ESCALATED` and enqueues it to the **Human Handoff Queue (§4.2)**. The human serves as the final arbiter; no agent role overrides a human decision.

### 4.4 Review Queue State Machine

The human_reviews table follows a simple two-state lifecycle:

```
                     ┌──────────────┐
  orchestrator ─────►│   PENDING    │◄──── dashboard polls GET /api/reviews
  calls              │   (waiting)  │
  request_human_     └──────┬───────┘
  input()                    │ user responds via dashboard
                             ▼
                     ┌──────────────┐
                     │   ANSWERED   │
                     │              │────► orchestrator picks up via
                     └──────────────┘      check_human_response()
```

**Transitions:**
| From | To | Trigger | Notes |
|------|----|---------|-------|
| `pending` | `answered` | User submits via dashboard | Idempotent guard: `WHERE status='pending'` |

**Implementation:** `rasa/orchestrator/reviews.py` — `ReviewManager` class with sync psycopg methods. Dashboard at `rasa/gui_nice/reviews_panel.py` polls every 10s. API routes at `GET /api/reviews` and `POST /api/reviews/{id}/respond`.

---

## 5. Memory Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Retrieval Surface                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Short-Term   │  │ Semantic     │  │ Episodic /           │  │
│  │ Session Store│  │ Vector Index │  │ Graph Store (facts)  │  │
│  │   (Redis)    │  │  (pgvector)  │  │   (Neo4j / RDF)      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        │      Summarization      │
        │   & Eviction Layer      │
        └───────────┬───────────┘
                    ▼
         ┌──────────────────┐
         │ Archive / Cold   │
         │ Storage (object)   │
         └──────────────────┘
```

**Access Patterns:**
| Layer | Latency | Durability | Access Method |
|-------|---------|------------|---------------|
| Session | < 10 ms | Session-only | Key-value by `session_id` |
| Vector | < 100 ms | Persistent | k-NN by embedding + tag filters |
| Graph | < 50 ms | Persistent | Traversal queries (contracts, deps) |
| Archive | seconds | Permanent | Batch replay only |

**Canonical Model Enforcement:**
A separate `CanonicalModel` record type in the graph store holds project-wide contracts, style rules, and architecture decisions. Agents must read the current canonical model before writing to any file governed by it. The Orchestrator rejects diffs that violate canonical constraints.

**Canonical Model API:**
- **Writes:** Constrained to `ARCHITECT`-role agents via a governed endpoint.
- **Reads:** Required before any `CODER` emits a diff on a governed surface.
- **Validation:** The Orchestrator checks proposed diffs against canonical constraints prior to host application.

---

## 6. Safety & Guardrails

### 6.1 Sandbox Execution Pipeline
```
Agent Output ──► Static Scanner ──► Secret/PII Detector ──► Sandbox (read-only FS clone)
                                               │                        │
                                               ▼                        ▼
                                          Reject / Sanitize      Build + Test + Lint
                                                                        │
                                                                        ▼
                                                                 Gate Pass?
                                                               Yes /    \ No
                                                                │          │
                                                                ▼          ▼
                                                           Diff Artifact
                                                           Persisted
                                                                │
                                                                ▼
                                                          Host Apply   Diff Rejected
```

> **Artifact persistence guarantee:** Before `Host Apply`, the diff is snapshotted as an `Artifact` of kind `DIFF` with a SHA-256 hash. Recovery rewinds to this artifact, not to Sandbox ephemeral state.

### 6.2 Permission Matrix (Role × Action)

| Action | Planner | Coder | Reviewer | Architect | Orchestrator |
|--------|---------|-------|----------|-----------|--------------|
| Read file | ✓ | ✓ | ✓ | ✓ | ✓ |
| Write file (non-prod) | ✗ | ✓ | ✗ | ✓ | ✗ |
| Write file (protected*) | ✗ | ✗ | ✗ | ✓ | ✗ |
| Propose diff | ✓ | ✓ | ✓ | ✓ | ✓ |
| Approve diff to host | ✗ | ✗ | ✓ | ✓ | ✗ |
| Merge to main | ✗ | ✗ | ✗ | ✗ | ✓¹ |
| Delete file | ✗ | ✗ | ✗ | ✗ | ✗ |
| Kill agent session | ✗ | ✗ | ✗ | ✓ | ✓ |
| Modify CI/CD | ✗ | ✗ | ✗ | ✓ | ✗ |

> \* Protected = files listed in `.agent_protected_paths`; includes CI configs, credential files, and infrastructure definitions.  
> ¹ Orchestrator merge to main only after human gate or two-reviewer quorum.

---

## 7. Observability Schema

### 7.1 Reasoning Trace Record

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | UUID | Correlates full agent step sequence. |
| `step_number` | int | Monotonic within trace. |
| `thought` | text | Agent reasoning (LLM output). |
| `action` | ToolCall | Tool chosen. |
| `observation` | ToolResult | Raw result. |
| `latency_ms` | int | Wall time for this step. |
| `tokens_in` / `tokens_out` | int | LLM token counts. |
| `timestamp` | timestamp | Step completion. |

### 7.2 Replay Bundle
A deterministic replay requires:
1. `Checkpoint` (state at start)
2. Ordered `Message[]` from bus watermark onward
3. Seeded RNG state (if LLM gateway supports deterministic sampling)
4. Immutable artifact store (read-only)

---

## 8. Bootstrap & Ingestion Flow

### 8.1 Repository Bootstrap

```
New Repository ──► Git Clone ──► Dependency Graph Extractor
                                      │
                                      ▼
                              ┌─────────────────┐
                              │  Static Analyzer│
                              │  (build, test)  │
                              └────────┬────────┘
                                       │ pass?
                              ┌────────┴────────┐
                              ▼                 ▼
                     ┌──────────────┐    │ Human Fix Loop
                     │  Baseline    │    │
                     │  Freeze      │    │
                     └──────┬───────┘    │
                            │            │
              ┌─────────────┼────────────┘
              ▼             ▼
       ┌──────────┐  ┌──────────────┐
       │ Code Embedding │  │ Canonical Model│
       │   Pipeline     │  │   Extractor    │
       └────┬─────┘  └──────────────┘
            │
            ▼
       ┌──────────┐
       │ Vector DB│
       │  Seeded  │
       └──────────┘
```

### 8.2 Lore Ingestion Pipeline

Documentation files (`.md`, `.yaml`, `.rst`) are ingested into the lore knowledge base via `scripts/ingest_lore.py`:

```
Source Files ──► Chunk by ## headings or by size ──► canonical_nodes table
                                                            │
                                                            ▼
                                                   embeddings table (pgvector)
                                                   via nomic-embed-text
```

- **Node types:** `manual` (file-level parent), `manual_section` (heading-level children with `outgoing_edges` linking parent→children)
- **Chunking:** Markdown files split on `##` headings; other files split at 6000-char boundaries
- **Embeddings:** Batch of 10, truncated to 4000 chars for nomic-embed-text's 2048-token context window
- **Idempotency:** `ON CONFLICT (name, node_type) DO NOTHING` on nodes; unique index on `(node_id, model, chunk_index)` for embeddings
- **Usage:** `python scripts/ingest_lore.py --all --embed --embed-model nomic-embed-text`
- **Storage:** `rasa_memory` database: `canonical_nodes` table (graph nodes with JSONB body) + `embeddings` table (vector(1536) with HNSW index)

---

## 9. Interface Boundaries (Component Contracts)

| Component | Input Contract | Output Contract | Failure Mode |
|-----------|---------------|---------------|--------------|
| **Orchestrator** | Task DAG + resource state | Agent assignments + checkpoint triggers | Deadlocks on cyclic deps; degrade to serial execution |
| **LLM Gateway** | ModelRequest + prompt + budget | Completion + token count + finish reason | Fallback to next model in chain; ultimate fail → escalate |
| **Message Bus** | Envelope JSON | ACK / NACK + watermark | Retry 3times; then dead-letter to `ESCALATED` queue |
| **Sandbox** | Artifact bundle + build command | Exit code + stdout/stderr + artifact diff | Timeout → kill signal; non-zero → reject diff |
| **Policy Engine** | Agent + requested action | Allow / Deny / Require Review | Deny overrides agent; logged for audit |
| **Memory / RAG** | Query vector + filters | Ranked MemoryRecord[] | Empty result → agent falls back to baseline docs |
| **State Store** | Checkpoint or SessionState write | Hash-verified persistence | Write failure → halt affected tasks, alert |
| **Recovery Controller** | `Checkpoint` ID + replay intent | Restored `SessionState[]` + task graph | Checksum mismatch → dead-letter to `ESCALATED` |
| **Pool Controller** | Task demand + role + budget + model availability | Agent assignment / `QUEUED` / `REJECTED` | Overload → backpressure (rate-limit or degrade tier) |
| **Evaluation Engine** | `Task` completion events + `ReasoningTrace[]` | `EvaluationRecord` + trend alerts | Store unavailable → in-memory buffer with TTL; alert if buffer exceeds threshold |

---

## 10. Version Control Integration

- **Branch naming:** `agent/<task_id>/<agent_name>`
- **Commit attribution:** `Author: agent-name <agent-id@system.local>`; DCO sign-off required.
- **Merge policy:**
  - Fast-forward only if diff < 200 lines and all gates pass.
  - Squash merge for multi-commit agent branches.
  - Never push directly to `main`; always PR.
- **Diff logging:** Every proposed change is persisted as an `Artifact` of kind `DIFF` before host filesystem modification.

---

## 11. Recovery & Resumption Subsystem

### 11.1 Component Context

The Recovery Subsystem owns crash recovery, deterministic replay, and restart hygiene. It interacts with the Orchestrator, State Store, and Sandbox.

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│   Orchestrator      │◄───►│  Recovery           │◄───►│   State Store       │
│                     │     │  Controller         │     │  (Checkpoints)      │
└─────────────────────┘     └──────────┬──────────┘     └─────────────────────┘
                                       │
                      ┌──────────────────┴──────────────────┐
                      ▼                                       ▼
            ┌───────────────────┐                 ┌───────────────────┐
            │   Sandbox         │                 │   Artifact        │
            │   Controller      │                 │   Store           │
            └───────────────────┘                 └───────────────────┘
```

### 11.2 Interface Contracts

| Component | Input Contract | Output Contract | Failure Mode |
|-----------|---------------|---------------|--------------|
| **Recovery Controller** | `Checkpoint` ID + replay intent | Restored `SessionState[]` + task graph | Checksum mismatch → dead-letter to `ESCALATED` |
| **Idempotency Ledger** | `ToolCall` hash (`agent_id` + `tool_id` + `idempotency_key` + argument hash) | `SEEN` (short-circuit) or `NEW` (proceed) | Ledger unavailability → proceed with warning, flag for audit |
| **Partial-Write Detector** | Agent-declared artifact manifest vs. post-Sandbox manifest | `MATCH` or `ROLLBACK_TO_CHECKPOINT` | Detector failure → conservative rollback |
| **Restart Hygiene Controller** | Orchestrator warm-start signal | Drained stale agents, reaped orphaned sandboxes, dead-lettered in-flight tasks | Incomplete drain → alert, block new assignments until resolved |

### 11.3 Kill Switch Transport

The Kill Switch is a control-plane signal emitted by the Recovery Controller and consumed by the Orchestrator. It is **not** a bus message; it uses a dedicated side-channel to ensure delivery even when the Message Bus is saturated or partitioned. If the side-channel is unavailable, the signal falls back to message-bus broadcast or host-level process signaling.

| Signal | Payload | Consumer | Effect |
|--------|---------|----------|--------|
| `KILL_AGENT_SESSION` | `agent_id` + `reason` + `checkpoint_id` | Orchestrator → Agent | Immediate graceful drain; session state snapshotted if possible. |
| `KILL_SWITCH_GLOBAL` | `reason` + `timestamp` | Orchestrator (broadcast) | Halt new assignments; pause in-flight verifications; enqueue all active tasks to `ESCALATED`. |
| `SIDE_CHANNEL_FAILURE` | `reason` + `timestamp` | Orchestrator + Bus | Fallback to message-bus broadcast or direct host process signal delivery; alert admin. |

---

## 12. Agent Pool & Backpressure Controller

### 12.1 Component Context

Sits between the Orchestrator and the pool of warm agent processes. Throttles demand based on budget, concurrency ceilings, and model availability.

```
┌─────────────┐     ┌─────────────────────────────┐     ┌────────────────┐
│ Orchestrator│────►│  Agent Pool & Backpressure │────►│  Warm Agent    │
│  (demand)   │     │  Controller                │     │  Pool          │
└─────────────┘     └─────────────┬───────────────┘     └────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
          ┌──────────────┐        ┌──────────────┐
          │   Budget     │        │   Model      │
          │   Monitor    │        │   Router     │
          └──────────────┘        └──────────────┘
```

### 12.2 Interface Contracts

| Component | Input Contract | Output Contract | Failure Mode |
|-----------|---------------|---------------|--------------|
| **Pool Controller** | Task demand + agent role requirements + budget state + model availability | Agent assignment / `QUEUED` / `REJECTED` (backpressure) | Overload → rate-limit incoming tasks or degrade model tier |
| **Warm Agent Pool** | Orchestrator scale signal | Pre-initialized `SessionState` | Cold start latency spike → queue tasks with SLA buffer |

### 12.3 Backpressure Policies

- **Concurrency Ceiling:** Max `N` agents of a given role may run concurrently.
- **Budget Backpressure:** If organization-level budget consumed > 80%, reject all non-critical tasks.
- **Model Exhaustion:** If requested model tier unavailable, degrade to next tier with audit trail.

---

## 13. Evaluation Engine

### 13.1 Component Context

Consumes `Task` gate results and `ReasoningTrace` records to compute KPIs, benchmark scores, and drift signals.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Task Lifecycle │────►│   Evaluation    │────►│   Metrics       │
│  (gate results) │     │   Engine        │     │   Store         │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                     ┌───────────┴───────────┐
                     ▼                       ▼
           ┌──────────────┐        ┌──────────────┐
           │  Benchmark   │        │   Drift      │
           │  Suite       │        │  Detector    │
           └──────────────┘        └──────────────┘
```

### 13.2 Entity: EvaluationRecord

| Field | Type | Cardinality | Description |
|-------|------|-------------|-------------|
| `eval_id` | UUID | 1 | Unique identifier. |
| `agent_id` | UUID | 1 | Agent under evaluation. |
| `task_id` | UUID | 1 | Task evaluated. |
| `task_type` | enum | 1 | `REFACTOR`, `FEATURE`, `BUGFIX`. |
| `model_id` | string | 1 | Model that served the request. |
| `gate_results` | JSON | 1 | `{test, lint, type_check}` results. |
| `score` | float [0–1] | 0..1 | Composite quality score from human-review sampling. |
| `cycle_time_ms` | int | 1 | Wall time from `ASSIGNED` to `COMPLETE`. |
| `tokens_consumed` | int | 1 | Budget consumed. |

### 13.3 Interface Contracts

| Component | Input Contract | Output Contract | Failure Mode |
|-----------|---------------|---------------|--------------|
| **Evaluation Engine** | `Task` completion events + `ReasoningTrace[]` | `EvaluationRecord` + trend alerts | Metrics store unavailable → buffer in-memory with TTL, alert if buffer exceeds threshold |
| **Drift Detector** | Rolling window of `EvaluationRecord`s | `ALERT` if pass-rate drops below threshold or latency spikes | False positive → human review sampling |
| **Benchmark Suite** | New model or prompt version candidate | Benchmark scorecard vs. baseline | Regression → block promotion of candidate |

---

---

## 14. Operational Tooling

### 14.1 Desktop Launchers

The project provides double-click `.bat` launchers in `launchers/` and desktop shortcuts for all common operations:

| Launcher | Command | Purpose |
|----------|---------|---------|
| `start_all.bat` | `honcho start` | All services (API :8400, dashboard :8401, pool controller, agents) |
| `start_dashboard.bat` | `python -m rasa.gui_nice` | NiceGUI web dashboard |
| `start_api.bat` | `python -m rasa.gui.server` | API server |
| `start_pool.bat` | `python -m rasa.pool.controller` | Pool controller (agent task dispatch) |
| `start_heartbeat.bat` | `scripts/heartbeat_monitor.py --loop` | Self-healing monitor |
| `db_shell.bat` | `psql -U postgres` | PostgreSQL shell |
| `ingest_lore.bat` | `scripts/ingest_lore.py --all --embed` | Re-seed documentation into lore store |
| `run_tests.bat` | `pytest tests/ -v` | Test suite |

### 14.2 Heartbeat Self-Healing Monitor

`scripts/heartbeat_monitor.py` runs a continuous health check loop that restarts dead services:

- **Poll interval:** 30 seconds (configurable via `--interval`)
- **Scope:** All services in the registry that have `can_start=True` and `is_external=False`
- **Cooldown:** 60-second minimum between restart attempts per service to prevent restart loops
- **Mechanism:** Uses the same `HealthChecker` and `ProcessManager` classes as the GUI server
- **Logging:** `logs/heartbeat.log` with timestamps
- **Modes:** `--loop` for continuous operation, default one-shot for scheduled task use

### 14.3 Auto-Interview on Project Creation

When a new project is created via the API or dashboard, the server auto-dispatchs a planner task:

1. `POST /api/orchestrator/projects` creates the project record
2. `TaskDelegator.create_task(soul_id="planner-v1")` creates a planning task
3. `TaskDelegator.assign_task()` sets status to `ASSIGNED` and sends PG NOTIFY on `tasks_assigned` channel
4. Pool controller receives the notification and spawns `rasa.agent.dispatcher --soul planner-v1 --task-id <id> --one-shot`
5. Planner agent produces a structured plan decomposed into actionable tasks

The interactive interview capability (back-and-forth with user via `request_human_input`) requires the agent runtime to support function-calling tool execution — a future enhancement.

| # | Item | Status | Owner |
|---|------|--------|-------|
| 1 | Prompt governance schema (instruction versioning, A/B test harness) | **Out of scope** | — |
| 2 | Cost allocation / chargeback schema | TBD | — |
| 3 | Multi-region / fault-tolerant orchestrator replication | TBD | — |
| 4 | Plugin ABI for external tool providers | TBD | — |
| 5 | Runtime dynamic model routing (tier switching mid-session) | TBD | — |
| 6 | Recovery / Pool controller explicit state-machine definitions | **Addressed in implementation schema** | — |
| 7 | Human-in-the-loop review channel (§4.2, §4.4) | **Addressed** | — |
| 8 | Lore ingestion pipeline (§8.2) | **Addressed** | — |
| 9 | Operational tooling: desktop launchers, heartbeat monitor, auto-interview (§14) | **Addressed** | — |

---

*End of Architectural Schema v2.1*
