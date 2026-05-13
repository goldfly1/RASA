"""Policy client for real-time rule enforcement against tool calls."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import psycopg


def _pg_dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_policy"


class PolicyClient:
    """Queries policy_rules table to gate agent tool calls.

    Features:
    - In-memory rule cache with TTL for hot-reload support
    - Layered evaluation: org guardrails -> soul sheet -> task override
    - auto_invoke enforcement from soul sheet behavior.tool_policy
    - Audit logging for every decision
    """

    def __init__(self, cache_ttl: float = 30.0) -> None:
        self._cache_ttl = cache_ttl
        self._cache: list[tuple] | None = None
        self._cache_at: float = 0.0

    def _load_rules(self) -> list[tuple]:
        """Load all active policy rules from PostgreSQL, ordered by priority."""
        try:
            with psycopg.connect(_pg_dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, match_field, match_op, match_value, action, action_params, priority
                           FROM policy_rules
                           WHERE 1=1
                           ORDER BY priority DESC"""
                    )
                    return cur.fetchall()
        except Exception as exc:
            print(f"[policy] rule load failed (using cache): {exc}", flush=True)
            return self._cache or []

    def _get_rules(self) -> list[tuple]:
        """Return cached rules, refreshing if TTL expired."""
        now = time.monotonic()
        if self._cache is None or (now - self._cache_at) > self._cache_ttl:
            self._cache = self._load_rules()
            self._cache_at = now
        return self._cache

    def invalidate_cache(self) -> None:
        """Force a rule reload on next evaluate call."""
        self._cache = None

    def evaluate(
        self,
        tool_name: str,
        args: dict,
        soul_id: str,
        auto_invoke: bool = True,
        task_override: dict | None = None,
    ) -> dict[str, Any]:
        """Check policy rules for a tool call. Returns decision: allow/deny/review.

        Args:
            tool_name: The tool being called (e.g. 'file_write')
            args: Tool arguments
            soul_id: The agent's soul ID
            auto_invoke: Whether the soul sheet allows auto-invocation
            task_override: Emergency task-level deny override
        """
        try:
            # Layer 0: auto_invoke check
            if not auto_invoke:
                self._write_audit(None, soul_id, tool_name, "review", args,
                                  reason="auto_invoke disabled")
                return {
                    "decision": "review",
                    "matched_rule": None,
                    "reason": "auto_invoke is disabled; tool calls require pre-approval",
                }

            # Layer 1: task override (emergency denial)
            if task_override and task_override.get("deny_all"):
                self._write_audit(None, soul_id, tool_name, "deny", args,
                                  reason="task override: deny_all")
                return {
                    "decision": "deny",
                    "matched_rule": None,
                    "reason": "Task override: all tools denied",
                }

            rules = self._get_rules()
            if not rules:
                return {"decision": "allow", "matched_rule": None, "reason": "no rules"}

            # Layer 2: organization guardrails + soul sheet rules (merged by priority)
            for rule in rules:
                rule_id, match_field, match_op, match_value, action, action_params, priority = rule
                action_params = (
                    action_params if isinstance(action_params, dict)
                    else json.loads(action_params or "{}")
                )

                if self._rule_matches(match_field, match_op, match_value, tool_name, args, soul_id):
                    self._write_audit(
                        str(rule_id) if rule_id else None,
                        soul_id, tool_name, action, args,
                        reason=f"Rule matched: {match_field} {match_op} {match_value}"
                    )
                    return {
                        "decision": action,
                        "matched_rule": str(rule_id) if rule_id else None,
                        "reason": f"Rule {rule_id}: {match_field} {match_op} {match_value}",
                        "action_params": action_params,
                    }

            # Layer 3: default allow
            return {"decision": "allow", "matched_rule": None, "reason": "no rules matched"}

        except Exception as exc:
            # Fail open: don't block agents on policy errors
            print(f"[policy] evaluate error (default allow): {exc}", flush=True)
            return {"decision": "allow", "matched_rule": None, "reason": f"policy error (default allow): {exc}"}

    def _rule_matches(
        self,
        field: str,
        op: str,
        value: str,
        tool_name: str,
        args: dict,
        soul_id: str,
    ) -> bool:
        """Check if a rule matches the current tool call."""
        # Resolve target value from field
        if field == "tool_name":
            target = tool_name
        elif field == "soul_id":
            target = soul_id
        elif field.startswith("arg."):
            arg_key = field[4:]
            target = str(args.get(arg_key, ""))
        else:
            return False

        # Evaluate operator
        if op == "eq":
            return target == value
        elif op == "neq":
            return target != value
        elif op == "contains":
            return value in target
        elif op == "prefix":
            return target.startswith(value)
        elif op == "suffix":
            return target.endswith(value)
        elif op == "regex":
            import re
            try:
                return bool(re.search(value, target))
            except re.error:
                return False
        elif op == "glob":
            import fnmatch
            return fnmatch.fnmatch(target, value)
        elif op == "in":
            # value is a comma-separated list
            return target in value.split(",")
        return False

    def _write_audit(
        self,
        rule_id: str | None,
        soul_id: str,
        tool_name: str,
        decision: str,
        args: dict,
        reason: str = "",
    ) -> None:
        """Write an audit log entry to PostgreSQL."""
        try:
            with psycopg.connect(_pg_dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO audit_log (id, rule_id, agent_id, decision, context, created_at)
                           VALUES (%s, %s, %s, %s, %s, NOW())""",
                        (
                            str(uuid.uuid4()),
                            rule_id,
                            soul_id,
                            decision,
                            json.dumps({
                                "tool": tool_name,
                                "args": args,
                                "reason": reason,
                            }),
                        ),
                    )
                conn.commit()
        except Exception:
            pass


# Singleton with cache invalidation support
_policy_client: PolicyClient | None = None


def get_policy_client() -> PolicyClient:
    global _policy_client
    if _policy_client is None:
        _policy_client = PolicyClient()
    return _policy_client


def invalidate_policy_cache() -> None:
    """Force a policy rule reload on next evaluate call."""
    client = get_policy_client()
    client.invalidate_cache()
