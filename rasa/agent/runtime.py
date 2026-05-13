"""Agent Runtime — daemon task poller and interactive REPL.

Modes:
  daemon (default):  polls PostgreSQL for tasks, calls LLM, writes results.
  interactive:       stdin/stdout REPL for local debugging and exploration.
  one-shot:          not supported here; use dispatcher.py

Usage:
  python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
  python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml --mode interactive
  RASA_AGENT_MODE=interactive python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import selectors
import signal
import sys
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

import chevron
import httpx
import psycopg
import yaml

from rasa.agent.tools import AGENT_TOOL_DEFS, execute_tool
from rasa.agent.soul import SoulLoader
from rasa.agent.checkpoint import save_checkpoint, load_checkpoint, delete_checkpoint
from rasa.agent.replay import save_replay
from rasa.bus.envelope import Envelope, Metadata
from rasa.bus.redis import RedisPublisher
from rasa.llm_gateway.client import GatewayClient, GatewayError

SOULS_DIR = Path(__file__).parent.parent.parent / "souls"


class AgentState(Enum):
    IDLE = 'IDLE'
    WARMING = 'WARMING'
    ACTIVE = 'ACTIVE'
    PAUSED = 'PAUSED'
    RESUMING = 'RESUMING'
    CHECKPOINTED = 'CHECKPOINTED'
    RECOVERING = 'RECOVERING'


_soul_loader = SoulLoader()

def _load_soul(path: str) -> dict:
    """Load a soul sheet using SoulLoader (validates + resolves inheritance)."""
    p = Path(path)
    # Strip .yaml extension and directory prefix to get soul_id
    soul_id = p.stem if p.suffix == ".yaml" else p.name
    # If path includes a directory, try to resolve
    if not (SOULS_DIR / f"{soul_id}.yaml").exists():
        soul_id = path  # pass through as-is for SoulLoader to try
    soul_obj = _soul_loader.load(soul_id)
    return soul_obj.raw


def _make_agent_id(soul_id: str) -> str:
    return f"agent-{soul_id}-{uuid.uuid4().hex[:8]}"


def _pg_dsn(dbname: str) -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname={dbname}"


class AgentRuntime:
    """Stateful agent daemon: polls tasks, assembles prompts, calls LLM, writes results."""

    def __init__(self, soul_path: str, agent_id: str | None = None) -> None:
        self.soul = _load_soul(soul_path)
        self.agent_id = agent_id or _make_agent_id(self.soul["soul_id"])
        self.state = AgentState.IDLE
        self._running = False
        self.gateway: GatewayClient | None = None
        self.redis_pub: RedisPublisher | None = None
        self._current_task_id: str | None = None
        self._memory_context: dict[str, Any] = {}
        self._conversation_messages: list[dict[str, Any]] = []
        self._current_model: str | None = None

    async def start(self) -> None:
        self._running = True
        self.gateway = GatewayClient()
        self.redis_pub = RedisPublisher()
        await self.redis_pub.connect()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._heartbeat_loop())
                tg.create_task(self._task_poll_loop())
                tg.create_task(self._listen_control_channel())
        except* Exception as eg:
            import traceback
            print(f"[{self.agent_id}] TaskGroup failure: {eg}", flush=True)
            for exc in eg.exceptions:
                traceback.print_exception(type(exc), exc, exc.__traceback__)
            raise

    async def _heartbeat_loop(self) -> None:
        session = self.soul.get("behavior", {}).get("session", {})
        interval = session.get("heartbeat_interval_seconds", 5)
        while self._running:
            meta = Metadata(
                soul_id=self.soul["soul_id"],
                agent_id=self.agent_id,
                timestamp_ms=int(time.time() * 1000),
            )
            # Sample resource usage
            payload: dict[str, Any] = {
                "current_state": self.state.value,
                "soul_id": self.soul["soul_id"],
            }
            try:
                import psutil
                proc = psutil.Process()
                payload["memory_usage_bytes"] = proc.memory_info().rss
                payload["cpu_percent"] = proc.cpu_percent()
                payload["host_cpu_percent"] = psutil.cpu_percent(interval=None)
                payload["host_memory_percent"] = psutil.virtual_memory().percent
            except ImportError:
                pass

            env = Envelope.new(
                source="agent-runtime",
                destination="pool-controller",
                payload=payload,
                metadata=meta,
            )
            try:
                await self.redis_pub.publish(f"agents.heartbeat.{self.agent_id}", env)
            except Exception:
                pass  # heartbeat is best-effort
            await asyncio.sleep(interval)

    async def _task_poll_loop(self) -> None:
        print(f"[{self.agent_id}] poll loop started (soul_id={self.soul['soul_id']})", flush=True)
        while self._running:
            try:
                task = await self._poll_for_task()
                if task is not None:
                    await self._execute_task(task)
                else:
                    print(f"[{self.agent_id}] poll: no task found", flush=True)
            except Exception as exc:
                import traceback
                print(f"[{self.agent_id}] poll loop error: {exc}", flush=True)
                traceback.print_exc()
            await asyncio.sleep(5)

    async def _poll_for_task(self) -> dict | None:
        try:
            async with await psycopg.AsyncConnection.connect(_pg_dsn("rasa_orch")) as conn:
                async with conn.transaction():
                    cur = await conn.execute(
                        "SELECT id, title, description, payload FROM tasks "
                        "WHERE soul_id = %s AND status = 'ASSIGNED' "
                        "ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
                        (self.soul["soul_id"],),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return None
                    task_id = str(row[0])
                    await conn.execute(
                        "UPDATE tasks SET status = 'RUNNING', started_at = NOW() WHERE id = %s",
                        (task_id,),
                    )
                    payload = row[3]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    return {
                        "id": task_id,
                        "title": row[1],
                        "description": row[2] or "",
                        "payload": payload or {},
                    }
        except Exception:
            import traceback
            print(f"[{self.agent_id}] poll error: {traceback.format_exc()}", flush=True)
            return None

    def _should_checkpoint(self, turn: int) -> bool:
        interval = self.soul.get("behavior", {}).get("session", {}).get("checkpoint_interval_seconds", 30)
        # Checkpoint every N turns based on interval (rough: every ~6th turn for 30s interval at ~5s per turn)
        return turn > 0 and turn % max(1, interval // 5) == 0

    def _restore_from_checkpoint(self, task_id: str) -> bool:
        """Attempt to restore state from a previous checkpoint. Returns True if restored."""
        snapshot = load_checkpoint(task_id)
        if not snapshot:
            return False
        self._conversation_messages = snapshot.get("messages", [])
        self._memory_context = snapshot.get("memory_context", {})
        self._current_model = snapshot.get("model")
        state_str = snapshot.get("state", "ACTIVE")
        try:
            self.state = AgentState(state_str)
        except ValueError:
            self.state = AgentState.ACTIVE
        print(f"[{self.agent_id}] restored checkpoint for {task_id}: turn={snapshot.get('turn', 0)}, {len(self._conversation_messages)} messages", flush=True)
        return True

    async def _execute_task(self, task: dict) -> None:
        print(f"[{self.agent_id}] picked up task {task['id'][:8]}: {task['title']}", flush=True)
        self.state = AgentState.WARMING
        self._current_task_id = task["id"]

        memory = await self._assemble_memory(task)
        system_prompt = self._render_prompt(task, memory)

        self.state = AgentState.ACTIVE
        model_cfg = self.soul.get("model", {})

        # Check for existing checkpoint
        restored = self._restore_from_checkpoint(task["id"])

        # Build tool definitions from soul sheet's allowed_tools
        allowed = self.soul.get("behavior", {}).get("tool_policy", {}).get("allowed_tools", [])
        tool_defs = [AGENT_TOOL_DEFS[name] for name in allowed if name in AGENT_TOOL_DEFS]

        # Tool-calling loop: call LLM → execute tools → repeat until final content
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task["description"] or task["title"]},
        ]

        max_tool_rounds = 10
        start_turn = 0  # Always start turn count from 0 for this execution; checkpoint saved on restore sets messages
        for turn_idx in range(start_turn, start_turn + max_tool_rounds):
            try:
                result = await self.gateway.complete(
                    "",
                    messages=messages,
                    tools=tool_defs or None,
                    tier=model_cfg.get("default_tier", "standard"),
                    temperature=model_cfg.get("temperature", 0.2),
                    max_tokens=model_cfg.get("max_tokens", 8192),
                    top_p=model_cfg.get("top_p", 1.0),
                )
            except GatewayError as exc:
                await self._write_failure(task, str(exc))
                delete_checkpoint(task["id"])
                self.state = AgentState.IDLE
                self._current_task_id = None
                return
            except Exception as exc:
                import traceback
                print(f"[{self.agent_id}] unexpected error in _execute_task: {exc}", flush=True)
                traceback.print_exc()
                self.state = AgentState.IDLE
                self._current_task_id = None
                return

            tool_calls = result.get("tool_calls", [])
            if not tool_calls:
                await self._write_result(task, result)
                delete_checkpoint(task["id"])
                self.state = AgentState.IDLE
                self._current_task_id = None
                return

            # Append assistant message with tool_calls
            messages.append({
                "role": "assistant",
                "content": result.get("content", ""),
                "tool_calls": tool_calls,
            })

            # Execute each tool and append result message
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # Check policy rules (deny at DB level)
                try:
                    from rasa.policy.client import get_policy_client
                    policy = get_policy_client()
                    policy_result = policy.evaluate(name, args, self.soul["soul_id"], auto_invoke=self.soul.get("behavior", {}).get("tool_policy", {}).get("auto_invoke", True))
                    if policy_result["decision"] == "deny":
                        tool_result = {"error": f"Tool '{name}' denied by policy: {policy_result['reason']}"}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": json.dumps(tool_result, default=str),
                        })
                        continue
                except Exception:
                    pass  # policy check is best-effort, default allow

                # Check if this tool action requires human review
                review_needed = await self._check_human_review_required(name, args, task)
                if review_needed:
                    review_result = await self._request_and_wait_review(name, args, task)
                    if review_result.get("blocked"):
                        tool_result = {"error": f"Tool '{name}' blocked: {review_result.get('reason', 'human review required')}"}
                    elif review_result.get("approved"):
                        tool_result = await execute_tool(name, args)
                    else:
                        # Timeout or pending ? inform LLM
                        tool_result = {"error": f"Tool '{name}' pending human review. Await response."}
                else:
                    tool_result = await execute_tool(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, default=str),
                })

            # Save checkpoint periodically
            if self._should_checkpoint(turn_idx):
                save_checkpoint(
                    task_id=task["id"],
                    agent_id=self.agent_id,
                    messages=messages,
                    memory_context=self._memory_context,
                    current_state=self.state.value,
                    turn=turn_idx,
                    model=self._current_model,
                )

        # Exceeded max tool rounds
        await self._write_failure(task, "Exceeded max tool call rounds (10)")
        delete_checkpoint(task["id"])
        self.state = AgentState.IDLE
        self._current_task_id = None

    async def _check_human_review_required(self, tool_name: str, args: dict, task: dict) -> bool:
        """Check if this tool+args combination requires human confirmation per the soul sheet."""
        require_confirm = self.soul.get("behavior", {}).get("tool_policy", {}).get("require_human_confirm", [])
        if not require_confirm:
            return False
        for pattern in require_confirm:
            if ":" in pattern:
                tool_part, arg_part = pattern.split(":", 1)
                if tool_part != tool_name:
                    continue
                # Check if any argument value contains the restricted pattern
                for key, value in args.items():
                    if isinstance(value, str) and arg_part in value:
                        return True
            else:
                if pattern == tool_name:
                    return True
        return False

    async def _request_and_wait_review(self, tool_name: str, args: dict, task: dict) -> dict:
        """Create a human review and wait for a response with timeout."""
        try:
            from rasa.orchestrator.reviews import ReviewManager
            rm = ReviewManager()
            reason = f"Tool '{tool_name}' needs approval: {json.dumps(args)[:200]}"
            review = rm.create_review(
                task_id=task.get("id", ""),
                agent_id=self.agent_id,
                reason=reason,
                payload={"tool": tool_name, "args": args},
            )
            review_id = review.get("id", "")
            print(f"[{self.agent_id}] human review requested: {review_id}", flush=True)

            # Wait for response with timeout (60s)
            deadline = time.time() + 60
            while time.time() < deadline:
                status = rm.get_review(review_id)
                if status and status.get("status") == "answered":
                    response = status.get("response", "").lower()
                    if "approve" in response or "yes" in response or "proceed" in response:
                        return {"approved": True, "review_id": review_id}
                    return {"blocked": True, "reason": response, "review_id": review_id}
                await asyncio.sleep(3)

            return {"blocked": True, "reason": "Review timed out", "review_id": review_id}
        except Exception as e:
            print(f"[{self.agent_id}] review request failed: {e}", flush=True)
            return {"blocked": True, "reason": str(e)}

    async def _assemble_memory(self, task: dict) -> dict:
        # Try Go memory controller first
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                resp = await client.post(
                    "http://127.0.0.1:8300/assemble",
                    json={
                        "soul_id": self.soul["soul_id"],
                        "task_id": task["id"],
                        "agent_id": self.agent_id,
                        "variables": [
                            "short_term_summary",
                            "graph_excerpt",
                            "semantic_matches",
                        ],
                        "resolution": {},
                    },
                )
                resp.raise_for_status()
                ctx = resp.json().get("variables", {})
                # If Go controller returned meaningful data, use it
                if ctx.get("graph_excerpt") or ctx.get("short_term_summary"):
                    return ctx
        except Exception:
            pass

        # Fall back to direct pgvector search
        try:
            from rasa.memory.search import get_context_for_task
            ctx = get_context_for_task(
                task_title=task.get("title", ""),
                task_description=task.get("description", ""),
            )
            if ctx.get("graph_excerpt"):
                self._memory_context = ctx
                return ctx
        except Exception:
            pass

        return {
            "short_term_summary": "",
            "graph_excerpt": "",
            "semantic_matches": [],
        }

    def _render_prompt(self, task: dict, memory: dict) -> str:
        ctx = {
            "metadata": self.soul.get("metadata", {}),
            "agent_role": self.soul.get("agent_role", ""),
            "model": self.soul.get("model", {}),
            "behavior": self.soul.get("behavior", {}),
            "tools": {"enabled": []},
            "task": {
                "id": task["id"],
                "title": task["title"],
                "type": task.get("payload", {}).get("type", "generic"),
                "description": task.get("description", ""),
            },
            "memory": memory,
        }
        system = chevron.render(self.soul["prompt"]["system_template"], ctx)
        if "context_injection" in self.soul["prompt"]:
            system += "\n\n" + chevron.render(
                self.soul["prompt"]["context_injection"], ctx
            )
        return system.strip()



    # --- Pause / Resume / Recover ---

    async def pause(self) -> None:
        """Persist working memory to Redis and transition to PAUSED."""
        import redis.asyncio as aioredis
        try:
            r = await aioredis.from_url("redis://localhost:6379")
            snapshot = {
                "messages": self._conversation_messages,
                "memory_context": self._memory_context,
                "current_model": self._current_model,
                "task_id": self._current_task_id,
            }
            await r.set(f"rasa:agent:{self.agent_id}:paused", json.dumps(snapshot, default=str), ex=3600)
            await r.aclose()
        except Exception as exc:
            print(f"[{self.agent_id}] Redis pause persist failed: {exc}", flush=True)

        if self._current_task_id:
            try:
                async with await psycopg.AsyncConnection.connect(_pg_dsn("rasa_orch")) as conn:
                    await conn.execute(
                        "UPDATE tasks SET status = 'PAUSED' WHERE id = %s",
                        (self._current_task_id,),
                    )
            except Exception:
                pass

        self.state = AgentState.PAUSED
        print(f"[{self.agent_id}] paused (task={self._current_task_id})", flush=True)

    async def resume(self) -> bool:
        """Reload working memory from Redis and transition to ACTIVE."""
        self.state = AgentState.RESUMING
        import redis.asyncio as aioredis
        try:
            r = await aioredis.from_url("redis://localhost:6379")
            raw = await r.get(f"rasa:agent:{self.agent_id}:paused")
            await r.delete(f"rasa:agent:{self.agent_id}:paused")
            await r.aclose()
            if raw:
                snapshot = json.loads(raw)
                self._conversation_messages = snapshot.get("messages", [])
                self._memory_context = snapshot.get("memory_context", {})
                self._current_model = snapshot.get("current_model")
                self._current_task_id = snapshot.get("task_id")
        except Exception as exc:
            print(f"[{self.agent_id}] Redis resume load failed: {exc}", flush=True)

        if self._current_task_id:
            try:
                async with await psycopg.AsyncConnection.connect(_pg_dsn("rasa_orch")) as conn:
                    await conn.execute(
                        "UPDATE tasks SET status = 'RUNNING' WHERE id = %s",
                        (self._current_task_id,),
                    )
            except Exception:
                pass

        self.state = AgentState.ACTIVE
        print(f"[{self.agent_id}] resumed (task={self._current_task_id})", flush=True)
        return True

    async def recover(self) -> bool:
        """Attempt recovery from last checkpoint after a crash."""
        self.state = AgentState.RECOVERING
        print(f"[{self.agent_id}] attempting recovery...", flush=True)
        import redis.asyncio as aioredis
        try:
            r = await aioredis.from_url("redis://localhost:6379")
            raw = await r.get(f"rasa:agent:{self.agent_id}:paused")
            await r.aclose()
            if raw:
                snapshot = json.loads(raw)
                self._conversation_messages = snapshot.get("messages", [])
                self._memory_context = snapshot.get("memory_context", {})
                self._current_model = snapshot.get("current_model")
                self._current_task_id = snapshot.get("task_id")
                self.state = AgentState.ACTIVE
                print(f"[{self.agent_id}] recovered from Redis hot-path", flush=True)
                return True
        except Exception:
            pass
        if self._current_task_id:
            restored = self._restore_from_checkpoint(self._current_task_id)
            if restored:
                self.state = AgentState.ACTIVE
                print(f"[{self.agent_id}] recovered from flat-file checkpoint", flush=True)
                return True
        self.state = AgentState.IDLE
        print(f"[{self.agent_id}] recovery failed, returning to IDLE", flush=True)
        return False

    def reload_soul(self) -> bool:
        """Hot-reload the soul sheet from disk if changed."""
        soul_id = self.soul.get("soul_id", "")
        if not soul_id:
            return False
        new_soul = _soul_loader.reload_if_stale(soul_id)
        if new_soul is None:
            return False
        self.soul = new_soul.raw
        print(f"[{self.agent_id}] soul hot-reloaded: {soul_id} v{new_soul.soul_version}", flush=True)
        return True

    async def _listen_control_channel(self) -> None:
        """Listen on Redis control channel for pause/resume/recover signals."""
        import redis.asyncio as aioredis
        try:
            r = await aioredis.from_url("redis://localhost:6379")
            pubsub = r.pubsub()
            await pubsub.subscribe(f"agents.control.{self.agent_id}")
            print(f"[{self.agent_id}] listening on control channel agents.control.{self.agent_id}", flush=True)
            async for msg in pubsub.listen():
                if not self._running:
                    break
                if msg["type"] != "message":
                    continue
                data = msg.get("data", b"")
                if isinstance(data, bytes):
                    data = data.decode()
                if data == "pause":
                    await self.pause()
                elif data == "resume":
                    await self.resume()
                elif data == "recover":
                    await self.recover()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[{self.agent_id}] control channel error: {exc}", flush=True)
        finally:
            try:
                await pubsub.unsubscribe()
                await r.aclose()
            except Exception:
                pass

    async def _write_result(self, task: dict, result: dict) -> None:
        async with await psycopg.AsyncConnection.connect(_pg_dsn("rasa_orch")) as conn:
            await conn.execute(
                "UPDATE tasks SET status = 'COMPLETED', completed_at = NOW(), result = %s WHERE id = %s",
                (json.dumps(result), task["id"]),
            )
            await conn.execute("SELECT pg_notify('task_completed', %s)", (json.dumps({"task_id": str(task["id"]), "new_status": "COMPLETED"}),))

        # Write replay bundle
        try:
            save_replay(
                task_id=task["id"],
                soul_id=self.soul["soul_id"],
                soul_raw=self.soul,
                system_prompt=self._render_prompt(task, self._memory_context),
                messages=self._conversation_messages,
                result=result,
                memory_context=self._memory_context,
                model=self._current_model,
                token_usage=result.get("usage"),
            )
        except Exception:
            pass  # replay is best-effort

    async def _write_failure(self, task: dict, error_msg: str) -> None:
        async with await psycopg.AsyncConnection.connect(_pg_dsn("rasa_orch")) as conn:
            await conn.execute(
                "UPDATE tasks SET status = 'FAILED', failed_at = NOW(), error_message = %s WHERE id = %s",
                (error_msg, task["id"]),
            )
            await conn.execute("SELECT pg_notify('task_completed', %s)", (json.dumps({"task_id": str(task["id"]), "new_status": "FAILED"}),))

    async def _assemble_memory_interactive(self) -> dict:
        """Best-effort memory assembly for interactive sessions (no real task)."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                resp = await client.post(
                    "http://127.0.0.1:8300/assemble",
                    json={
                        "soul_id": self.soul["soul_id"],
                        "task_id": "interactive-session",
                        "agent_id": self.agent_id,
                        "variables": [
                            "short_term_summary",
                            "graph_excerpt",
                            "semantic_matches",
                        ],
                        "resolution": {},
                    },
                )
                resp.raise_for_status()
                return resp.json().get("variables", {})
        except Exception:
            return {
                "short_term_summary": "",
                "graph_excerpt": "",
                "semantic_matches": [],
            }

    async def _read_multiline_input(self) -> str | None:
        """Read multi-line input from stdin. Blank line sends, \\ continues."""
        lines: list[str] = []
        first = True
        while True:
            prompt = ">>> " if first else "... "
            first = False
            sys.stdout.write(prompt)
            sys.stdout.flush()
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                sys.stdout.write("\n")
                return None
            if not line:  # EOF (Ctrl+D / Ctrl+Z)
                sys.stdout.write("\n")
                return None
            stripped = line.rstrip("\r\n")
            if stripped.endswith("\\"):
                lines.append(stripped[:-1])
                continue
            if stripped == "" and lines:
                break
            if stripped == "":
                continue
            lines.append(stripped)
        text = "\n".join(lines).strip()
        return text or None

    def _handle_command(self, cmd: str) -> str | None:
        """Handle a slash command. Returns 'exit' to signal loop termination, None otherwise."""
        parts = cmd.strip().split(maxsplit=1)
        verb = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if verb in ("/exit", "/quit"):
            return "exit"

        if verb == "/help":
            print(
                "Commands:\n"
                "  /exit, /quit    Exit the interactive session\n"
                "  /help           Show this help message\n"
                "  /clear          Reset conversation history\n"
                "  /model <name>   Switch LLM model\n"
                "  /memory         Show current memory context\n"
                "  /save <file>    Save conversation to a markdown file\n"
                "\n"
                "Multi-line: end a line with \\ to continue. Press Enter on a blank line to send."
            )
            return None

        if verb == "/clear":
            system = self._conversation_messages[0]
            self._conversation_messages = [system]
            print("[Conversation history cleared]")
            return None

        if verb == "/model":
            if arg:
                self._current_model = arg
                print(f"[Model switched to: {arg}]")
            else:
                print(
                    f"[Current model: {self._current_model or 'tier default'}]"
                )
            return None

        if verb == "/memory":
            if self._memory_context:
                for key, val in self._memory_context.items():
                    if isinstance(val, str) and val:
                        print(f"[{key}]: {val[:200]}...")
                    elif isinstance(val, list) and val:
                        print(f"[{key}]: {len(val)} items")
                    elif not val:
                        print(f"[{key}]: (empty)")
                    else:
                        print(f"[{key}]: {val}")
            else:
                print("[No memory context available]")
            return None

        if verb == "/save":
            path = arg or f"conversation-{self.agent_id}.md"
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# Conversation - {self.soul['soul_id']}\n\n")
                    for msg in self._conversation_messages:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        if role == "system":
                            continue  # skip system prompt in export
                        f.write(f"## {role.capitalize()}\n\n{content}\n\n")
                print(f"[Conversation saved to {path}]")
            except OSError as e:
                print(f"[Error saving file: {e}]")
            return None

        print(f"Unknown command: {verb}. Type /help for available commands.")
        return None

    async def start_interactive(self) -> None:
        """Interactive REPL mode: read user input, call LLM, display results."""
        self._running = True
        self.gateway = GatewayClient()
        self.state = AgentState.ACTIVE

        synthetic_task = {
            "id": "interactive-session",
            "title": "Interactive session",
            "description": "",
            "payload": {"type": "conversation"},
        }

        self._memory_context = await self._assemble_memory_interactive()
        system_prompt = self._render_prompt(synthetic_task, self._memory_context)

        self._conversation_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._current_model: str | None = None

        model_cfg = self.soul.get("model", {})
        banner = (
            f"RASA Interactive — soul={self.soul['soul_id']} "
            f"agent={self.agent_id} "
            f"tier={model_cfg.get('default_tier', 'standard')}"
        )
        print(f"\n{'=' * len(banner)}")
        print(banner)
        print(f"{'=' * len(banner)}")
        print("Type /help for commands. Press Enter on a blank line to send.\n")

        try:
            while self._running:
                text = await self._read_multiline_input()
                if text is None:
                    break

                if text.startswith("/"):
                    if self._handle_command(text) == "exit":
                        break
                    continue

                self._conversation_messages.append(
                    {"role": "user", "content": text}
                )

                try:
                    result = await self.gateway.complete(
                        system_prompt,
                        tier=model_cfg.get("default_tier", "standard"),
                        model=self._current_model,
                        temperature=model_cfg.get("temperature", 0.2),
                        max_tokens=model_cfg.get("max_tokens", 8192),
                        top_p=model_cfg.get("top_p", 1.0),
                        seed=int(time.time()),
                        extra_body={"messages": self._conversation_messages},
                    )
                except Exception as exc:
                    print(f"\n[Error] {exc}")
                    self._conversation_messages.pop()
                    continue

                reply = result.get("content", "")
                usage = result.get("usage", {})
                pt = usage.get("prompt_tokens", "?")
                ct = usage.get("completion_tokens", "?")
                print(f"\n{reply}\n")
                print(f"[tokens: {pt}+{ct} | model: {result.get('model', '?')}]\n")

                self._conversation_messages.append(
                    {"role": "assistant", "content": reply}
                )
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._running = False
        if self.gateway:
            await self.gateway.close()
        if self.redis_pub:
            await self.redis_pub.close()


def _resolve_mode(args: argparse.Namespace, soul: dict) -> str:
    """Resolve effective session mode by priority:
    1. --mode flag
    2. --one-shot shorthand
    3. RASA_AGENT_MODE env var
    4. Soul sheet default
    """
    if args.mode is not None:
        return args.mode
    if args.one_shot:
        return "one-shot"
    env_mode = os.environ.get("RASA_AGENT_MODE")
    if env_mode in ("one-shot", "interactive", "daemon"):
        return env_mode
    return soul.get("behavior", {}).get("session", {}).get("mode", "daemon")


def main() -> None:
    parser = argparse.ArgumentParser(description="RASA Agent Runtime")
    parser.add_argument("--soul", required=True, help="Path to soul YAML file or soul_id")
    parser.add_argument("--agent-id", default=None, help="Override agent UUID")
    parser.add_argument(
        "--mode",
        choices=["one-shot", "interactive", "daemon"],
        default=None,
        help="Session mode (overrides RASA_AGENT_MODE env var and soul sheet default)",
    )
    parser.add_argument(
        "--one-shot",
        action="store_true",
        default=False,
        help="Shorthand for --mode one-shot",
    )
    args = parser.parse_args()

    runtime = AgentRuntime(soul_path=args.soul, agent_id=args.agent_id)
    mode = _resolve_mode(args, runtime.soul)
    print(f"Agent {runtime.agent_id} (soul={runtime.soul['soul_id']}, mode={mode}) starting")

    if mode == "interactive":
        try:
            asyncio.run(runtime.start_interactive())
        except KeyboardInterrupt:
            pass
        print(f"Agent {runtime.agent_id} stopped")
        return

    if mode == "one-shot":
        print("One-shot mode not supported via runtime; use dispatcher.py")
        sys.exit(1)

    # Daemon mode (default)
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    else:
        loop = asyncio.new_event_loop()
    shutdown_flag = False

    def _on_signal():
        nonlocal shutdown_flag
        if not shutdown_flag:
            shutdown_flag = True
            asyncio.ensure_future(runtime.shutdown(), loop=loop)

    try:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except NotImplementedError:
        pass  # Windows doesn't support add_signal_handler for SIGTERM

    try:
        loop.run_until_complete(runtime.start())
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        import traceback
        print(f"Agent {runtime.agent_id} fatal error: {exc}", flush=True)
        traceback.print_exc()
    finally:
        try:
            loop.run_until_complete(runtime.shutdown())
        except Exception:
            pass
        loop.close()
        print(f"Agent {runtime.agent_id} stopped")


if __name__ == "__main__":
    main()
