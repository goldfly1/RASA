"""Orchestrator CLI - communicates with persistent orch daemon via stdin/stdout pipes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


class OrchestratorCLI:
    """CLI that talks to a persistent orchestrator daemon subprocess.
    
    Non-conversation commands (list, status, etc.) still use DB directly.
    Conversational messages route through the daemon for real-time responses.
    """

    def __init__(self, output_callback=None):
        self.output = output_callback or print
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._response_ready = threading.Event()
        self._last_response: str = ""
        self._last_error: str = ""
        self._partial: list[str] = []  # for streaming tool_call/tool_result events
        self._last_event_time: float = 0.0

    def _get_delegator(self):
        from rasa.orchestrator.delegator import TaskDelegator
        return TaskDelegator()

    @property
    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Launch the orchestrator daemon subprocess."""
        if self.is_running:
            return
        with self._lock:
            if self.is_running:
                return
            self.output("[system] Starting orchestrator daemon...")
            env = os.environ.copy()
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                self._proc = subprocess.Popen(
                    [sys.executable, "-m", "rasa.gui.orch_daemon"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    text=True,
                    bufsize=1,
                    creationflags=creationflags,
                )
            except Exception as e:
                self.output(f"[system] Failed to start orchestrator daemon: {e}")
                self._proc = None
                return

            self._running = True
            self._reader_thread = threading.Thread(target=self._read_responses, daemon=True)
            self._reader_thread.start()
            # Also read stderr in background
            threading.Thread(target=self._read_stderr, daemon=True).start()
            self.output("[system] Orchestrator daemon started (pid=" + str(self._proc.pid) + ").")

    def stop(self) -> None:
        """Stop the orchestrator daemon."""
        with self._lock:
            self._running = False
            if self._proc:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
            self.output("[system] Orchestrator daemon stopped.")

    def _read_stderr(self) -> None:
        """Read stderr from daemon for debugging."""
        while self._running and self._proc and self._proc.poll() is None:
            try:
                line = self._proc.stderr.readline()
                if line:
                    self.output(f"[orch-err] {line.rstrip()}")
                else:
                    break
            except Exception:
                break

    def _read_responses(self) -> None:
        """Read JSON-line responses from daemon stdout."""
        turn_tools: list[str] = []
        current_turn: int = 0

        def _flush_tools() -> None:
            if turn_tools:
                names = ", ".join(turn_tools)
                self.output(f"  Turn {current_turn} - tools: {names} ({len(turn_tools)} calls)")
                turn_tools.clear()

        while self._running and self._proc and self._proc.poll() is None:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                self._last_event_time = time.time()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")
                if etype == "thinking":
                    _flush_tools()
                    current_turn = event.get("turn", current_turn + 1)
                    self.output(f"  ... thinking (turn {current_turn}) ...")
                elif etype == "tool_call":
                    turn_tools.append(event.get("name", "?"))
                elif etype == "tool_result":
                    pass  # condensed into turn summary
                elif etype == "reply":
                    pass  # final reply comes in 'done'
                elif etype == "done":
                    _flush_tools()
                    result = event.get("result", {})
                    reply = result.get("reply", "(no response)")
                    self._last_response = reply
                    self._response_ready.set()
                elif etype == "reset_ok":
                    self._last_response = "Conversation reset."
                    self._response_ready.set()
                elif etype == "error":
                    self._last_error = event.get("message", "Unknown error")
                    self._last_response = f"Error: {self._last_error}"
                    self._response_ready.set()
            except Exception:
                break
        # Process exited
        self._running = False
        self.output("[system] Orchestrator daemon disconnected.")

    def _send_to_daemon(self, text: str) -> str:
        """Send a message to the daemon and wait for response."""
        if not self.is_running:
            return "Error: Orchestrator daemon is not running. Check service status or click START ALL."

        self._response_ready.clear()
        self._last_event_time = time.time()  # seed idle timer
        self._last_response = ""
        self._last_error = ""

        try:
            request = json.dumps({"text": text}) + "\n"
            self._proc.stdin.write(request)
            self._proc.stdin.flush()
        except Exception as e:
            self._running = False
            return f"Error writing to orchestrator: {e}"

        # Wait for response, resetting idle timer on each event
        idle_timeout = 300.0
        while not self._response_ready.is_set():
            elapsed = time.time() - self._last_event_time
            if elapsed > idle_timeout:
                if self._last_response:
                    return self._last_response
                return "Orchestrator idle for " + str(int(elapsed)) + "s. The daemon may be stuck."
            time.sleep(0.5)

        return self._last_response or "(empty response)"

    def execute(self, line: str) -> str:
        """Parse and execute a CLI command."""
        line = line.strip()
        if not line:
            return ""
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd == "help":
                return self._help()
            elif cmd == "list":
                return self._list_tasks(args)
            elif cmd == "status":
                return self._status(args)
            elif cmd == "capabilities":
                return self._cap_list()
            elif cmd == "match":
                return self._match(args)
            elif cmd == "cancel":
                return self._cancel(args)
            elif cmd == "retry":
                return self._retry(args)
            elif cmd == "reset":
                return self._reset_conversation()
            else:
                return self._send_to_daemon(line)
        except Exception as e:
            return f"Error: {e}"

    def _help(self) -> str:
        return (
            "Commands:\n"
            "  <anything else>    - Send message to the Orchestrator (live IPC)\n"
            "  reset              - Clear conversation history\n"
            "  list [status]      - List recent tasks\n"
            "  status <task-id>   - Query task status\n"
            "  capabilities       - List agent capabilities\n"
            "  match <desc>       - Find best agent for a task\n"
            "  cancel <task-id>   - Cancel a task\n"
            "  retry <task-id>    - Retry a failed task\n"
            "  help               - This message"
        )

    def _reset_conversation(self) -> str:
        if not self.is_running:
            return "Orchestrator daemon is not running."
        self._response_ready.clear()
        self._last_event_time = time.time()  # seed idle timer
        try:
            request = json.dumps({"action": "reset"}) + "\n"
            self._proc.stdin.write(request)
            self._proc.stdin.flush()
        except Exception as e:
            return f"Error: {e}"
        if not self._response_ready.wait(timeout=10.0):
            return "Reset timed out."
        return self._last_response

    # --- DB-backed commands (unchanged from original) ---

    def _list_tasks(self, args) -> str:
        d = self._get_delegator()
        tasks = d.list_project_tasks()
        if not tasks:
            return "No tasks found."
        out = []
        for t in tasks[:20]:
            tid = t["id"][:8]
            status = t["status"]
            title = (t["title"] or "")[:60]
            out.append(f"  [{status}] {tid}  {title}")
        return chr(10).join(out)

    def _status(self, args) -> str:
        if not args:
            return "Usage: status <task-id>"
        d = self._get_delegator()
        t = d.query_task(args[0])
        if not t:
            return f"Task {args[0][:8]} not found"
        return json.dumps(t, indent=2, default=str)

    def _cap_list(self) -> str:
        from rasa.orchestrator.capabilities import CapabilityRegistry
        caps = CapabilityRegistry()
        out = []
        for c in caps.list_capabilities():
            out.append(f"  {c['soul_id']} ({c['agent_role']}): {c['description'][:80]}")
        return chr(10).join(out) if out else "No capabilities registered."

    def _match(self, args) -> str:
        if not args:
            return "Usage: match <task description>"
        desc = " ".join(args)
        from rasa.orchestrator.capabilities import CapabilityRegistry
        caps = CapabilityRegistry()
        scored = caps.score_match(desc)
        if not scored:
            return "No matching agents found."
        out = []
        for s in scored[:5]:
            out.append(f"  {s['soul_id']} score={s.get('_score', 0):.1f}  {s['description'][:60]}")
        return chr(10).join(out)

    def _cancel(self, args) -> str:
        if not args:
            return "Usage: cancel <task-id>"
        import psycopg
        dsn_vals = {
            "host": os.environ.get("RASA_DB_HOST", "localhost"),
            "port": os.environ.get("RASA_DB_PORT", "5432"),
            "user": os.environ.get("RASA_DB_USER", "postgres"),
            "password": os.environ.get("RASA_DB_PASSWORD", "8764"),
            "dbname": "rasa_orch",
        }
        dsn = " ".join(f"{k}={v}" for k, v in dsn_vals.items())
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status = 'CANCELLED', updated_at = NOW() WHERE id = %s AND status IN ('PENDING', 'ASSIGNED')",
                    (args[0],),
                )
                if cur.rowcount:
                    conn.commit()
                    return f"Task {args[0][:8]} -> CANCELLED"
                return f"Task {args[0][:8]} not in cancellable state"

    def _retry(self, args) -> str:
        if not args:
            return "Usage: retry <task-id>"
        import psycopg
        dsn_vals = {
            "host": os.environ.get("RASA_DB_HOST", "localhost"),
            "port": os.environ.get("RASA_DB_PORT", "5432"),
            "user": os.environ.get("RASA_DB_USER", "postgres"),
            "password": os.environ.get("RASA_DB_PASSWORD", "8764"),
            "dbname": "rasa_orch",
        }
        dsn = " ".join(f"{k}={v}" for k, v in dsn_vals.items())
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status::text, COALESCE(retry_count, 0), soul_id FROM tasks WHERE id = %s",
                    (args[0],),
                )
                row = cur.fetchone()
                if not row:
                    return f"Task {args[0][:8]} not found"
                if row[0] not in ("FAILED", "CANCELLED"):
                    return f"Task {args[0][:8]} is {row[0]} - can only retry FAILED or CANCELLED"
                cur.execute(
                    "UPDATE tasks SET status = 'PENDING', retry_count = %s, assigned_agent_id = NULL, updated_at = NOW(), completed_at = NULL WHERE id = %s",
                    (row[1] + 1, args[0])
                )
                conn.commit()
                return f"Task {args[0][:8]} -> PENDING (retry #{row[1] + 1})"
