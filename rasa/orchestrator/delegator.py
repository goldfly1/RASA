"""Task delegator — creates, assigns, and monitors tasks in PostgreSQL."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import psycopg


def _dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "8764")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_orch"


class TaskDelegator:
    """Creates, assigns, and monitors tasks in the PostgreSQL task DAG."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or _dsn()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def create_task(
        self,
        soul_id: str,
        title: str,
        description: str = "",
        parent_id: str | None = None,
        payload: dict | None = None,
    ) -> str:
        """Insert a PENDING task. Returns the task UUID."""
        task_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tasks (id, title, description, payload, status, soul_id)
                       VALUES (%s, %s, %s, %s, 'PENDING', %s)""",
                    (task_id, title, description, json.dumps(payload or {}), soul_id),
                )
                if parent_id:
                    cur.execute(
                        "UPDATE tasks SET parent_id = %s WHERE id = %s",
                        (parent_id, task_id),
                    )
            conn.commit()
        return task_id

    def assign_task(self, task_id: str) -> str | None:
        """Set task to ASSIGNED and send PG NOTIFY. Returns soul_id or None if not found."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT soul_id FROM tasks WHERE id = %s AND status = 'PENDING'",
                    (task_id,),
                )
                row = cur.fetchone()
                if not row:
                    # Check if already assigned/running
                    cur.execute("SELECT status, soul_id FROM tasks WHERE id = %s", (task_id,))
                    existing = cur.fetchone()
                    if existing:
                        return existing[1]  # soul_id — already transitioning
                    return None

                soul_id = row[0]
                cur.execute(
                    "UPDATE tasks SET status = 'ASSIGNED', assigned_at = NOW() WHERE id = %s",
                    (task_id,),
                )
                # Notify pool controller
                cur.execute("SELECT pg_notify('tasks_assigned', %s)", (json.dumps({
                    "task_id": task_id,
                    "soul_id": soul_id,
                }),))
            conn.commit()
        return soul_id

    def query_task(self, task_id: str) -> dict[str, Any] | None:
        """Return task info: status, result, error_message, soul_id, title."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, title, description, status, soul_id, result, error_message,
                              created_at, started_at, completed_at
                       FROM tasks WHERE id = %s""",
                    (task_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "title": row[1],
            "description": row[2],
            "status": row[3],
            "soul_id": row[4],
            "result": row[5] if row[5] else None,
            "error_message": row[6],
            "created_at": str(row[7]) if row[7] else None,
            "started_at": str(row[8]) if row[8] else None,
            "completed_at": str(row[9]) if row[9] else None,
        }

    def poll_task(self, task_id: str, timeout: float = 120.0, interval: float = 2.0) -> dict[str, Any] | None:
        """Poll task until COMPLETED/FAILED or timeout. Returns the task dict."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.query_task(task_id)
            if task and task["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                return task
            time.sleep(interval)
        return self.query_task(task_id)

    def list_project_tasks(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """List all tasks, optionally filtered by project root."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if project_id:
                    # Tasks under this project's root task (recursive via parent_id)
                    cur.execute(
                        """WITH RECURSIVE project_tasks AS (
                               SELECT id, parent_id, title, description, status, soul_id,
                                      created_at, completed_at, error_message
                               FROM tasks WHERE id = (SELECT root_task_id FROM projects WHERE id = %s)
                               UNION
                               SELECT t.id, t.parent_id, t.title, t.description, t.status, t.soul_id,
                                      t.created_at, t.completed_at, t.error_message
                               FROM tasks t
                               JOIN project_tasks pt ON t.parent_id = pt.id
                           )
                           SELECT * FROM project_tasks ORDER BY created_at""",
                        (project_id,),
                    )
                else:
                    cur.execute(
                        """SELECT id, parent_id, title, description, status, soul_id,
                                  created_at, completed_at, error_message
                           FROM tasks ORDER BY created_at DESC LIMIT 100"""
                    )
                rows = cur.fetchall()
        return [
            {
                "id": str(r[0]),
                "parent_id": str(r[1]) if r[1] else None,
                "title": r[2],
                "description": r[3],
                "status": r[4],
                "soul_id": r[5],
                "created_at": str(r[6]) if r[6] else None,
                "completed_at": str(r[7]) if r[7] else None,
                "error_message": r[8],
            }
            for r in rows
        ]
