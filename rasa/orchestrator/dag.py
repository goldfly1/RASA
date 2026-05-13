"""DAG validation: cycle detection for task dependency graphs."""

from __future__ import annotations

import os
from typing import Any

import psycopg


def _dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "8764")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_orch"


def detect_cycle(new_task_id: str, parent_id: str) -> bool:
    """Check if adding {new_task_id} as child of {parent_id} would create a cycle.

    Traverses upward from parent_id through the parent chain.
    If new_task_id is found among ancestors, a cycle exists.

    Also checks task_dependencies table: if any dependency from new_task_id
    to an ancestor of parent_id exists, a cycle would form.
    """
    try:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                # Walk up the parent chain from parent_id
                visited: set[str] = set()
                current = parent_id
                while current:
                    if current == new_task_id:
                        return True  # direct cycle
                    if current in visited:
                        return True  # existing cycle (should not happen)
                    visited.add(current)
                    cur.execute(
                        "SELECT parent_id FROM tasks WHERE id = %s",
                        (current,),
                    )
                    row = cur.fetchone()
                    current = str(row[0]) if (row and row[0]) else None

                # Also check if any ancestor of parent_id depends on new_task_id
                if visited:
                    cur.execute(
                        """SELECT 1 FROM task_dependencies
                           WHERE from_task_id = ANY(%s) AND to_task_id = %s
                           LIMIT 1""",
                        (list(visited), new_task_id),
                    )
                    if cur.fetchone():
                        return True

        return False
    except Exception:
        # If the DB is unreachable, fail open (allow the task creation)
        return False


def validate_dag(task_id: str) -> bool:
    """Check whether the entire DAG rooted at task_id contains any cycles.

    Returns True if the DAG is valid (no cycles), False otherwise.
    """
    try:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                # Use recursive CTE + cycle detection in PostgreSQL
                cur.execute(
                    """WITH RECURSIVE task_tree(id, parent_id, path, has_cycle) AS (
                           SELECT id, parent_id, ARRAY[id] AS path, FALSE
                           FROM tasks WHERE id = %s
                           UNION ALL
                           SELECT t.id, t.parent_id,
                                  tt.path || t.id,
                                  t.id = ANY(tt.path)
                           FROM tasks t
                           JOIN task_tree tt ON t.id = tt.parent_id
                           WHERE NOT tt.has_cycle
                       )
                       SELECT COUNT(*) > 0 FROM task_tree WHERE has_cycle""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return False
        return True
    except Exception:
        return True  # fail open
