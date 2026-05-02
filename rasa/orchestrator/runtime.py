"""OrchestratorRuntime — multi-turn agent with task delegation capabilities."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import chevron
import httpx
import yaml

from rasa.orchestrator.capabilities import CapabilityRegistry
from rasa.orchestrator.delegator import TaskDelegator
from rasa.orchestrator.project import ProjectManager
from rasa.orchestrator.tools import ORCHESTRATOR_TOOL_DEFS

SOULS_DIR = Path(__file__).parent.parent.parent / "souls"
PROJECT_ROOT = SOULS_DIR.parent
MAX_TOOL_TURNS = 20


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
) -> str:
    allowed = soul.get("behavior", {}).get("tool_policy", {}).get("allowed_tools", [])
    from rasa.gui.chat import TOOL_DEFS as CHAT_TOOL_DEFS
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
            "After assigning, use task_query to check results."
        )
    return body.strip()


# ── Runtime ──

class OrchestratorRuntime:
    """Multi-turn orchestrator with task delegation and project tracking."""

    def __init__(self):
        self.soul = _load_soul("orchestrator-v1")
        self.delegator = TaskDelegator()
        self.project_mgr = ProjectManager()
        self._messages: list[dict] = []
        self._project_id: str | None = None
        self._mode: str = "step_by_step"  # or "autonomous"

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
        return _render_system_prompt(self.soul, summary, task_ctx, caps)

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
                return {"result": f"Task created: {tid} (soul={soul_id}, status=PENDING)"}

            elif tool_name == "task_assign":
                task_id = args["task_id"]
                soul = self.delegator.assign_task(task_id)
                if soul:
                    return {"result": f"Task {task_id} assigned to {soul}"}
                else:
                    return {"result": f"Task {task_id} not found or not in PENDING state"}

            elif tool_name == "task_query":
                task_id = args["task_id"]
                task = self.delegator.query_task(task_id)
                if task:
                    return {"result": json.dumps(task, indent=2)}
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

            else:
                return {"result": f"Unknown tool: {tool_name}"}

        except Exception as e:
            return {"result": f"Error executing {tool_name}: {e}"}

    async def _llm_call(self, base_url: str, api_key: str, payload: dict) -> dict:
        """Call the LLM API with retry logic for transient errors."""
        TRANSIENT = (429, 502, 503)
        max_retries = 3
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as c:
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
                        print(f"LLM transient error {r.status_code}, retrying in {wait}s "
                              f"(attempt {attempt + 1}/{max_retries})", flush=True)
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
                raise RuntimeError(f"LLM call failed: {e.response.status_code}")
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"LLM error: {e}, retrying in {wait}s "
                          f"(attempt {attempt + 1}/{max_retries})", flush=True)
                    await asyncio.sleep(wait)
                    continue
        raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_error}")

    async def send_message(self, text: str) -> dict:
        """Send a message to the orchestrator. Returns reply + steps."""
        # Initialize conversation on first message
        if not self._messages:
            system = self._render_system()
            self._messages.append({"role": "system", "content": system})

        self._messages.append({"role": "user", "content": text})

        # Model config
        model_cfg = self.soul.get("model", {})
        tier = model_cfg.get("default_tier", "premium")
        if tier == "premium":
            model = os.environ.get("RASA_PREMIUM_MODEL", "deepseek-v4-pro:cloud")
        else:
            model = os.environ.get("RASA_DEFAULT_MODEL", "deepseek-v4-flash:cloud")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
        tool_defs = self._get_tool_defs()

        start = time.time()
        steps: list[dict] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for turn in range(MAX_TOOL_TURNS):
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
                return {
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
                }

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

                result = await self._execute_tool(tool_name, tool_args)
                result_text = result["result"]

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

        # MAX_TOOL_TURNS fallback
        reply = "I've reached the tool operation limit. Let me summarize what I've done."
        self._messages.append({"role": "assistant", "content": reply})
        elapsed = time.time() - start
        return {
            "reply": reply,
            "steps": steps,
            "model": model,
            "usage": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
            },
            "elapsed_seconds": round(elapsed, 1),
            "project_id": self._project_id,
            "mode": self._mode,
        }

    def reset(self) -> None:
        self._messages = []
