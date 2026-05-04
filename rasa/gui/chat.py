"""GUI chat — multi-turn agent conversations with tool execution."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import json

import chevron
import httpx
import yaml

SOULS_DIR = Path(__file__).parent.parent.parent / "souls"

# In-memory conversations: soul_id -> {"soul": dict, "messages": list, "created_at": float}
CONVERSATIONS: dict[str, dict] = {}
MAX_CONVERSATIONS = 10
MAX_TOOL_TURNS = 15  # Prevent infinite tool loops


# ── Tool definitions (OpenAI tool format) ──

TOOL_DEFS = {
    "file_read": {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a file from the project filesystem. Use absolute paths or paths relative to the project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file (absolute or relative to project root)"},
                },
                "required": ["path"],
            },
        },
    },
    "file_write": {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file (absolute or relative to project root)"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "shell_exec": {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Run a shell command in the project root directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    "git_diff": {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff for the working tree. Optionally filter by path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional path to filter diff by"},
                },
            },
        },
    },
}

PROJECT_ROOT = SOULS_DIR.parent


# ── Safety helpers ──

def _check_tool_policy(soul: dict, tool_name: str, args: dict) -> str | None:
    """Check tool against soul sheet policy. Returns None if allowed, or an error string."""
    policy = soul.get("behavior", {}).get("tool_policy", {})
    allowed = policy.get("allowed_tools", [])
    denied = policy.get("denied_tools", [])
    require_confirm = policy.get("require_human_confirm", [])

    if allowed and tool_name not in allowed:
        return f"Tool '{tool_name}' is not in allowed_tools for this agent"

    for pattern in denied:
        if ":" in pattern:
            t, constraint = pattern.split(":", 1)
            if t != tool_name:
                continue
            if constraint.startswith("/") or constraint.startswith("\\"):
                # Path restriction — check if args path matches
                arg_path = args.get("path", "") or args.get("command", "")
                if constraint.rstrip("*") in arg_path:
                    return f"Tool '{tool_name}' denied by policy: {pattern}"
            else:
                # Command restriction — check if command contains this
                cmd = args.get("command", "")
                if constraint in cmd:
                    return f"Tool '{tool_name}' denied by policy: {pattern}"

    for pattern in require_confirm:
        if ":" in pattern:
            t, constraint = pattern.split(":", 1)
            if t == tool_name and constraint in (args.get("command", "") or args.get("path", "")):
                return f"Tool '{tool_name} requires human confirmation: {pattern}"

    return None


# ── Tool execution ──

async def _execute_tool(tool_name: str, args: dict) -> dict:
    """Execute a tool and return {result: str, truncated: bool}."""
    try:
        if tool_name == "file_read":
            path = Path(args["path"])
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            content = path.read_text(encoding="utf-8", errors="replace")
            truncated = len(content) > 10000
            if truncated:
                content = content[:10000] + "\n\n...[truncated]"
            return {"result": content, "truncated": truncated}

        elif tool_name == "file_write":
            path = Path(args["path"])
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return {"result": f"Written {len(args['content'])} bytes to {path}"}

        elif tool_name == "shell_exec":
            cmd = args["command"]
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
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

        elif tool_name == "git_diff":
            cmd = ["git", "diff"]
            if args.get("path"):
                cmd.append(args["path"])
            result = subprocess.run(
                cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip() or "(no changes)"
            return {"result": output}

        else:
            return {"result": f"Unknown tool: {tool_name}"}

    except FileNotFoundError as e:
        return {"result": f"File not found: {e}"}
    except subprocess.TimeoutExpired:
        return {"result": "Command timed out after 60s"}
    except Exception as e:
        return {"result": f"Error: {e}"}


def _get_tool_defs(soul: dict) -> list[dict]:
    """Return tool definitions for the soul sheet's allowed tools."""
    allowed = soul.get("behavior", {}).get("tool_policy", {}).get("allowed_tools", [])
    return [TOOL_DEFS[t] for t in allowed if t in TOOL_DEFS]


async def _llm_call(base_url: str, api_key: str, payload: dict) -> dict:
    """Call the LLM API with retry logic for transient errors."""
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
                if r.status_code in (429, 502, 503):
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
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 502, 503) and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"LLM {e.response.status_code}, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
                continue
            raise
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"LLM error: {e}, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
                continue
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_error}")


# ── Soul / model helpers ──

def _load_soul(soul_id: str) -> dict:
    p = SOULS_DIR / f"{soul_id}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"Soul '{soul_id}' not found in {SOULS_DIR}")
    return yaml.safe_load(p.read_text())


def _render_system_prompt(soul: dict) -> str:
    allowed = soul.get("behavior", {}).get("tool_policy", {}).get("allowed_tools", [])
    tool_infos = []
    for t in allowed:
        td = TOOL_DEFS.get(t)
        if td:
            tool_infos.append({
                "name": t,
                "description": td["function"]["description"],
            })
    ctx: dict = {
        "metadata": soul.get("metadata", {}),
        "agent_role": soul.get("agent_role", ""),
        "model": soul.get("model", {}),
        "behavior": soul.get("behavior", {}),
        "tools": {"enabled": tool_infos},
        "task": {
            "id": "gui-chat",
            "title": "GUI conversation",
            "type": "conversation",
            "description": "",
        },
        "memory": {
            "short_term_summary": "",
            "graph_excerpt": "",
            "semantic_matches": [],
        },
    }
    body = chevron.render(soul["prompt"]["system_template"], ctx)
    if "context_injection" in soul.get("prompt", {}):
        body += "\n\n" + chevron.render(soul["prompt"]["context_injection"], ctx)
    if "tool_use_preamble" in soul.get("prompt", {}):
        body += "\n\n" + chevron.render(soul["prompt"]["tool_use_preamble"], ctx)
    if allowed:
        body += (
            "\n\nYou have access to function-calling tools. When a task requires reading files, "
            "inspecting the codebase, or checking git state, you MUST call the appropriate function.\n"
            + "\n".join(f"- {t}: {TOOL_DEFS[t]['function']['description']}" for t in allowed)
            + "\n\nRules:\n"
            "1. When the user asks you to read a file, call file_read — do not simulate it.\n"
            "2. After the tool result comes back, use it to answer the user.\n"
            "3. You can call multiple tools in sequence.\n"
            "4. Do NOT fabricate file contents. Always use the tool."
        )
    return body.strip()


def _resolve_model(soul: dict) -> tuple[str, str]:
    """Resolve model — canonical pattern: ollama launch claude --model deepseek-v4-pro:cloud."""
    model = (os.environ.get("RASA_MODEL")
             or (os.environ.get("RASA_PREMIUM_MODEL", "deepseek-v4-pro:cloud")
                 if soul.get("model", {}).get("default_tier") == "premium"
                 else os.environ.get("RASA_DEFAULT_MODEL", "deepseek-v4-flash:cloud")))
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    return base_url, model


def _evict_stale() -> None:
    while len(CONVERSATIONS) >= MAX_CONVERSATIONS:
        oldest = min(CONVERSATIONS.keys(), key=lambda k: CONVERSATIONS[k]["created_at"])
        del CONVERSATIONS[oldest]


def list_souls() -> list[dict]:
    souls = []
    for p in sorted(SOULS_DIR.glob("*.yaml")):
        doc = yaml.safe_load(p.read_text())
        if doc and doc.get("cli", {}).get("enabled", True):
            souls.append({
                "id": doc.get("soul_id", p.stem),
                "name": doc.get("metadata", {}).get("name", p.stem),
                "role": doc.get("agent_role", ""),
                "tier": doc.get("model", {}).get("default_tier", "standard"),
            })
    return souls


# ── Main: send + tool loop ──

async def send_message(soul_id: str, text: str) -> dict:
    """Send a message to an agent. Runs tool loop until final response."""
    if soul_id not in CONVERSATIONS:
        soul = _load_soul(soul_id)
        system = _render_system_prompt(soul)
        CONVERSATIONS[soul_id] = {
            "soul": soul,
            "messages": [{"role": "system", "content": system}],
            "created_at": time.time(),
        }
        _evict_stale()

    session = CONVERSATIONS[soul_id]
    session["messages"].append({"role": "user", "content": text})

    base_url, model = _resolve_model(session["soul"])
    model_cfg = session["soul"].get("model", {})
    tool_defs = _get_tool_defs(session["soul"])
    api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
    start = time.time()
    steps: list[dict] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0

    async def _run_tool_loop() -> dict:
        nonlocal total_prompt_tokens, total_completion_tokens
        for turn in range(MAX_TOOL_TURNS):
            payload: dict[str, Any] = {
                "model": model,
                "messages": session["messages"],
                "stream": False,
                "temperature": model_cfg.get("temperature", 0.2),
                "max_tokens": model_cfg.get("max_tokens", 4096),
            }
            if tool_defs:
                payload["tools"] = tool_defs

            data = await _llm_call(base_url, api_key, payload)

            choice = data["choices"][0]
            msg = choice["message"]
            usage = data.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            # Check for tool calls
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                # Final text response
                reply = msg.get("content", "") or ""
                session["messages"].append({"role": "assistant", "content": reply})
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
            session["messages"].append(assistant_msg)

            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except Exception:
                    tool_args = {}

                # Safety check
                policy_error = _check_tool_policy(session["soul"], tool_name, tool_args)
                if policy_error:
                    result_text = policy_error
                else:
                    exec_result = await _execute_tool(tool_name, tool_args)
                    result_text = exec_result["result"]

                steps.append({
                    "type": "tool_use",
                    "name": tool_name,
                    "args": tool_args,
                    "result": result_text[:500],
                })

                session["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })

        # If we hit MAX_TOOL_TURNS, return accumulated context
        reply = "I've reached the limit of tool operations for this turn. Let me summarize what I've done so far."
        session["messages"].append({"role": "assistant", "content": reply})
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
        }

    try:
        return await asyncio.wait_for(_run_tool_loop(), timeout=300.0)
    except asyncio.TimeoutError:
        elapsed = time.time() - start
        return {
            "reply": "The request timed out after 5 minutes. Try resetting the conversation and sending a shorter message.",
            "steps": steps,
            "model": model,
            "usage": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
            },
            "elapsed_seconds": round(elapsed, 1),
        }


def reset_conversation(soul_id: str) -> None:
    CONVERSATIONS.pop(soul_id, None)
