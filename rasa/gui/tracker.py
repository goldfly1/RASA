import sqlite3
import os
import uuid
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "data", "rasa_gui.db")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    db = get_db()
    db.executescript(sql)
    db.commit()
    db.close()

class Tracker:
    def __init__(self):
        self.db = get_db()

    def close(self):
        self.db.close()

    def add_project(self, name, goal="", description=""):
        pid = str(uuid.uuid4())[:8]
        self.db.execute(
            "INSERT INTO projects (id, name, goal, description) VALUES (?, ?, ?, ?)",
            (pid, name, goal, description)
        )
        self.db.commit()
        self._log(pid, f"Project created: {name}", "success")
        return pid

    def list_projects(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM projects ORDER BY priority DESC, updated_at DESC"
        ).fetchall()]

    def get_project(self, project_id):
        r = self.db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(r) if r else None

    def set_phase(self, project_id, phase):
        self.db.execute(
            "UPDATE projects SET phase = ?, updated_at = datetime('now') WHERE id = ?",
            (phase, project_id)
        )
        self.db.commit()
        self._log(project_id, f"Phase -> {phase}")

    def set_priority(self, project_id, priority):
        self.db.execute(
            "UPDATE projects SET priority = ?, updated_at = datetime('now') WHERE id = ?",
            (priority, project_id)
        )
        self.db.commit()
        self._log(project_id, f"Priority -> {priority}")

    def set_notes(self, project_id, notes):
        self.db.execute(
            "UPDATE projects SET notes = ?, updated_at = datetime('now') WHERE id = ?",
            (notes, project_id)
        )
        self.db.commit()
        self._log(project_id, "Notes updated")

    def add_task(self, project_id, title, soul_id="coder-v2-dev"):
        self.db.execute(
            "INSERT INTO tasks (project_id, title, soul_id) VALUES (?, ?, ?)",
            (project_id, title, soul_id)
        )
        self.db.commit()
        self._log(project_id, f"Task added: {title}")
        return self.db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def list_tasks(self, project_id=None):
        if project_id:
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_task_status(self, task_id, status, orch_task_id=None):
        self.db.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (status, task_id)
        )
        if orch_task_id:
            self.db.execute(
                "UPDATE tasks SET orch_task_id = ? WHERE id = ?",
                (orch_task_id, task_id)
            )
        self.db.commit()

    def get_activity(self, project_id=None, limit=50):
        if project_id:
            rows = self.db.execute(
                "SELECT * FROM activity_log WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _log(self, project_id, message, level="info"):
        self.db.execute(
            "INSERT INTO activity_log (project_id, message, level) VALUES (?, ?, ?)",
            (project_id, message, level)
        )
        self.db.commit()
