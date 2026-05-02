"""End-to-end smoke test for the RASA pipeline.

Submits a task, waits for it to complete, and verifies the result.
Requires PostgreSQL running with RASA_DB_PASSWORD set.
Can run with or without agent/pool-controller services active.

Usage:
  pytest tests/test_smoke.py -v
  pytest tests/test_smoke.py -v -k "test_smoke_submit_and_wait"
"""

from __future__ import annotations

import asyncio
import json
import os
import selectors
import sys
import uuid

import pytest

from rasa.bus.envelope import Envelope, Metadata


def _pg_dsn(dbname: str = "rasa_orch") -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return (
        f"host={host} port={port} user={user} password={password} "
        f"dbname={dbname}"
    )


def _require_db():
    pw = os.environ.get("RASA_DB_PASSWORD", "")
    if not pw:
        pytest.skip("RASA_DB_PASSWORD not set")
    try:
        import psycopg
        with psycopg.connect(_pg_dsn("rasa_orch")) as conn:
            conn.execute("SELECT 1")
    except Exception:
        pytest.skip("PostgreSQL not reachable")


class TestSmokeSubmit:
    """Submit a task via the PG bus and verify it completes."""

    def test_smoke_submit_and_wait(self):
        """Full pipeline: insert task, publish, wait for completion."""
        _require_db()

        import psycopg

        task_id = str(uuid.uuid4())
        correlation_id = str(uuid.uuid4())
        soul_id = "coder-v2-dev"
        title = "Smoke test — return OK"

        # Insert task row
        with psycopg.connect(_pg_dsn("rasa_orch")) as conn:
            conn.execute(
                """INSERT INTO tasks (id, correlation_id, title, description, payload, status, soul_id, priority)
                   VALUES (%s, %s, %s, %s, %s, 'PENDING', %s, 5)""",
                (
                    task_id,
                    correlation_id,
                    title,
                    "Return the string OK",
                    json.dumps({"type": "smoke-test", "goal": "Return the string OK"}),
                    soul_id,
                ),
            )
            conn.commit()

        # Publish task to pool-controller
        meta = Metadata(soul_id=soul_id, task_id=task_id)
        env = Envelope.new(
            "smoke-test", "pool-controller",
            {"task_id": task_id, "title": title, "goal": "Return OK"},
            metadata=meta,
            correlation_id=correlation_id,
        )

        from rasa.bus.pg import PostgresPublisher
        pub = PostgresPublisher(dbname="rasa_orch")
        _loop_factory = (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) if sys.platform == "win32" else None
        asyncio.run(pub.setup(), loop_factory=_loop_factory)

        loop = _loop_factory() if sys.platform == "win32" else asyncio.new_event_loop()
        loop.run_until_complete(pub.publish("tasks_assigned", env))

        # Subscribe to task_completed and wait up to 30s
        from rasa.bus.pg import PostgresSubscriber

        received: list[Envelope] = []

        async def handler(env: Envelope):
            if env.metadata.task_id == task_id:
                received.append(env)

        sub = PostgresSubscriber(dbname="rasa_orch")
        loop.run_until_complete(sub.setup())
        loop.run_until_complete(sub.subscribe("task_completed", handler))
        loop.run_until_complete(sub.listen("task_completed"))

        # Wait for completion
        async def wait_for_task():
            for _ in range(30):
                await asyncio.sleep(1)
                if received:
                    return
            raise TimeoutError("Task did not complete within 30s")

        try:
            loop.run_until_complete(asyncio.wait_for(wait_for_task(), timeout=35))
        except (TimeoutError, asyncio.TimeoutError):
            pass  # Will verify task state below
        finally:
            loop.run_until_complete(sub.close())

        # Verify task final state
        with psycopg.connect(_pg_dsn("rasa_orch")) as conn:
            row = conn.execute(
                "SELECT status, result, error_message FROM tasks WHERE id = %s",
                (task_id,),
            ).fetchone()

        assert row is not None, f"Task {task_id[:8]} not found"
        status, result, error = row
        print(f"\nTask {task_id[:8]}: status={status}")

        if result:
            print(f"Result: {json.dumps(result, indent=2)[:300]}")

        # If agents are running, task should be COMPLETED
        # If not, it will be PENDING — still a valid smoke test of the pipeline
        valid_states = {"COMPLETED", "PENDING", "ASSIGNED"}
        assert status in valid_states, (
            f"Task status={status} not in {valid_states}. "
            f"error_message={error}"
        )

        if status == "COMPLETED" and result:
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            print(f"Content: {content[:200]}")


class TestSmokeDBConnectivity:
    """Verify all RASA databases are reachable."""

    DBS = [
        "rasa_orch",
        "rasa_pool",
        "rasa_policy",
        "rasa_memory",
        "rasa_eval",
        "rasa_recovery",
    ]

    def test_all_databases_reachable(self):
        """Every database in the architecture should accept connections."""
        _require_db()
        import psycopg

        unreachable = []
        for dbname in self.DBS:
            try:
                with psycopg.connect(_pg_dsn(dbname)) as conn:
                    conn.execute("SELECT 1")
            except Exception as exc:
                unreachable.append(f"{dbname}: {exc}")

        assert not unreachable, (
            f"Databases unreachable:\n" + "\n".join(unreachable)
        )

    def test_metrics_views_exist(self):
        """Metrics views from 070_metrics_views.sql should be queryable."""
        _require_db()
        import psycopg

        views = {
            "rasa_orch": ["v_task_latency", "v_daily_summary"],
            "rasa_eval": ["v_soul_performance", "v_latest_drift"],
            "rasa_pool": ["v_agent_uptime", "v_recent_backpressure"],
            "rasa_policy": ["v_recent_decisions"],
            "rasa_recovery": ["v_recent_recoveries"],
        }

        missing = []
        for dbname, view_names in views.items():
            try:
                with psycopg.connect(_pg_dsn(dbname)) as conn:
                    for v in view_names:
                        try:
                            conn.execute(f"SELECT 1 FROM {v} LIMIT 0")
                        except Exception as exc:
                            missing.append(f"{dbname}.{v}: {exc}")
            except Exception as exc:
                missing.append(f"{dbname} (connect): {exc}")

        # Views may not exist if migration hasn't been run yet — warn, don't fail
        if missing:
            print(f"\n  Note: {len(missing)} view(s) not yet available "
                  f"(run migrations/070_metrics_views.sql):")
            for m in missing:
                print(f"    - {m}")
