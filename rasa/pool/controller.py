"""Pool controller — receives task assignments and routes them to daemon agents
or one-shot dispatchers. Tracks live agents via Redis heartbeats.

Usage:
  python -m rasa.pool.controller --pool-file config/pool.yaml
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import psycopg
import yaml

from rasa.bus import Envelope, Metadata, RedisSubscriber

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "pool.yaml"
VENV_PYTHON = Path(__file__).parent.parent.parent / ".venv" / "Scripts" / "python.exe"

# Track daemon agents via heartbeats: soul_id -> [(agent_id, last_seen), ...]
_live_agents: dict[str, list[tuple[str, float]]] = {}
_AGENT_TTL = 30.0  # seconds before a non-heartbeating agent is considered dead


def _pg_conn():
    return psycopg.connect(
        host=os.environ.get("RASA_DB_HOST", "localhost"),
        port=int(os.environ.get("RASA_DB_PORT", "5432")),
        user=os.environ.get("RASA_DB_USER", "postgres"),
        password=os.environ.get("RASA_DB_PASSWORD", ""),
        dbname="rasa_orch",
    )


# ── Agent tracking ──


def _prune_expired():
    """Remove agents that haven't heartbeated within TTL."""
    now = time.time()
    for soul_id in list(_live_agents.keys()):
        alive = [(aid, ts) for aid, ts in _live_agents[soul_id] if now - ts < _AGENT_TTL]
        if alive:
            _live_agents[soul_id] = alive
        else:
            del _live_agents[soul_id]


def _pick_agent(soul_id: str) -> str | None:
    """Pick the most recent live daemon agent for this soul type, or None."""
    _prune_expired()
    entries = _live_agents.get(soul_id)
    if not entries:
        return None
    # Most recent heartbeat first
    entries.sort(key=lambda x: -x[1])
    return entries[0][0]


# ── Handlers ──


async def _handle_raw_notify(conn: psycopg.Connection):
    """Listen for raw pg_notify on tasks_assigned and handle assignments."""
    conn.execute("LISTEN tasks_assigned")
    print("[pool] listening on tasks_assigned (PG NOTIFY)", flush=True)

    def _callback(notice):
        asyncio.create_task(_on_task_assigned(notice.payload or ""))

    conn.add_notify_handler(_callback)

    while True:
        await asyncio.sleep(1)
        # Periodic execute to process the incoming NOTIFY queue —
        # psycopg sync connections only fire notify handlers during execute()
        conn.execute("SELECT 1")


async def _on_task_assigned(payload: str):
    """Process a raw pg_notify payload from TaskDelegator.assign_task()."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        print(f"[pool] bad notify payload: {payload[:100]}", flush=True)
        return

    task_id = data.get("task_id")
    soul_id = data.get("soul_id", "")
    if not task_id:
        return

    print(f"[pool] received task {task_id[:12]}... -> soul={soul_id}", flush=True)

    # Try to assign to a running daemon agent first
    agent_id = _pick_agent(soul_id)
    if agent_id:
        print(f"[pool] assigning to daemon agent {agent_id}", flush=True)
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tasks SET assigned_agent_id = %s WHERE id = %s AND status = 'ASSIGNED'",
                        (agent_id, task_id),
                    )
                    if cur.rowcount:
                        print(f"[pool] task {task_id[:12]}... -> agent {agent_id}", flush=True)
                        return
        except Exception as e:
            print(f"[pool] agent assignment failed: {e}, falling back to one-shot", flush=True)

    # Fall back to spawning a one-shot dispatcher
    print(f"[pool] no daemon agent for {soul_id}, spawning one-shot", flush=True)
    _spawn_one_shot(task_id, soul_id)


async def _handle_heartbeat(env: Envelope) -> None:
    """Track live daemon agents from Redis heartbeats."""
    agent_id = env.metadata.agent_id
    soul_id = env.metadata.soul_id
    state = env.payload.get("current_state", "UNKNOWN")

    if soul_id and agent_id:
        entries = _live_agents.setdefault(soul_id, [])
        # Update or append
        for i, (aid, _ts) in enumerate(entries):
            if aid == agent_id:
                entries[i] = (agent_id, time.time())
                break
        else:
            entries.append((agent_id, time.time()))

    if state in ("IDLE", "ACTIVE", "WARMING"):
        pass  # agent is alive
    else:
        print(f"[pool] unhandled state from {agent_id}: {state}", flush=True)


def _spawn_one_shot(task_id: str, soul_id: str, goal: str | None = None) -> None:
    """Spawn a one-shot agent dispatcher subprocess."""
    cmd = [
        str(VENV_PYTHON),
        "-m", "rasa.agent.dispatcher",
        "--soul", soul_id,
        "--task-id", task_id,
        "--one-shot",
    ]
    env = os.environ.copy()
    env["RASA_DB_PASSWORD"] = os.environ.get("RASA_DB_PASSWORD", "")
    env.setdefault("RASA_MODEL", "deepseek-v4-pro:cloud")
    env.setdefault("OLLAMA_BASE_URL", os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"))
    print(f"[pool] spawning {soul_id} for task {task_id[:12]}...", flush=True)
    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


async def _cleanup_stale_agents():
    """Periodically prune expired agent entries."""
    while True:
        await asyncio.sleep(15)
        _prune_expired()


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool-file", default=str(CONFIG_PATH), help="Path to pool.yaml")
    args = parser.parse_args()

    with open(args.pool_file) as f:
        cfg = yaml.safe_load(f)
    print("[pool] controller starting with config:", args.pool_file, flush=True)

    # PostgreSQL LISTEN for task assignments (raw NOTIFY from delegator)
    pg_conn = _pg_conn()
    pg_conn.autocommit = True

    # Redis subscriber for agent heartbeats
    redis_sub = RedisSubscriber(url="redis://localhost:6379")
    await redis_sub.subscribe("agents.heartbeat.*", _handle_heartbeat)
    await redis_sub.listen()

    # Start listeners
    loop = asyncio.get_running_loop()
    loop.create_task(_handle_raw_notify(pg_conn))
    loop.create_task(_cleanup_stale_agents())

    print("[pool] listening on tasks_assigned (PG) + agents.heartbeat.* (Redis)", flush=True)
    print(f"[pool] {len(_live_agents)} agents tracked", flush=True)

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        pg_conn.close()
        await redis_sub.close()
        print("[pool] shut down", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
