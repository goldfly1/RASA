"""Metrics queries for the RASA dashboard — reads SQL views across all databases."""

from __future__ import annotations

import os
from typing import Any

import psycopg


def _pg_dsn(dbname: str) -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname={dbname}"


def _query_rows(dbname: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run a query and return rows as list of dicts."""
    try:
        with psycopg.connect(_pg_dsn(dbname)) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
                return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


def get_task_summary() -> dict[str, Any]:
    """Task counts by status (last 24h) + daily summary."""
    by_status = _query_rows(
        "rasa_orch",
        "SELECT status, COUNT(*) as count FROM tasks "
        "WHERE created_at > NOW() - INTERVAL '24 hours' "
        "GROUP BY status ORDER BY status",
    )
    daily = _query_rows("rasa_orch", "SELECT * FROM v_daily_summary")
    return {"by_status": by_status, "daily": daily}


def get_task_latency() -> list[dict[str, Any]]:
    """Per-task latency breakdown."""
    return _query_rows("rasa_orch", "SELECT * FROM v_task_latency ORDER BY created_at DESC LIMIT 50")


def get_agent_uptime() -> list[dict[str, Any]]:
    """Agent heartbeat coverage and liveness."""
    return _query_rows("rasa_pool", "SELECT * FROM v_agent_uptime ORDER BY last_seen DESC")


def get_backpressure() -> list[dict[str, Any]]:
    """Recent backpressure events."""
    return _query_rows("rasa_pool", "SELECT * FROM v_recent_backpressure")


def get_soul_performance() -> list[dict[str, Any]]:
    """Per-soul evaluation metrics."""
    return _query_rows("rasa_eval", "SELECT * FROM v_soul_performance")


def get_drift_status() -> list[dict[str, Any]]:
    """Latest drift snapshots per soul."""
    return _query_rows("rasa_eval", "SELECT * FROM v_latest_drift")


def get_policy_decisions() -> list[dict[str, Any]]:
    """Recent policy decisions."""
    return _query_rows("rasa_policy", "SELECT * FROM v_recent_decisions")


def get_recovery_actions() -> list[dict[str, Any]]:
    """Recent recovery actions."""
    return _query_rows("rasa_recovery", "SELECT * FROM v_recent_recoveries")


def get_live_agents() -> list[dict[str, Any]]:
    """Live agents from rasa_pool with state and last heartbeat."""
    return _query_rows(
        "rasa_pool",
        "SELECT agent_id, soul_id, state, "
        "EXTRACT(epoch FROM (NOW() - last_heartbeat))::INT AS idle_s "
        "FROM agents WHERE state != 'DISCONNECTED' "
        "ORDER BY state, soul_id",
    )


def get_all_metrics() -> dict[str, Any]:
    """Aggregate all metrics into a single response for the dashboard overview."""
    return {
        "tasks": get_task_summary(),
        "latency": get_task_latency(),
        "agent_uptime": get_agent_uptime(),
        "backpressure": get_backpressure(),
        "soul_performance": get_soul_performance(),
        "drift": get_drift_status(),
        "policy": get_policy_decisions(),
        "recovery": get_recovery_actions(),
        "live_agents": get_live_agents(),
    }
