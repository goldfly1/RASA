"""Interactive orchestrator REPL — terminal-native chat replacing the Tkinter chat pane."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from rasa.orchestrator.delegator import TaskDelegator
from rasa.orchestrator.project import ProjectManager
from rasa.orchestrator.runtime import OrchestratorRuntime


HELP_TEXT = """\
Available commands:
  /projects              List all projects
  /project <id|name>     Select or create a project
  /mode [step|auto]      Show or set mode (step_by_step / autonomous)
  /tasks                 List tasks for current project
  /task <id>             Query a specific task
  /reset                 Clear conversation history
  /help                  Show this help
  /quit, /exit, Ctrl+D   Exit

Type anything else to send a message to the orchestrator.
"""


def _resolve_model_env() -> tuple[str, str, str]:
    """Return (base_url, api_key, model)."""
    tier = "premium"  # orchestrator always uses premium
    model = os.environ.get("RASA_PREMIUM_MODEL", "deepseek-v4-pro:cloud")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
    return base_url, api_key, model


def _run_async(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in event loop — use nested (works for prompt_toolkit's asyncio loop)
    return loop.run_until_complete(coro)


class ReplSession:
    """Holds REPL state and orchestrator integration."""

    def __init__(self):
        self.runtime = OrchestratorRuntime()
        self.project_mgr = ProjectManager()
        self.delegator = TaskDelegator()

    @property
    def project_id(self) -> str | None:
        return self.runtime.project_id

    @property
    def mode(self) -> str:
        return self.runtime.get_mode()

    def send(self, text: str) -> dict:
        return _run_async(self.runtime.send_message(text))


def run_repl() -> None:
    """Entry point for `rasa repl`."""
    session = ReplSession()
    _print_banner(session)

    # Try prompt_toolkit first; fall back to raw input()
    try:
        _repl_prompt_toolkit(session)
    except ImportError:
        _repl_fallback(session)


def _print_banner(session: ReplSession) -> None:
    proj_id = session.project_id
    proj_label = "(none)"
    if proj_id:
        proj = session.project_mgr.get_project(proj_id)
        if proj:
            proj_label = proj.get("name", proj_id[:8])

    print(f"\n  RASA Orchestrator REPL")
    print(f"  Project: {proj_label}   Mode: {session.mode}")
    print(f"  Type /help for commands, /quit to exit\n")


def _repl_prompt_toolkit(session: ReplSession) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    completer = WordCompleter([
        "/projects", "/project", "/mode", "/tasks", "/task",
        "/reset", "/help", "/quit", "/exit",
        "step_by_step", "autonomous", "step", "auto",
    ], ignore_case=True, sentence=True)

    kb = KeyBindings()

    @kb.add("c-d")
    def _(event):
        event.app.exit()

    style = Style.from_dict({
        "prompt": "#58a6ff bold",
        "separator": "#8b949e",
    })

    pt_session = PromptSession(
        history=FileHistory(os.path.expanduser("~/.rasa_repl_history")),
        completer=completer,
        key_bindings=kb,
        style=style,
    )

    def get_prompt():
        p = session.project_id
        pid = p[:8] if p else "none"
        return [
            ("class:prompt", f"rasa:{pid}"),
            ("class:separator", " > "),
        ]

    while True:
        try:
            line = pt_session.prompt(get_prompt, multiline=False)
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        line = line.strip()
        if not line:
            continue

        if _dispatch_slash(session, line):
            continue

        # Send to orchestrator
        _print_thinking()
        start = time.time()
        try:
            result = session.send(line)
        except Exception as e:
            print(f"\n  [Error] {e}\n")
            continue

        duration = time.time() - start
        _print_response(result, duration)


def _repl_fallback(session: ReplSession) -> None:
    """Fallback REPL using plain input()."""
    while True:
        try:
            proj_id = session.project_id
            pid = proj_id[:8] if proj_id else "none"
            line = input(f"rasa:{pid} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not line:
            continue

        if _dispatch_slash(session, line):
            continue

        print("  ...", end="", flush=True)
        start = time.time()
        try:
            result = session.send(line)
        except Exception as e:
            print(f"\r  [Error] {e}")
            continue

        duration = time.time() - start
        print(f"\r  ({duration:.1f}s)")
        _print_response(result, duration)


def _dispatch_slash(session: ReplSession, line: str) -> bool:
    """Handle a slash command. Returns True if handled."""
    if not line.startswith("/"):
        return False

    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit", "/q"):
        print("Goodbye.")
        sys.exit(0)

    elif cmd == "/help":
        print(HELP_TEXT)

    elif cmd == "/projects":
        projects = session.project_mgr.list_projects()
        if not projects:
            print("  No projects found. Use /project <name> to create one.")
        else:
            print(f"  {'NAME':<30} {'ID':<38} {'STATUS':<10}")
            print(f"  {'-'*30} {'-'*38} {'-'*10}")
            for p in projects:
                pid = p["id"][:36]
                name = p["name"][:28]
                status = p.get("status", "")
                current = " <" if p["id"] == session.project_id else ""
                print(f"  {name:<30} {pid:<38} {status:<10}{current}")

    elif cmd == "/project":
        if not arg:
            print("  Usage: /project <id-or-name>")
            print("  Use /projects to list available projects")
            return True
        projects = session.project_mgr.list_projects()
        match = None
        for p in projects:
            if p["id"].startswith(arg) or p.get("name", "").lower() == arg.lower():
                match = p
                break
        if match:
            session.runtime.set_project(match["id"])
            print(f"  Selected project: {match.get('name', 'unnamed')} ({match['id'][:12]})")
        else:
            # Auto-create
            print(f"  Creating new project: {arg}")
            p = session.project_mgr.create_project(arg, "")
            session.runtime.set_project(p["id"])
            print(f"  Created: {p['id'][:12]}")

    elif cmd == "/mode":
        mode = session.mode
        if arg in ("step", "step_by_step"):
            session.runtime.set_mode("step_by_step")
            print("  Mode: step_by_step")
        elif arg in ("auto", "autonomous"):
            session.runtime.set_mode("autonomous")
            print("  Mode: autonomous")
        else:
            print(f"  Current mode: {mode}")
            print("  Use /mode step  or  /mode auto  to change")

    elif cmd == "/tasks":
        pid = session.project_id
        if not pid:
            print("  No project selected. Use /project <name> first.")
            return True
        tasks = session.delegator.list_project_tasks(pid)
        if not tasks:
            print("  No tasks yet. Start a conversation to create work.")
        else:
            print(f"  {'STATUS':<14} {'TITLE':<40} {'ID'}")
            print(f"  {'-'*14} {'-'*40} {'-'*12}")
            for t in tasks:
                tid = t["id"][:12]
                title = t["title"][:38]
                status = t["status"]
                print(f"  {status:<14} {title:<40} {tid}")

    elif cmd == "/task":
        if not arg:
            print("  Usage: /task <task-id>")
            return True
        task = session.delegator.query_task(arg)
        if not task:
            print(f"  Task not found: {arg}")
        else:
            print(f"  ID:          {task['id']}")
            print(f"  Title:       {task['title']}")
            print(f"  Status:      {task['status']}")
            print(f"  Soul:        {task.get('soul_id', '')}")
            print(f"  Created:     {task.get('created_at', '')}")
            if task.get("result"):
                print(f"  Result:\n{task['result'][:500]}")
            if task.get("error_message"):
                print(f"  Error:       {task['error_message']}")

    elif cmd == "/reset":
        session.runtime.reset()
        print("  Conversation reset. Starting fresh.")

    else:
        print(f"  Unknown command: {cmd}. Type /help for available commands.")

    return True


def _print_thinking() -> None:
    print("  ...", end="", flush=True)


def _print_response(result: dict, duration: float) -> None:
    # Clear the thinking indicator
    print(f"\r  ({duration:.1f}s)")

    if "error" in result:
        print(f"  [Error] {result['error']}")
        return

    # Tool steps
    for step in result.get("steps", []):
        name = step.get("name", "tool")
        args = step.get("args", {})
        step_result = step.get("result", "")
        arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
        print(f"    \033[32m└ {name}({arg_str})\033[0m")
        if step_result:
            preview = step_result[:120].replace("\n", " ")
            print(f"      \033[90m{preview}\033[0m")

    # Reply
    reply = result.get("reply", "")
    print(f"  {reply}")

    # Metadata
    model = result.get("model", "")
    usage = result.get("usage", {})
    elapsed = result.get("elapsed_seconds", duration)
    pt = usage.get("prompt_tokens", 0)
    ct = usage.get("completion_tokens", 0)
    print(f"  \033[90m── {model.split('/')[-1]} · {pt}+{ct} tok · {elapsed}s\033[0m")
    print()
