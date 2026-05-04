"""Pool controller — subscribes to durable task assignments (PG LISTEN/NOTIFY)
and ephemeral heartbeats (Redis Pub/Sub). Spawns Windows workers on demand.

Usage:
  python -m rasa.pool.controller --pool-file config/pool.yaml
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from rasa.bus import Envelope, Metadata, PostgresSubscriber, RedisSubscriber

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "pool.yaml"


async def _handle_task_assigned(env: Envelope) -> None:
    """Handle a durable task assignment notification — spawn a worker."""
    soul_id = env.metadata.soul_id
    task_id = env.metadata.task_id
    goal = env.payload.get("title", "")
    print(f"[pool] received task_assigned: {task_id} -> soul={soul_id}")
    _spawn(task_id, soul_id, goal)


async def _handle_heartbeat(env: Envelope) -> None:
    """Handle an ephemeral heartbeat — log and track liveness."""
    agent_id = env.metadata.agent_id
    state = env.payload.get("current_state", "UNKNOWN")
    soul_id = env.metadata.soul_id
    print(f"[pool] heartbeat from {agent_id} ({soul_id}): {state}")


def _spawn(task_id: str, soul_id: str, goal: str | None) -> None:
    """Dispatch a task to a Windows subprocess — ollama launch claude --model deepseek-v4-pro:cloud."""
    venv_python = r"C:\Users\goldf\rasa\.venv\Scripts\python.exe"
    cmd = [
        venv_python,
        "-m", "rasa.agent.dispatcher",
        "--soul", soul_id,
        "--task-id", task_id,
        "--one-shot",
    ]
    env = os.environ.copy()
    env["RASA_DB_PASSWORD"] = os.environ.get("RASA_DB_PASSWORD", "")
    # Canonical model config: ollama launch claude --model deepseek-v4-pro:cloud
    env.setdefault("RASA_MODEL", "deepseek-v4-pro:cloud")
    env.setdefault("OLLAMA_BASE_URL", os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"))
    print(f"[pool] spawning {soul_id} for task {task_id} (model=deepseek-v4-pro:cloud)")
    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool-file", default=str(CONFIG_PATH), help="Path to pool.yaml")
    args = parser.parse_args()

    with open(args.pool_file) as f:
        cfg = yaml.safe_load(f)
    print("[pool] controller starting with config:", args.pool_file)

    # PostgreSQL subscriber for durable task assignments
    pg_sub = PostgresSubscriber(dbname="rasa_orch")
    await pg_sub.setup()
    await pg_sub.subscribe("tasks_assigned", _handle_task_assigned)
    await pg_sub.listen("tasks_assigned")

    # Redis subscriber for ephemeral agent heartbeats
    redis_sub = RedisSubscriber(url="redis://localhost:6379")
    await redis_sub.subscribe("agents.heartbeat.*", _handle_heartbeat)
    await redis_sub.listen()

    print("[pool] listening on tasks_assigned (PG) + agents.heartbeat.* (Redis)")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await pg_sub.close()
        await redis_sub.close()
        print("[pool] shut down")


if __name__ == "__main__":
    asyncio.run(main())
