"""Project state manager — CRUD for cross-session project persistence."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import psycopg


def _dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "8764")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_orch"


class ProjectManager:
    """Manages project lifecycle and cross-session state persistence."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or _dsn()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def create_project(self, name: str, goal: str = "", description: str = "") -> dict[str, Any]:
        """Create a new project. Returns the project dict."""
        project_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO projects (id, name, description, goal, status, metadata)
                       VALUES (%s, %s, %s, %s, 'active', '{}')""",
                    (project_id, name, description, goal),
                )
            conn.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """Get project by ID with task summary."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, name, description, goal, status, root_task_id,
                              created_at, updated_at, metadata
                       FROM projects WHERE id = %s""",
                    (project_id,),
                )
                row = cur.fetchone()
        if not row:
            return None

        # Count tasks
        with self._connect() as conn:
            with conn.cursor() as cur:
                root_id = str(row[5]) if row[5] else None
                if root_id:
                    cur.execute(
                        """SELECT status, COUNT(*) FROM tasks
                           WHERE id IN (
                               WITH RECURSIVE pt AS (
                                   SELECT id FROM tasks WHERE id = %s
                                   UNION ALL
                                   SELECT t.id FROM tasks t JOIN pt ON t.parent_id = pt.id
                               ) TABLE pt
                           ) GROUP BY status""",
                        (root_id,),
                    )
                else:
                    cur.execute(
                        "SELECT status, COUNT(*) FROM tasks GROUP BY status"
                    )
                counts = {r[0]: r[1] for r in cur.fetchall()}

        return {
            "id": str(row[0]),
            "name": row[1],
            "description": row[2],
            "goal": row[3],
            "status": row[4],
            "root_task_id": str(row[5]) if row[5] else None,
            "created_at": str(row[6]),
            "updated_at": str(row[7]),
            "task_counts": counts,
        }

    def list_projects(self) -> list[dict[str, Any]]:
        """List all projects with basic info."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, name, description, goal, status, created_at
                       FROM projects ORDER BY created_at DESC"""
                )
                rows = cur.fetchall()
        return [
            {
                "id": str(r[0]),
                "name": r[1],
                "description": r[2],
                "goal": r[3],
                "status": r[4],
                "created_at": str(r[5]),
            }
            for r in rows
        ]

    def update_status(self, project_id: str, status: str) -> bool:
        """Update project status. Returns True if found."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE projects SET status = %s, updated_at = NOW() WHERE id = %s",
                    (status, project_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0

    def set_root_task(self, project_id: str, task_id: str) -> bool:
        """Link a root task to a project. Returns True if found."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE projects SET root_task_id = %s, updated_at = NOW() WHERE id = %s",
                    (task_id, project_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0

    def get_project_summary(self, project_id: str) -> str:
        """Generate a text summary of project state for LLM context injection."""
        project = self.get_project(project_id)
        if not project:
            return "No active project."

        counts = project.get("task_counts", {})
        total = sum(counts.values())
        done = counts.get("COMPLETED", 0)
        failed = counts.get("FAILED", 0)
        running = counts.get("RUNNING", 0) + counts.get("ASSIGNED", 0)

        lines = [
            f"Project: {project['name']}",
            f"Goal: {project['goal'] or 'Not specified'}",
            f"Status: {project['status']}",
            f"Tasks: {done}/{total} completed",
        ]
        if running:
            lines.append(f"Active: {running} tasks in progress")
        if failed:
            lines.append(f"Failed: {failed} tasks — may need attention")
        return "\n".join(lines)
