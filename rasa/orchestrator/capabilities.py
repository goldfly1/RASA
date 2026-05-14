"""Capability registry — what each specialist agent can do."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg


def _dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "8764")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_orch"


class CapabilityRegistry:
    """Manages agent capability metadata in PostgreSQL."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or _dsn()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def list_capabilities(self) -> list[dict[str, Any]]:
        """Return all agent capability rows."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, soul_id, agent_role, display_name, description,
                              capabilities, access_level, created_at, updated_at
                       FROM agent_capabilities ORDER BY soul_id"""
                )
                rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_capability(self, soul_id: str) -> dict[str, Any] | None:
        """Get a single agent's capability row by soul_id."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, soul_id, agent_role, display_name, description,
                              capabilities, access_level, created_at, updated_at
                       FROM agent_capabilities WHERE soul_id = %s""",
                    (soul_id,),
                )
                row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def register_capability(
        self,
        soul_id: str,
        agent_role: str,
        display_name: str,
        description: str,
        capabilities: list[dict] | None = None,
        access_level: str = "read-only",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Insert or update an agent's capability row (upsert on soul_id)."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_capabilities
                          (soul_id, agent_role, display_name, description,
                           capabilities, access_level, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (soul_id) DO UPDATE SET
                          agent_role    = EXCLUDED.agent_role,
                          display_name  = EXCLUDED.display_name,
                          description   = EXCLUDED.description,
                          capabilities  = EXCLUDED.capabilities,
                          access_level  = EXCLUDED.access_level,
                          metadata      = agent_capabilities.metadata || EXCLUDED.metadata,
                          updated_at    = NOW()
                       RETURNING id, soul_id, agent_role, display_name, description,
                                 capabilities, access_level, created_at, updated_at""",
                    (
                        soul_id,
                        agent_role,
                        display_name,
                        description,
                        json.dumps(capabilities or []),
                        access_level,
                        json.dumps(metadata or {}),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _row_to_dict(row) if row else {}

    def update_capabilities(
        self, soul_id: str, capabilities: list[dict]
    ) -> bool:
        """Update only the capabilities JSONB array for an agent."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE agent_capabilities
                          SET capabilities = %s, updated_at = NOW()
                       WHERE soul_id = %s""",
                    (json.dumps(capabilities), soul_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0

    def score_match(self, task_description: str, task_title: str = '') -> list[dict]:
        caps = self.list_capabilities()
        results = []
        query = (task_description + ' ' + task_title).lower()
        for cap in caps:
            score = 0.0
            desc = cap.get('description', '').lower()
            role = cap.get('agent_role', '').lower()
            for item in cap.get('capabilities', []):
                name = (item.get('name', '') + ' ' + item.get('description', '')).lower()
                category = item.get('category', '').lower()
                for word in query.split():
                    if word in name or word in category:
                        score += 1.0
            for word in query.split():
                if word in desc:
                    score += 0.5
                if word in role:
                    score += 0.3
            if score > 0:
                cap['_score'] = score
                results.append(cap)
        results.sort(key=lambda r: r['_score'], reverse=True)
        return results

    def find_best_soul(self, task_description: str, task_title: str = '') -> str | None:
        scored = self.score_match(task_description, task_title)
        if not scored:
            return None
        return scored[0]['soul_id']

    def delete_capability(self, soul_id: str) -> bool:
        """Remove an agent's capability row."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_capabilities WHERE soul_id = %s", (soul_id,)
                )
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0


def _row_to_dict(row: tuple) -> dict[str, Any]:
    """Convert a psycopg row tuple to a dict."""
    return {
        "id": str(row[0]),
        "soul_id": row[1],
        "agent_role": row[2],
        "display_name": row[3],
        "description": row[4],
        "capabilities": json.loads(row[5]) if isinstance(row[5], str) else (row[5] or []),
        "access_level": row[6],
        "created_at": str(row[7]) if row[7] else None,
        "updated_at": str(row[8]) if row[8] else None,
    }
