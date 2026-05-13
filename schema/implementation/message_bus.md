# Inter-Component Communication

> **Architectural Reference:** `architectural_schema_v2.1.md` §4  
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report (revised for zero extra dependencies)  
> **Owner:** TBD  
> **Last Updated:** 2026-04-28

---

## 1. Purpose

Defines how Rasa components communicate on a single-machine pilot. Replaces the JetStream message bus (enterprise architecture) with **two already-provisioned backends** — no new infrastructure needed.

---

## 2. Transport Split

| Message Type | Backend | Rationale |
|-------------|---------|-----------|
| **Durable events** (task assignment, checkpoint, sandbox result, eval record) | **PostgreSQL LISTEN/NOTIFY** + backing table | Transaction-safe, zero extra deps. Full payload in table; NOTIFY carries an ID. |
| **Ephemeral events** (heartbeats, policy updates) | **Redis Pub/Sub** | High-frequency, loss-tolerant. Next heartbeat/poll covers a missed message. |

> **Upgrade path:** When scaling beyond a single machine, introduce JetStream as a dedicated message bus. Both PostgreSQL and Redis backends can be swapped transparently behind the same `Publisher`/`Subscriber` interface.

---

## 3. PostgreSQL LISTEN/NOTIFY — Durable Events

### 3.1 How It Works

1. Producer inserts a row into a backing table (e.g., `tasks`).
2. Producer calls `NOTIFY channel_name, payload_id` — the payload is the row ID, not the full message.
3. Consumer, which has issued `LISTEN channel_name`, receives the notification.
4. Consumer fetches the full row from the backing table.
5. Consumer processes and marks the row as consumed (or moves to history).

```sql
-- Producer transaction (Orchestrator assigning a task)
BEGIN;
INSERT INTO tasks (task_id, soul_id, payload, status)
VALUES (''0195f...'', ''coder-v2-dev'', ''{...}'', ''assigned'');
NOTIFY tasks_assigned, ''0195f...'';
COMMIT;
```

```sql
-- Consumer listener (Pool Controller)
LISTEN tasks_assigned;
-- On notification: SELECT * FROM tasks WHERE task_id = ''0195f...'';
```

### 3.2 Channel Topology

| Channel | Producer | Consumer | Backing Table |
|---------|----------|----------|---------------|
| `tasks_assigned` | Orchestrator | Pool Controller | `tasks` |
| `tasks_submit` | CLI | Orchestrator | `tasks` |
| `checkpoint_saved` | Agent Runtime | Recovery Controller | `checkpoints` |
| `sandbox_result` | Sandbox Pipeline | Orchestrator | `sandbox_results` |
| `eval_record` | Evaluation Engine | Orchestrator / Observability | `evaluation_records` |
| `souls_loaded` | Bootstrap | Pool Controller | `soul_sheets` |

### 3.3 Error Handling

- **Listener disconnect:** On reconnect, re-issue `LISTEN` for all channels and replay unconsumed rows from the backing table (status != `consumed`).
- **Orphan notification:** If a notification arrives for a row that doesn't exist (producer crashed mid-transaction after NOTIFY), the consumer silently ignores it.
- **Delivery guarantee:** At-least-once. The backing table provides durability; the consumer marks rows as consumed to prevent reprocessing.

---

## 4. Redis Pub/Sub — Ephemeral Events

### 4.1 How It Works

Components publish to Redis channels. Subscribers receive the message in real-time. No persistence — if a subscriber isn't listening, the message is lost.

```python
# Publisher (Agent Runtime)
import redis.asyncio as redis
r = await redis.from_url(''redis://localhost:6379'')
await r.publish(''agents.heartbeat.agent-42'', json.dumps({
    ''agent_id'': ''agent-42'',
    ''soul_id'': ''coder-v2-dev'',
    ''current_state'': ''ACTIVE''
}))
```

```go
// Subscriber (Pool Controller)
pubsub := rdb.Subscribe(ctx, "agents.heartbeat.*")
// Redis supports glob patterns in channels
```

### 4.2 Channel Topology

| Channel | Producer | Consumer | Notes |
|---------|----------|----------|-------|
| `agents.heartbeat.{agent_id}` | Agent Runtime | Pool Controller | Glob pattern: `agents.heartbeat.*` |
| `policy.update` | Policy Engine admin | Policy Engine instances | If missed, PostgreSQL poll (30s) catches up |

### 4.3 Error Handling

- **Subscriber disconnect:** Heartbeat loss is detected by the Pool Controller after `3 × heartbeat_interval`. The agent is marked dead and the task is reassigned. No durability needed.
- **Redis restart:** All subscribers reconnect and re-subscribe to channels. Heartbeat silence timeouts will fire, but agents will recover on next heartbeat cycle.

---

## 5. Envelope Schema

Every message on either transport uses the same JSON envelope:

```json
{
  "message_id": "uuid",
  "correlation_id": "uuid",
  "source_component": "orchestrator | pool_controller | agent_runtime | ...",
  "destination_component": "orchestrator | pool_controller | agent_runtime | ...",
  "payload": {},
  "metadata": {
    "soul_id": "coder-v2-dev",
    "prompt_version_hash": "a3f2...",
    "agent_role": "CODER",
    "task_id": "0195f...",
    "agent_id": "agent-42",
    "timestamp_ms": 1745620000000
  }
}
```

The envelope is serialized to JSON for both transports. The same schema is used for PostgreSQL backing table rows and Redis Pub/Sub messages.

---

## 6. Shared Interfaces

Both Go and Python components use a common interface:

**Go (internal/messaging/):**
```go
type Publisher interface {
    Publish(ctx context.Context, channel string, msg *Envelope) error
}

type Subscriber interface {
    Subscribe(ctx context.Context, channel string, handler func(*Envelope)) error
}
```

**Python (rasa/bus/):**
```python
class Publisher:
    async def publish(self, channel: str, msg: Envelope) -> None: ...

class Subscriber:
    async def subscribe(self, channel: str, handler: Callable[[Envelope], None]) -> None: ...
```

Both backends (PostgreSQL and Redis) implement these interfaces. Components choose the right backend for each message type and never deal with raw connections.

---

## 7. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Durable messaging** | PostgreSQL 16 LISTEN/NOTIFY | Zero new dependencies. Transaction-safe. At-least-once delivery via backing tables. |
| **Ephemeral messaging** | Redis 7 Pub/Sub | Already in the stack. Sub-millisecond publish. Pattern subscriptions for heartbeats. |
| **Go client (PostgreSQL)** | `pgx` v5 with listen/notify | Mature, well-documented. `pgxpool` for connection pooling. |
| **Go client (Redis)** | `go-redis` | Standard Redis client. Supports Pub/Sub with pattern matching. |
| **Python client (PostgreSQL)** | `psycopg` v3 | Async support via `psycopg_pool`. LISTEN/NOTIFY via dedicated connection. |
| **Python client (Redis)** | `redis-py` (async) | `redis.asyncio` for async Pub/Sub. |
| **Envelope format** | JSON | Already decided in `top_level_decisions.md`. Protobuf as upgrade path. |

---

## 8. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-28 | Replaced JetStream with PostgreSQL LISTEN/NOTIFY + Redis Pub/Sub. Added shared interface patterns. Removed dead-letter policy (handled by backing table status). | Codex |
| 2026-04-25 | Initial draft: JetStream topic topology and envelope schema | ? |
