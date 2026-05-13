"""Checkpoint serialization and recovery for agent sessions."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

CHECKPOINTS_DIR = Path(__file__).parent.parent.parent / "data" / "checkpoints"
REDIS_URL = os.environ.get("RASA_REDIS_URL", "redis://localhost:6379")


def _pg_dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_orch"


def _redis_key(task_id: str) -> str:
    return f"checkpoint:{task_id}"


def save_checkpoint(
    task_id: str,
    agent_id: str,
    messages: list[dict[str, Any]],
    memory_context: dict[str, Any],
    current_state: str,
    turn: int,
    model: str | None = None,
    ttl: int = 600,
) -> str:
    """Save a checkpoint to Redis, PostgreSQL, and flat file. Returns checkpoint UUID."""
    checkpoint_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    snapshot = {
        "checkpoint_id": checkpoint_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "saved_at": now,
        "state": current_state,
        "turn": turn,
        "model": model,
        "messages": messages,
        "memory_context": memory_context,
    }

    # Redis hot-path (fastest recovery)
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        r.set(_redis_key(task_id), json.dumps(snapshot, default=str), ex=ttl)
        r.close()
    except Exception as exc:
        print(f"[checkpoint] Redis write failed: {exc}", flush=True)

    # Flat file
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = CHECKPOINTS_DIR / f"{task_id}.json"
    file_path.write_text(json.dumps(snapshot, indent=2, default=str))

    # PostgreSQL (durable)
    try:
        with psycopg.connect(_pg_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO checkpoint_refs (id, task_id, agent_id, snapshot_path, metadata)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         agent_id = EXCLUDED.agent_id,
                         snapshot_path = EXCLUDED.snapshot_path,
                         metadata = EXCLUDED.metadata""",
                    (
                        checkpoint_id,
                        task_id,
                        agent_id,
                        str(file_path),
                        json.dumps({"turn": turn, "state": current_state, "saved_at": now}),
                    ),
                )
            conn.commit()
    except Exception as exc:
        print(f"[checkpoint] DB write failed (file saved): {exc}", flush=True)

    return checkpoint_id


def load_checkpoint(task_id: str) -> dict[str, Any] | None:
    """Load the latest checkpoint for a task. Tries Redis -> flat file -> PostgreSQL."""
    # Try Redis hot-path first (fastest)
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        raw = r.get(_redis_key(task_id))
        r.close()
        if raw:
            return json.loads(raw)
    except Exception:
        pass

    # Try flat file
    file_path = CHECKPOINTS_DIR / f"{task_id}.json"
    if file_path.exists():
        try:
            return json.loads(file_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to PostgreSQL
    try:
        with psycopg.connect(_pg_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT snapshot_path, metadata FROM checkpoint_refs
                       WHERE task_id = %s ORDER BY created_at DESC LIMIT 1""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    pg_path = Path(row[0])
                    if pg_path.exists():
                        return json.loads(pg_path.read_text())
    except Exception:
        pass

    return None


def delete_checkpoint(task_id: str) -> None:
    """Remove checkpoint from all stores after task terminal state."""
    # Redis
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        r.delete(_redis_key(task_id))
        r.close()
    except Exception:
        pass

    # Flat file
    file_path = CHECKPOINTS_DIR / f"{task_id}.json"
    file_path.unlink(missing_ok=True)

    # PostgreSQL
    try:
        with psycopg.connect(_pg_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM checkpoint_refs WHERE task_id = %s", (task_id,))
            conn.commit()
    except Exception:
        pass
