"""Agent-level tool definitions and implementations for the agent runtime.

These are the low-level tools agents use to interact with the codebase:
file_read, file_write, shell_exec, git_diff, and human interaction tools.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.parent

# ── Tool definitions (OpenAI function-calling format) ──

AGENT_TOOL_DEFS: dict[str, dict] = {
    "file_read": {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a file from the project. Path is relative to the project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path (e.g. rasa/gui/server.py)"},
                    "offset": {"type": "integer", "description": "Line number to start from (optional)"},
                    "limit": {"type": "integer", "description": "Number of lines to read (optional)"},
                },
                "required": ["path"],
            },
        },
    },
    "file_write": {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file. Creates parent directories if needed. Path is relative to project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
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
            "description": "Execute a shell command in the project directory. Returns stdout + stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
                },
                "required": ["command"],
            },
        },
    },
    "git_diff": {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show uncommitted changes (git diff) or diff between branches/commits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Optional git ref (e.g. 'main', 'HEAD~3'). Defaults to unstaged diff."},
                },
            },
        },
    },
    "request_human_input": {
        "type": "function",
        "function": {
            "name": "request_human_input",
            "description": "Request guidance from the human operator when blocked or uncertain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why human input is needed"},
                    "payload": {"type": "object", "description": "Optional context (options, data, etc.)"},
                },
                "required": ["reason"],
            },
        },
    },
    "check_human_response": {
        "type": "function",
        "function": {
            "name": "check_human_response",
            "description": "Check if the human has responded to a previous request_human_input call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "review_id": {"type": "string", "description": "Review UUID from request_human_input"},
                },
                "required": ["review_id"],
            },
        },
    },
}

# ── Tool implementations ──

# Paths that file_write is NOT allowed to touch
_PROTECTED_PREFIXES = [
    ".venv", "__pycache__", "node_modules", ".git", ".env",
]
_PROTECTED_FILE_PATTERNS = [".env", "*.pem", "*.key", "credentials*"]


def _resolve_path(path: str) -> Path:
    """Resolve a relative path and validate it's within the project."""
    full = (PROJECT_ROOT / path).resolve()
    if not str(full).startswith(str(PROJECT_ROOT.resolve())):
        raise PermissionError(f"Path '{path}' is outside the project root")
    return full


async def tool_file_read(path: str, offset: int = 0, limit: int = 0) -> dict:
    """Read a file from the project."""
    try:
        full = _resolve_path(path)
        if not full.exists():
            return {"error": f"File not found: {path}"}
        if not full.is_file():
            return {"error": f"Not a file: {path}"}
        text = full.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        if offset:
            lines = lines[offset:]
        if limit:
            lines = lines[:limit]
        return {"content": "\n".join(lines), "total_lines": len(text.split("\n"))}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Read failed: {e}"}


async def tool_file_write(path: str, content: str) -> dict:
    """Write content to a file within the project."""
    try:
        full = _resolve_path(path)
        # Protected path check
        for prefix in _PROTECTED_PREFIXES:
            if prefix in full.parts:
                return {"error": f"Cannot write to protected path: {path}"}
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"success": True, "path": path, "bytes": len(content)}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Write failed: {e}"}


async def tool_shell_exec(command: str, timeout: int = 30) -> dict:
    """Execute a shell command in the project directory."""
    # Basic safety: block destructive commands if they'd be too broad
    dangerous = ["rm -rf /", "rm -rf ~", "mkfs", "dd if=", ":(){ :|:& };:"]
    for d in dangerous:
        if d in command:
            return {"error": f"Command blocked for safety: contains dangerous pattern"}
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"error": f"Shell exec failed: {e}"}


async def tool_git_diff(target: str = "") -> dict:
    """Show git diff."""
    try:
        cmd = ["git", "diff"]
        if target:
            cmd.append(target)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {"diff": stdout.decode(errors="replace")}
    except Exception as e:
        return {"error": f"Git diff failed: {e}"}


async def tool_request_human_input(reason: str, payload: dict | None = None) -> dict:
    """Request human input via the review channel."""
    try:
        from rasa.orchestrator.reviews import ReviewManager
        rm = ReviewManager()
        review = rm.create_review(
            task_id="",
            agent_id="agent-runtime",
            reason=reason,
            payload=payload or {},
        )
        return {"review_id": review["id"], "status": "pending", "message": f"Request sent: {reason}"}
    except Exception as e:
        return {"error": f"Failed to request human input: {e}"}


async def tool_check_human_response(review_id: str) -> dict:
    """Check if human has responded to a review request."""
    try:
        from rasa.orchestrator.reviews import ReviewManager
        rm = ReviewManager()
        review = rm.get_review(review_id)
        if not review:
            return {"error": f"Review not found: {review_id}"}
        if review["status"] == "answered":
            return {"status": "answered", "response": review.get("response", "")}
        return {"status": "pending", "response": None}
    except Exception as e:
        return {"error": f"Failed to check: {e}"}


# ── Dispatch ──

TOOL_IMPL = {
    "file_read": tool_file_read,
    "file_write": tool_file_write,
    "shell_exec": tool_shell_exec,
    "git_diff": tool_git_diff,
    "request_human_input": tool_request_human_input,
    "check_human_response": tool_check_human_response,
}


async def execute_tool(name: str, args: dict) -> dict:
    """Execute a tool by name with arguments. Returns result dict."""
    impl = TOOL_IMPL.get(name)
    if not impl:
        return {"error": f"Unknown tool: {name}"}
    try:
        return await impl(**args)
    except TypeError as e:
        return {"error": f"Invalid arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
