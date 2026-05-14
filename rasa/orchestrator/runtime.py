"""OrchestratorRuntime — multi-turn agent with task delegation capabilities."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import chevron
import httpx
import yaml

from rasa.orchestrator.capabilities import CapabilityRegistry
from rasa.orchestrator.delegator import TaskDelegator
from rasa.orchestrator.project import ProjectManager
from rasa.orchestrator.reviews import ReviewManager
from rasa.orchestrator.tools import ORCHESTRATOR_TOOL_DEFS

SOULS_DIR = Path(__file__).parent.parent.parent / "souls"
PROJECT_ROOT = SOULS_DIR.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


# ── Soul loading (same pattern as chat.py) ──

def _load_soul(soul_id: str) -> dict:
    p = SOULS_DIR / f"{soul_id}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"Soul '{soul_id}' not found")
    return yaml.safe_load(p.read_text())


def _render_system_prompt(
    soul: dict,
    project_summary: str = "",
    task_context: str = "",
    capabilities: list[dict] | None = None,
    service_status: str = "",
) -> str:
    allowed = soul.get("behavior", {}).get("tool_policy", {}).get("allowed_tools", [])
    from rasa.agent.tools import AGENT_TOOL_DEFS as CHAT_TOOL_DEFS
    tool_infos = []
    for t in allowed:
        if t in ORCHESTRATOR_TOOL_DEFS:
            tool_infos.append({"name": t, "description": ORCHESTRATOR_TOOL_DEFS[t]["function"]["description"]})
        elif t in CHAT_TOOL_DEFS:
            tool_infos.append({"name": t, "description": CHAT_TOOL_DEFS[t]["function"]["description"]})

    ctx = {
        "metadata": soul.get("metadata", {}),
        "agent_role": soul.get("agent_role", ""),
        "model": soul.get("model", {}),
        "behavior": soul.get("behavior", {}),
        "tools": {"enabled": tool_infos},
        "task": {
            "id": "orchestrator-session",
            "title": "Orchestrator conversation",
            "type": "conversation",
            "description": "",
        },
        "memory": {
            "project_state": project_summary,
            "active_tasks": task_context,
            "service_status": service_status,
        },
    }
    if capabilities:
        for c in capabilities:
            c["capability_list"] = c.get("capabilities", [])
        ctx["memory"]["agent_capabilities"] = capabilities
    body = chevron.render(soul["prompt"]["system_template"], ctx)
    if "context_injection" in soul.get("prompt", {}):
        body += "\n\n" + chevron.render(soul["prompt"]["context_injection"], ctx)
    if "tool_use_preamble" in soul.get("prompt", {}):
        body += "\n\n" + chevron.render(soul["prompt"]["tool_use_preamble"], ctx)
    if tool_infos:
        body += (
            "\n\nYou have function-calling tools available.\n"
            "When a task requires creating work for another agent, "
            "call task_create first, then task_assign to dispatch it.\n"
            "After assigning, do NOT poll task_query repeatedly — "
            "assigned tasks run asynchronously in the background. "
            "Query at most once per turn. Continue with other work "
            "instead of waiting for sub-tasks to complete."
        )
    return body.strip()


# ── Runtime ──

class OrchestratorRuntime:
    """Multi-turn orchestrator with task delegation, service management, and project tracking."""

    def __init__(
        self,
        process_manager=None,
        health_cache: dict | None = None,
        health_cache_lock=None,
        service_map: dict | None = None,
        registry: list | None = None,
    ):
        self.soul = _load_soul("orchestrator-v1")
        self.delegator = TaskDelegator()
        self.project_mgr = ProjectManager()
        self.review_mgr = ReviewManager()
        self._messages: list[dict] = []
        self._project_id: str | None = None
        self._mode: str = "autonomous"  # or "step_by_step"
        # Service management (set by server.py)
        self.process_manager = process_manager
        self._health_cache = health_cache or {}
        self._health_cache_lock = health_cache_lock
        self._service_map = service_map or {}
        self._registry = registry or []

    @property
    def project_id(self) -> str | None:
        return self._project_id

    def set_project(self, project_id: str) -> None:
        self._project_id = project_id

    def set_mode(self, mode: str) -> None:
        if mode in ("step_by_step", "autonomous"):
            self._mode = mode

    def get_mode(self) -> str:
        return self._mode

    def _load_capabilities(self) -> list[dict]:
        """Fetch agent capabilities from the registry for system prompt injection."""
        try:
            registry = CapabilityRegistry()
            return registry.list_capabilities()
        except Exception:
            return []

    def _render_system(self) -> str:
        summary = ""
        task_ctx = ""
        if self._project_id:
            summary = self.project_mgr.get_project_summary(self._project_id)
            tasks = self.delegator.list_project_tasks(self._project_id)
            if tasks:
                active = [t for t in tasks if t["status"] in ("PENDING", "ASSIGNED", "RUNNING")]
                task_ctx = f"{len(tasks)} total, {len(active)} active"
        caps = self._load_capabilities()
        # Ingest pending human reviews into context so the LLM knows to poll
        pending_reviews = self.review_mgr.get_pending_reviews(limit=5)
        if pending_reviews:
            lines = []
            for r in pending_reviews:
                lines.append(f"- Review {r['id'][:8]}...: {r['reason'][:120]}")
            task_ctx += "\n\n## Pending Human Reviews\n" + "\n".join(lines)
        # Build service status string (read-only snapshot of health cache)
        cache = self._health_cache if isinstance(self._health_cache, dict) else {}
        svc_lines = []
        for sid, info in cache.items():
            st = info.get("status", "unknown")
            if st == "running":
                svc_lines.append(f"  {sid}: RUNNING")
            elif st == "error":
                svc_lines.append(f"  {sid}: ERROR")
            else:
                svc_lines.append(f"  {sid}: {st.upper()}")
        service_status = "\n".join(svc_lines) if svc_lines else "Service cache not yet initialized."
        return _render_system_prompt(self.soul, summary, task_ctx, caps, service_status)

    def _get_tool_defs(self) -> list[dict]:
        allowed = self.soul.get("behavior", {}).get("tool_policy", {}).get("allowed_tools", [])
        defs = []
        for t in allowed:
            if t in ORCHESTRATOR_TOOL_DEFS:
                defs.append(ORCHESTRATOR_TOOL_DEFS[t])
            else:
                from rasa.gui.chat import TOOL_DEFS
                if t in TOOL_DEFS:
                    defs.append(TOOL_DEFS[t])
        return defs

    async def _execute_tool(self, tool_name: str, args: dict) -> dict:
        """Execute an orchestrator or file tool. Returns {result: str}."""
        try:
            # ── Orchestrator tools ──
            if tool_name == "task_create":
                soul_id = args["soul_id"]
                title = args["title"]
                description = args.get("description", "")
                tid = self.delegator.create_task(
                    soul_id=soul_id,
                    title=title,
                    description=description,
                    parent_id=None,
                )
                if self._project_id:
                    # If this is the first task, set it as root
                    proj = self.project_mgr.get_project(self._project_id)
                    if proj and not proj["root_task_id"]:
                        self.project_mgr.set_root_task(self._project_id, tid)
                return {
                    "result": f"Task created: {tid} (soul={soul_id}, status=PENDING)",
                    "metadata": {
                        "task_id": tid,
                        "soul_id": soul_id,
                        "title": title,
                        "status": "PENDING",
                    },
                }

            elif tool_name == "task_assign":
                task_id = args["task_id"]
                soul = self.delegator.assign_task(task_id)
                if soul:
                    self._spawn_agent(task_id, soul)
                    return {
                        "result": f"Task {task_id} assigned to {soul} — agent process launched",
                        "metadata": {
                            "task_id": task_id,
                            "soul_id": soul,
                            "status": "ASSIGNED",
                        },
                    }
                else:
                    return {"result": f"Task {task_id} not found or not in PENDING state"}

            elif tool_name == "task_query":
                task_id = args["task_id"]
                task = self.delegator.query_task(task_id)
                if task:
                    return {
                        "result": json.dumps(task, indent=2),
                        "metadata": {
                            "task_id": task_id,
                            "status": task.get("status"),
                            "soul_id": task.get("soul_id"),
                            "title": task.get("title"),
                        },
                    }
                else:
                    return {"result": f"Task {task_id} not found"}

            elif tool_name == "task_list":
                tasks = self.delegator.list_project_tasks(self._project_id)
                if tasks:
                    return {"result": json.dumps(tasks, indent=2)}
                else:
                    return {"result": "No tasks found for this project"}

            elif tool_name == "project_status":
                if self._project_id:
                    summary = self.project_mgr.get_project_summary(self._project_id)
                    return {"result": summary}
                else:
                    return {"result": "No project selected. Create or select a project first."}

            elif tool_name == "capability_query":
                from rasa.orchestrator.capabilities import CapabilityRegistry
                cr = CapabilityRegistry()
                results = cr.list_capabilities()
                category = args.get("category", "").strip().lower()
                role = args.get("role", "").strip().lower()
                if category:
                    results = [
                        r for r in results
                        if any(
                            c.get("category", "").lower() == category
                            for c in (r.get("capabilities") or [])
                        )
                    ]
                if role:
                    results = [r for r in results if r.get("agent_role", "").lower() == role]
                if not results:
                    return {"result": "No agents found matching the query."}
                return {"result": json.dumps(results, indent=2)}

            elif tool_name == "request_human_input":
                reason = args["reason"]
                payload = args.get("payload", {})
                review = self.review_mgr.create_review(
                    task_id=self._project_id or "orchestrator-session",
                    agent_id="orchestrator-v1",
                    reason=reason,
                    payload=payload,
                )
                return {
                    "result": (
                        f"Human review requested. Review ID: {review['id']}. "
                        f"Reason: {reason}. "
                        "The human will see this on the dashboard. "
                        "Call check_human_response with this review_id on a later turn "
                        "to see if the human has responded."
                    ),
                    "metadata": {
                        "review_id": review["id"],
                        "reason": reason,
                        "payload": payload,
                        "status": "BLOCKED",
                    },
                }

            elif tool_name == "check_human_response":
                review_id = args["review_id"]
                review = self.review_mgr.get_review(review_id)
                if not review:
                    return {"result": f"Review {review_id} not found."}

                if review["status"] == "answered":
                    response_text = review.get("response", "")
                    return {
                        "result": (
                            f"The human has responded to review {review_id}.\n"
                            f"Reviewer: {review.get('reviewer', 'unknown')}\n"
                            f"Guidance: {response_text}\n"
                            "You should follow this guidance and proceed."
                        ),
                        "metadata": {
                            "review_id": review_id,
                            "status": "answered",
                            "response": response_text,
                            "reviewer": review.get("reviewer"),
                        },
                    }
                elif review["status"] == "pending":
                    return {
                        "result": (
                            f"Review {review_id} is still pending. "
                            "The human has not responded yet. "
                            "Continue with other work and check again later."
                        ),
                        "metadata": {
                            "review_id": review_id,
                            "status": "pending",
                        },
                    }
                else:
                    return {
                        "result": (
                            f"Review {review_id} has status '{review['status']}'. "
                            f"Response: {review.get('response', 'N/A')}"
                        ),
                        "metadata": {
                            "review_id": review_id,
                            "status": review["status"],
                            "response": review.get("response"),
                        },
                    }

            # ── Service management tools ──
            elif tool_name == "service_list":
                if not self._registry:
                    return {"result": "Service registry not available — orchestrator not fully initialized."}
                lines = []
                for svc in self._registry:
                    status = "unknown"
                    pid = None
                    if self._health_cache_lock:
                        async with self._health_cache_lock:
                            cached = self._health_cache.get(svc.id, {})
                            status = cached.get("status", "unknown")
                            pid = cached.get("pid")
                    else:
                        if self.process_manager and self.process_manager.is_running(svc.id):
                            status = "running"
                    lines.append(
                        f"  {svc.id:22s}  {status:10s}  "
                        f"{'pid=' + str(pid) if pid else '':10s}  "
                        f"({svc.group.value})"
                    )
                    deps = svc.depends_on
                    if deps:
                        lines[-1] += f"  depends: {', '.join(deps)}"
                return {"result": "Services:\n" + "\n".join(lines)}

            elif tool_name == "service_start":
                service_id = args.get("service_id", "")
                svc = self._service_map.get(service_id)
                if not svc:
                    return {"result": f"Service '{service_id}' not found."}
                if not svc.can_start:
                    return {"result": f"Service '{service_id}' is external and cannot be started from here."}
                if not self.process_manager:
                    return {"result": "Process manager not available."}
                try:
                    pid = await self.process_manager.start(svc)
                    from rasa.gui.health import HealthChecker
                    await asyncio.sleep(1.0)
                    exit_code = self.process_manager.get_exit_code(svc.id)
                    if exit_code is not None:
                        stderr = self.process_manager.get_stderr(svc.id)
                        detail = f"exited immediately (code {exit_code})"
                        if stderr:
                            detail += f": {stderr.strip()[:200]}"
                        await self.process_manager.stop(svc.id)
                        return {"result": f"Service '{service_id}' {detail}"}
                    return {
                        "result": f"Service '{service_id}' starting (PID {pid}). It should be ready shortly.",
                        "metadata": {"service_id": service_id, "pid": pid, "status": "starting"},
                    }
                except Exception as e:
                    return {"result": f"Failed to start '{service_id}': {e}"}

            elif tool_name == "service_stop":
                service_id = args.get("service_id", "")
                if not self.process_manager:
                    return {"result": "Process manager not available."}
                try:
                    await self.process_manager.stop(service_id)
                    return {"result": f"Service '{service_id}' stopped."}
                except Exception as e:
                    return {"result": f"Failed to stop '{service_id}': {e}"}

            # ── File tools (reuse chat.py logic) ──
            elif tool_name == "file_read":
                path = Path(args["path"])
                if not path.is_absolute():
                    path = PROJECT_ROOT / path
                content = path.read_text(encoding="utf-8", errors="replace")
                truncated = len(content) > 10000
                if truncated:
                    content = content[:10000] + "\n\n...[truncated]"
                return {"result": content}

            elif tool_name == "git_diff":
                cmd = ["git", "diff"]
                if args.get("path"):
                    cmd.append(args["path"])
                import subprocess
                result = subprocess.run(
                    cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30
                )
                output = result.stdout.strip() or "(no changes)"
                return {"result": output}

            elif tool_name == "file_write":
                path = Path(args["path"])
                if not path.is_absolute():
                    path = PROJECT_ROOT / path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["content"], encoding="utf-8")
                return {"result": f"Written {len(args['content'])} bytes to {path}"}

            elif tool_name == "shell_exec":
                cmd = args["command"]
                import subprocess
                result = subprocess.run(
                    cmd, shell=True, cwd=PROJECT_ROOT,
                    capture_output=True, text=True, timeout=60,
                )
                output = ""
                if result.stdout:
                    output += result.stdout
                if result.stderr:
                    if output:
                        output += "\n--- stderr ---\n"
                    output += result.stderr
                if result.returncode != 0:
                    output += f"\n(exit code {result.returncode})"
                truncated = len(output) > 5000
                if truncated:
                    output = output[:5000] + "\n\n...[truncated]"
                return {"result": output.strip() or f"(exit code {result.returncode})"}

            else:
                return {"result": f"Unknown tool: {tool_name}"}

        except Exception as e:
            return {"result": f"Error executing {tool_name}: {e}"}

    # ── Agent spawning ──

    def _spawn_agent(self, task_id: str, soul_id: str) -> None:
        """Launch a daemon AgentRuntime subprocess for the task."""
        if not VENV_PYTHON.exists():
            print(f"[orch] venv python not found at {VENV_PYTHON}, cannot spawn agent", flush=True)
            return

        # Map soul_id to known service IDs for daemon agents
        soul_to_service = {
            "planner-v1": "agent-planner",
            "architect-v1": "agent-architect",
            "coder-v2-dev": "agent-coder",
            "reviewer-v1": "agent-reviewer",
        }
        service_id = soul_to_service.get(soul_id)

        # If we have a process manager, try starting the daemon service
        if self.process_manager and service_id:
            svc = self._service_map.get(service_id)
            if svc:
                # Check if already running
                if not self.process_manager.is_running(service_id):
                    print(f"[orch] starting daemon {service_id} for {soul_id} task {task_id[:12]}", flush=True)
                    try:
                        import asyncio
                        pid = asyncio.run_coroutine_threadsafe(
                            self.process_manager.start(svc),
                            asyncio.get_event_loop(),
                        ).result(timeout=10)
                        print(f"[orch] {service_id} started PID {pid}", flush=True)
                    except Exception as e:
                        print(f"[orch] failed to start {service_id}: {e}, falling back to direct spawn", flush=True)
                        service_id = None  # fall through to direct spawn
                else:
                    print(f"[orch] {service_id} already running, task {task_id[:12]} will be picked up", flush=True)

        # If no process manager or service wasn't started, spawn directly as daemon
        if not self.process_manager or not service_id:
            soul_path = SOULS_DIR / f"{soul_id}.yaml"
            if not soul_path.exists():
                print(f"[orch] soul not found at {soul_path}, cannot spawn", flush=True)
                return
            cmd = [
                str(VENV_PYTHON),
                "-m", "rasa.agent.runtime",
                "--soul", str(soul_path),
                "--mode", "daemon",
            ]
            env = os.environ.copy()
            env.setdefault("RASA_DB_PASSWORD", env.get("RASA_DB_PASSWORD", ""))
            env.setdefault("RASA_MODEL", env.get("RASA_MODEL", "deepseek-v4-pro:cloud"))
            env.setdefault("OLLAMA_BASE_URL", env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"))
            log_dir = PROJECT_ROOT / "logs"
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / f"agent_{soul_id}_{task_id[:12]}.log"
            print(f"[orch] spawning {soul_id} daemon for task {task_id[:12]}... (log: {log_path})", flush=True)
            try:
                with open(log_path, "w") as log:
                    subprocess.Popen(
                        cmd, env=env,
                        stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except Exception as e:
                print(f"[orch] failed to spawn agent: {e}", flush=True)

    async def _llm_call(self, base_url: str, api_key: str, payload: dict) -> dict:
        """Call the LLM API with retry logic for transient errors."""
        TRANSIENT = (429, 500, 502, 503)
        max_retries = 3
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as c:
                    r = await c.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    if r.status_code in TRANSIENT and attempt < max_retries - 1:
                        wait = 2 ** attempt
                        body = ""
                        try:
                            body = r.text[:500]
                        except Exception:
                            pass
                        print(f"[orch] LLM {r.status_code}, retrying in {wait}s "
                              f"(attempt {attempt + 1}/{max_retries})  body={body}", flush=True)
                        await asyncio.sleep(wait)
                        continue
                    r.raise_for_status()
                    return r.json()
            except httpx.TimeoutException:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"LLM timeout, retrying in {wait}s "
                          f"(attempt {attempt + 1}/{max_retries})", flush=True)
                    await asyncio.sleep(wait)
                    continue
                raise RuntimeError("LLM call timed out after retries")
            except httpx.HTTPStatusError as e:
                if e.response.status_code in TRANSIENT and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"LLM {e.response.status_code}, retrying in {wait}s "
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
                    print(f"LLM error: {e}, retrying in {wait}s "
                          f"(attempt {attempt + 1}/{max_retries})", flush=True)
                    await asyncio.sleep(wait)
                    continue
        raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_error}")

    async def send_message(self, text: str, on_event: Callable[[dict], Awaitable[None]] | None = None) -> dict:
        """Send a message to the orchestrator. Returns reply + steps.

        If on_event is provided, it's called with each intermediate event:
        - {"type": "thinking", "model": ..., "turn": N}
        - {"type": "tool_call", "name": ..., "args": ...}
        - {"type": "tool_result", "name": ..., "summary": ...}
        - {"type": "reply_chunk", "text": ...}  (final reply)
        """
        # Inject current project/task/review context into the system prompt
        # every turn so the LLM always sees up-to-date state
        system = self._render_system()
        if self._messages and self._messages[0]["role"] == "system":
            self._messages[0]["content"] = system
        else:
            self._messages.insert(0, {"role": "system", "content": system})

        self._messages.append({"role": "user", "content": text})

        # Trim conversation history: keep system + last 10 turns (20 messages)
        # to prevent unbounded context growth
        MAX_HISTORY = 20  # total non-system messages to keep
        non_system = [m for m in self._messages if m["role"] != "system"]
        if len(non_system) > MAX_HISTORY:
            to_remove = len(non_system) - MAX_HISTORY
            removed = 0
            i = 1  # skip system at [0]
            while i < len(self._messages) and removed < to_remove:
                if self._messages[i]["role"] != "system":
                    self._messages.pop(i)
                    removed += 1
                else:
                    i += 1

        # Model config — canonical pattern: ollama launch claude --model deepseek-v4-pro:cloud
        model_cfg = self.soul.get("model", {})
        model = (os.environ.get("RASA_MODEL")
                 or os.environ.get("RASA_PREMIUM_MODEL", "deepseek-v4-pro:cloud"))
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
        tool_defs = self._get_tool_defs()

        start = time.time()
        steps: list[dict] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        turn = 0
        while True:
            turn += 1
            if on_event:
                await on_event({"type": "thinking", "model": model, "turn": turn, "elapsed": round(time.time() - start, 1)})

            payload: dict[str, Any] = {
                "model": model,
                "messages": self._messages,
                "stream": False,
                "temperature": model_cfg.get("temperature", 0.2),
                "max_tokens": model_cfg.get("max_tokens", 4096),
            }
            if tool_defs:
                payload["tools"] = tool_defs

            data = await self._llm_call(base_url, api_key, payload)

            choice = data["choices"][0]
            msg = choice["message"]
            usage = data.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            # Check for tool calls
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                reply = msg.get("content", "") or ""
                self._messages.append({"role": "assistant", "content": reply})
                elapsed = time.time() - start
                result = {
                    "reply": reply,
                    "steps": steps,
                    "model": data.get("model", model),
                    "usage": {
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                    },
                    "elapsed_seconds": round(elapsed, 1),
                    "project_id": self._project_id,
                    "mode": self._mode,
                    "tool_calls": len(steps),
                }
                if on_event:
                    await on_event({"type": "reply", "text": reply, "result": result})
                    await on_event({"type": "done", "result": result})
                return result

            # Process tool calls
            assistant_msg = {"role": "assistant", "content": msg.get("content") or None}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["function"]["name"],
                                  "arguments": tc["function"]["arguments"]}}
                    for tc in tool_calls
                ]
            self._messages.append(assistant_msg)

            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except Exception:
                    tool_args = {}

                if on_event:
                    await on_event({"type": "tool_call", "name": tool_name, "args": tool_args})

                result = await self._execute_tool(tool_name, tool_args)
                result_text = result["result"]

                if on_event:
                    await on_event({"type": "tool_result", "name": tool_name, "summary": result_text[:300], "metadata": result.get("metadata", {})})

                steps.append({
                    "type": "tool_use",
                    "name": tool_name,
                    "args": tool_args,
                    "result": result_text[:500],
                })

                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })

    def reset(self) -> None:
        self._messages = []
