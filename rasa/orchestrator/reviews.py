"""ReviewManager — human-in-the-loop review operations on rasa_policy."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg


def _dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "8764")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_policy"


class ReviewManager:
    """CRUD for human_reviews in rasa_policy."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or _dsn()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def create_review(
        self,
        task_id: str,
        agent_id: str,
        reason: str,
        payload: dict | None = None,
    ) -> dict[str, Any]:
        """Insert a pending human review. Returns the review dict."""
        review_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO human_reviews
                       (id, task_id, agent_id, reason, payload, status, created_at)
                       VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                       RETURNING id, task_id, agent_id, reason, payload, status,
                                 reviewer, response, resolved_at, created_at""",
                    (review_id, task_id, agent_id, reason,
                     json.dumps(payload or {}), now),
                )
                row = cur.fetchone()
            conn.commit()
        return _row_to_dict(row) if row else {}

    def get_pending_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return all reviews with status='pending', newest first."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, task_id, agent_id, reason, payload, status,
                              reviewer, response, resolved_at, created_at
                       FROM human_reviews
                       WHERE status = 'pending'
                       ORDER BY created_at DESC
                       LIMIT %s""",
                    (limit,),
                )
                rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        """Get a single review by ID."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, task_id, agent_id, reason, payload, status,
                              reviewer, response, resolved_at, created_at
                       FROM human_reviews WHERE id = %s""",
                    (review_id,),
                )
                row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def respond_to_review(
        self,
        review_id: str,
        response: str,
        reviewer: str = "dashboard",
    ) -> bool:
        """Set the human response text and mark as answered/resolved.

        Only updates if status is still 'pending' (idempotent guard).
        Returns True if the row was updated.
        """
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE human_reviews
                          SET response = %s,
                              reviewer = %s,
                              status = 'answered',
                              resolved_at = %s
                       WHERE id = %s AND status = 'pending'""",
                    (response, reviewer, now, review_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0

    def list_reviews(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List reviews with optional status filter, newest first."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status_filter:
                    cur.execute(
                        """SELECT id, task_id, agent_id, reason, payload, status,
                                  reviewer, response, resolved_at, created_at
                           FROM human_reviews
                           WHERE status = %s
                           ORDER BY created_at DESC
                           LIMIT %s OFFSET %s""",
                        (status_filter, limit, offset),
                    )
                else:
                    cur.execute(
                        """SELECT id, task_id, agent_id, reason, payload, status,
                                  reviewer, response, resolved_at, created_at
                           FROM human_reviews
                           ORDER BY created_at DESC
                           LIMIT %s OFFSET %s""",
                        (limit, offset),
                    )
                rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: tuple) -> dict[str, Any]:
    """Convert a psycopg row tuple to a dict."""
    return {
        "id": str(row[0]),
        "task_id": str(row[1]) if row[1] else None,
        "agent_id": row[2],
        "reason": row[3],
        "payload": json.loads(row[4]) if isinstance(row[4], str) else (row[4] or {}),
        "status": row[5],
        "reviewer": row[6],
        "response": row[7],
        "resolved_at": str(row[8]) if row[8] else None,
        "created_at": str(row[9]) if row[9] else None,
    }
