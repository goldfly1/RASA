"""Legacy one-shot agent dispatcher — superseded by runtime.py for daemon agents.

Usage:
  python -m rasa.agent.dispatcher --soul planner-v1 --goal "Design caching module"
  python -m rasa.agent.dispatcher --soul coder-v2-dev --task-id <uuid> --one-shot

Prefer rasa.agent.runtime for long-lived agent processes (proper state machine, chevron rendering).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import argparse
import time
import signal
from pathlib import Path
from typing import Any

import yaml
import chevron
import httpx
import psycopg


SOULS_DIR = Path(__file__).parent.parent.parent / "souls"
CHECKPOINTS_DIR = Path("C:/Users/goldf/rasa/checkpoints")


def _pg_conn(dbname = "rasa_orch"):
    pw = os.environ.get("RASA_DB_PASSWORD", "")
    return psycopg.connect(
        host=os.environ.get("RASA_DB_HOST", "localhost"),
        port=int(os.environ.get("RASA_DB_PORT", "5432")),
        user=os.environ.get("RASA_DB_USER", "postgres"),
        password=pw,
        dbname=dbname,
        sslmode="disable",
    )


def _load_soul(soul_id) -> dict:
    for p in SOULS_DIR.glob("*.yaml"):
        with open(p) as f:
            doc = yaml.safe_load(f)
            if doc.get("soul_id") == soul_id:
                return doc
    raise FileNotFoundError(f"Soul {soul_id} not found in {SOULS_DIR}")


def _resolve_model(soul, override) -> tuple:
    """Resolve model following canonical pattern: ollama launch claude --model deepseek-v4-pro:cloud."""
    # Single override takes precedence: RASA_MODEL > CLI --model-override
    model = os.environ.get("RASA_MODEL") or override
    if not model:
        if soul.get("model", {}).get("preferred_model"):
            model = soul["model"]["preferred_model"]
        else:
            tier = soul.get("model", {}).get("default_tier", "standard")
            if tier == "premium":
                model = os.environ.get("RASA_PREMIUM_MODEL", "deepseek-v4-pro:cloud")
            else:
                model = os.environ.get("RASA_DEFAULT_MODEL", "deepseek-v4-flash:cloud")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    return base_url, model


def _render_system_prompt(soul, task, memory) -> str:
    ctx = {
        "metadata": soul["metadata"],
        "agent_role": soul["agent_role"],
        "model": soul.get("model", {}),
        "behavior": soul.get("behavior", {}),
        "tools": {"enabled": []},
        "task": task,
        "memory": memory,
    }
    body = chevron.render(soul["prompt"]["system_template"], ctx)
    if "context_injection" in soul["prompt"]:
        body += "\n\n" + chevron.render(soul["prompt"]["context_injection"], ctx)
    return body.strip()


async def _call_llm(base_url, model, messages, temperature, max_tokens) -> dict:
    api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    TRANSIENT = (429, 500, 502, 503)
    max_retries = 3
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as c:
                r = await c.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code in TRANSIENT and attempt < max_retries - 1:
                    body = ""
                    try:
                        body = r.text[:500]
                    except Exception:
                        pass
                    wait = 2 ** attempt
                    print(f"[dispatcher] LLM {r.status_code}, retrying in {wait}s "
                          f"(attempt {attempt + 1}/{max_retries})  body={body}", flush=True)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "model": data["model"],
                    "usage": data.get("usage", {}),
                }
        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"[dispatcher] LLM timeout, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
                continue
            raise RuntimeError("LLM call timed out after retries")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in TRANSIENT and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"[dispatcher] LLM {e.response.status_code}, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
                continue
            detail = ""
            try:
                detail = f": {e.response.text[:500]}"
            except Exception:
                pass
            raise RuntimeError(f"LLM call failed: {e.response.status_code}{detail}")
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"[dispatcher] LLM error: {e}, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
                continue
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_error}")


async def run_task(soul_id, task_id, goal, model_override, dry_run, one_shot) -> dict:
    soul = _load_soul(soul_id)
    base_url, model = _resolve_model(soul, model_override)

    with _pg_conn("rasa_orch") as conn:
        with conn.cursor() as cur:
            if task_id:
                cur.execute("SELECT id, title, description, payload, status FROM tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Task {task_id} not found")
                task = {"id": str(row[0]), "title": row[1], "description": row[2] or "", "type": (row[3] or {}).get("type", "generic"), "payload": row[3] or {}}
                cur.execute(
                    "UPDATE tasks SET status = 'RUNNING', started_at = NOW() "
                    "WHERE id = %s AND status IN ('ASSIGNED', 'PENDING')",
                    (task_id,),
                )
                if cur.rowcount == 0:
                    # Another process already claimed this task — bail out
                    cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
                    row = cur.fetchone()
                    print(f"[dispatcher] task {task_id[:12]} already claimed (status={row[0] if row else '?'}), exiting", flush=True)
                    conn.commit()
                    return {"task_id": task_id, "soul_id": soul_id, "skipped": True, "status": row[0] if row else 'gone'}
            else:
                cur.execute(
                    "INSERT INTO tasks (title, description, payload, status, soul_id) VALUES (%s, %s, %s, 'RUNNING', %s) RETURNING id",
                    (goal or f"Ad-hoc {soul_id}", "", json.dumps({"type": "ad-hoc", "goal": goal}), soul_id),
                )
                tid = str(cur.fetchone()[0])
                task = {"id": tid, "title": goal or f"Ad-hoc {soul_id}", "description": "", "type": "ad-hoc", "payload": {"goal": goal}}
                task_id = tid
            conn.commit()

    memory = {"short_term_summary": "", "graph_excerpt": "", "diff_summary": "", "semantic_matches": "[]"}
    system_prompt = _render_system_prompt(soul, task, memory)
    messages = [{"role": "system", "content": system_prompt}]
    if task.get("description"):
        messages.append({"role": "user", "content": task["description"]})
    elif goal:
        messages.append({"role": "user", "content": goal})

    temperature = soul.get("model", {}).get("temperature", 0.2)
    max_tokens = soul.get("model", {}).get("max_tokens", 4096)

    if dry_run:
        result = {"dry_run": True, "messages": messages, "model": model}
    else:
        try:
            result = await _call_llm(base_url, model, messages, temperature, max_tokens)
        except Exception as e:
            print(f"[dispatcher] LLM call failed for task {task_id}: {e}", flush=True)
            with _pg_conn("rasa_orch") as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tasks SET status = 'FAILED', completed_at = NOW(), "
                        "result = %s WHERE id = %s",
                        (json.dumps({"error": str(e), "stage": "llm_call"}), task_id),
                    )
                conn.commit()
            return {"task_id": task_id, "soul_id": soul_id, "error": str(e)}

    if not dry_run:
        with _pg_conn("rasa_orch") as conn:
            with conn.cursor() as cur:
                status = "COMPLETED" if one_shot else "CHECKPOINTED"
                cur.execute(
                    "UPDATE tasks SET status = %s, completed_at = NOW(), result = %s WHERE id = %s",
                    (status, json.dumps(result), task_id),
                )
                if not one_shot:
                    cur.execute(
                        "INSERT INTO checkpoint_refs (task_id, agent_id, snapshot_path, metadata) VALUES (%s, %s, %s, %s)",
                        (task_id, f"agent-{soul_id}", str(CHECKPOINTS_DIR / f"{task_id}.json"), json.dumps({"turn": 1})),
                    )
                conn.commit()

    return {"task_id": task_id, "soul_id": soul_id, **result}


async def daemon_loop(soul_id, task_id, model_override, interval=5):
    stop_requested = False
    def _handle_sig(signum, frame):
        nonlocal stop_requested
        stop_requested = True
    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)
    print(f"[{soul_id}] daemon starting for task {task_id}")
    while not stop_requested:
        with _pg_conn("rasa_orch") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET started_at = COALESCE(started_at, NOW()) WHERE id = %s AND status IN ('ASSIGNED', 'RUNNING', 'CHECKPOINTED')",
                    (task_id,),
                )
                cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                if not row or row[0] in ("COMPLETED", "FAILED", "CANCELLED"):
                    print(f"[{soul_id}] task {task_id} terminal state {row[0] if row else 'gone'}; shutting down")
                    conn.commit()
                    break
                conn.commit()
        print(f"[{soul_id}] heartbeat")
        time.sleep(interval)
    print(f"[{soul_id}] daemon stopped")


def main():
    parser = argparse.ArgumentParser(description="RASA Windows-side agent dispatcher")
    parser.add_argument("--soul", required=True, help="Soul id (e.g. coder-v2-dev)")
    parser.add_argument("--task-id", default=None, help="Existing task UUID")
    parser.add_argument("--goal", default=None, help="Ad-hoc goal text")
    parser.add_argument("--model-override", default=None, help="Force a specific LLM model")
    parser.add_argument("--dry-run", action="store_true", help="Render prompt but don't call LLM")
    parser.add_argument("--one-shot", action="store_true", default=True, dest="one_shot", help="Run once and exit")
    parser.add_argument("--daemon", action="store_false", dest="one_shot", help="Run heartbeat loop")
    parser.add_argument("--heartbeat-interval", type=int, default=5, help="Seconds between heartbeats")
    args = parser.parse_args()

    soul = _load_soul(args.soul)

    if args.one_shot:
        result = asyncio.run(
            run_task(
                soul_id=args.soul,
                task_id=args.task_id,
                goal=args.goal,
                model_override=args.model_override,
                dry_run=args.dry_run,
                one_shot=True,
            )
        )
        print(json.dumps(result, indent=2))
    else:
        if not args.task_id:
            with _pg_conn("rasa_orch") as conn:
                with conn.cursor() as cur:
                    goal = args.goal or f"Daemon {args.soul}"
                    cur.execute(
                        "INSERT INTO tasks (title, description, payload, status, soul_id) VALUES (%s, %s, %s, 'ASSIGNED', %s) RETURNING id",
                        (goal, "", json.dumps({"type": "daemon", "goal": goal}), args.soul),
                    )
                    args.task_id = str(cur.fetchone()[0])
                    conn.commit()
        asyncio.run(run_task(args.soul, args.task_id, args.goal, args.model_override, args.dry_run, False))
        asyncio.run(daemon_loop(args.soul, args.task_id, args.model_override, args.heartbeat_interval))


if __name__ == "__main__":
    main()
