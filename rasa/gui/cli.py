import json
import os
import sys

class OrchestratorCLI:
    def __init__(self, output_callback=None):
        self.output = output_callback or print
        self._delegator = None
        self._capabilities = None

    def _get_delegator(self):
        if self._delegator is None:
            from rasa.orchestrator.delegator import TaskDelegator
            self._delegator = TaskDelegator()
        return self._delegator

    def _get_capabilities(self):
        if self._capabilities is None:
            from rasa.orchestrator.capabilities import CapabilityRegistry
            self._capabilities = CapabilityRegistry()
        return self._capabilities

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
            elif cmd == "submit":
                return self._submit(args)
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
                return self._submit_auto(line)
        except Exception as e:
            return f"Error: {e}"

    def _help(self):
        return (
            "Commands:\n"
            "  submit <soul> <title> [goal...]  - Create and assign a task\n"
            "  list [status]                      - List recent tasks\n"
            "  status <task-id>                   - Query task status\n"
            "  capabilities                       - List agent capabilities\n"
            "  match <description>                - Find best agent for a task\n"
            "  cancel <task-id>                   - Cancel a task\n"
            "  retry <task-id>                    - Retry a failed task\n"
            "  help                               - This message\n"
            "  <anything else>                    - Submit as goal to default agent"
        )

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

    def _submit(self, args):
        if len(args) < 2:
            return "Usage: submit <soul-id> <title> [goal...]"
        soul_id = args[0]
        title = args[1]
        goal = " ".join(args[2:]) if len(args) > 2 else title
        d = self._get_delegator()
        tid = d.create_task(soul_id=soul_id, title=title, description=goal)
        d.assign_task(tid)
        return f"Task created: {tid[:8]} (soul={soul_id}, {title})"

    def _submit_auto(self, line):
        d = self._get_delegator()
        caps = self._get_capabilities()
        best = caps.find_best_soul(line, line[:60])
        tid = d.create_task(soul_id=best or "coder-v2-dev", title=line[:80], description=line)
        d.assign_task(tid)
        return f"Task {tid[:8]} -> {best or 'coder-v2-dev'} ({line[:50]}...)"

    def _status(self, args):
        if not args:
            return "Usage: status <task-id>"
        d = self._get_delegator()
        t = d.query_task(args[0])
        if not t:
            return f"Task {args[0][:8]} not found"
        return json.dumps(t, indent=2, default=str)

    def _cap_list(self):
        caps = self._get_capabilities()
        out = []
        for c in caps.list_capabilities():
            out.append(f"  {c['soul_id']} ({c['agent_role']}): {c['description'][:80]}")
        return chr(10).join(out) if out else "No capabilities registered."

    def _match(self, args):
        if not args:
            return "Usage: match <task description>"
        desc = " ".join(args)
        caps = self._get_capabilities()
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
