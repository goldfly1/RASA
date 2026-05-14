import json
import os
import sys
import time

class OrchestratorCLI:
    def __init__(self, output_callback=None):
        self.output = output_callback or print
        self._delegator = None

    def _get_delegator(self):
        if self._delegator is None:
            from rasa.orchestrator.delegator import TaskDelegator
            self._delegator = TaskDelegator()
        return self._delegator

    def execute(self, line):
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
            else:
                return self._send_to_orchestrator(line)
        except Exception as e:
            return f"Error: {e}"

    def _help(self):
        return (
            "Commands:\n"
            "  <anything else>                    - Send message to the Orchestrator\n"
            "  list [status]                      - List recent tasks\n"
            "  status <task-id>                   - Query task status\n"
            "  capabilities                       - List agent capabilities\n"
            "  match <description>                - Find best agent for a task\n"
            "  cancel <task-id>                   - Cancel a task\n"
            "  retry <task-id>                    - Retry a failed task\n"
            "  help                               - This message"
        )

    def _send_to_orchestrator(self, line):
        self.output(f"Sending to Orchestrator: {line[:80]}...")
        d = self._get_delegator()
        tid = d.create_task(
            soul_id="orchestrator-v1",
            title=line[:80],
            description=line
        )
        d.assign_task(tid)
        self.output(f"Task {tid[:8]} assigned to orchestrator-v1")
        self.output("Waiting for orchestrator response...")

        # Poll for up to 60 seconds
        deadline = time.time() + 60.0
        while time.time() < deadline:
            t = d.query_task(tid)
            if t and t.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                if t["status"] == "COMPLETED":
                    result = t.get("result")
                    if isinstance(result, dict):
                        reply = result.get("content") or result.get("reply") or json.dumps(result, default=str)
                        return f"Orchestrator: {reply}"
                    return f"Orchestrator: {result}"
                elif t["status"] == "FAILED":
                    return f"Orchestrator failed: {t.get('error_message', 'unknown error')}"
                else:
                    return f"Task cancelled."
            time.sleep(2.0)
        return f"Orchestrator did not respond within 60s. Task {tid[:8]} is {t.get('status', '?') if t else '?'}. Check 'status {tid[:8]}' later."

    def _list_tasks(self, args):
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

    def _status(self, args):
        if not args:
            return "Usage: status <task-id>"
        d = self._get_delegator()
        t = d.query_task(args[0])
        if not t:
            return f"Task {args[0][:8]} not found"
        return json.dumps(t, indent=2, default=str)

    def _cap_list(self):
        from rasa.orchestrator.capabilities import CapabilityRegistry
        caps = CapabilityRegistry()
        out = []
        for c in caps.list_capabilities():
            out.append(f"  {c['soul_id']} ({c['agent_role']}): {c['description'][:80]}")
        return chr(10).join(out) if out else "No capabilities registered."

    def _match(self, args):
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

    def _cancel(self, args):
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
                    (args[0],)
                )
                if cur.rowcount:
                    conn.commit()
                    return f"Task {args[0][:8]} -> CANCELLED"
                return f"Task {args[0][:8]} not in cancellable state"

    def _retry(self, args):
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
                    (args[0],)
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
