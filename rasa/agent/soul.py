"""Soul sheet loader with JSON Schema validation, inheritance, and typed access.

Replaces ad-hoc _load_soul() in dispatcher.py and runtime.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SOULS_DIR = Path(__file__).parent.parent.parent / "souls"

# JSON Schema for soul sheets (draft 2020-12 compatible)
SOUL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RASA Soul Sheet",
    "type": "object",
    "required": ["soul_version", "soul_id", "agent_role", "metadata", "model", "prompt", "behavior"],
    "properties": {
        "soul_version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
        "soul_id": {"type": "string", "minLength": 1},
        "agent_role": {"type": "string", "minLength": 1},
        "inherits": {"type": ["string", "null"]},
        "metadata": {
            "type": "object",
            "required": ["name", "description", "owner"],
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "owner": {"type": "string"},
                "created_at": {"type": "string"},
                "review_date": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "model": {
            "type": "object",
            "required": ["default_tier", "temperature", "max_tokens"],
            "properties": {
                "default_tier": {"type": "string", "enum": ["standard", "premium"]},
                "preferred_model": {"type": "string"},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "max_tokens": {"type": "integer", "minimum": 1},
                "top_p": {"type": "number"},
                "frequency_penalty": {"type": "number"},
                "presence_penalty": {"type": "number"},
            },
        },
        "prompt": {
            "type": "object",
            "required": ["system_template"],
            "properties": {
                "system_template": {"type": "string"},
                "context_injection": {"type": "string"},
                "tool_use_preamble": {"type": "string"},
            },
        },
        "behavior": {
            "type": "object",
            "required": ["tool_policy", "session"],
            "properties": {
                "principles": {"type": "array", "items": {"type": "string"}},
                "tool_policy": {
                    "type": "object",
                    "required": ["allowed_tools", "denied_tools"],
                    "properties": {
                        "auto_invoke": {"type": "boolean"},
                        "allowed_tools": {"type": "array", "items": {"type": "string"}},
                        "denied_tools": {"type": "array", "items": {"type": "string"}},
                        "require_human_confirm": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "session": {
                    "type": "object",
                    "required": ["mode"],
                    "properties": {
                        "mode": {"type": "string", "enum": ["daemon", "one-shot", "interactive"]},
                        "max_idle_minutes": {"type": "integer"},
                        "checkpoint_interval_seconds": {"type": "integer"},
                        "heartbeat_interval_seconds": {"type": "integer"},
                        "graceful_shutdown_seconds": {"type": "integer"},
                    },
                },
            },
        },
        "memory": {
            "type": "object",
            "properties": {
                "short_term_window": {"type": "integer"},
                "long_term_retrieval_k": {"type": "integer"},
                "graph_traversal_depth": {"type": "integer"},
            },
        },
        "cli": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "argument_binding": {"type": "object"},
                "environment_injection": {"type": "object"},
                "exit_codes": {"type": "object"},
            },
        },
        "extensions": {"type": "object"},
    },
}


@dataclass
class Soul:
    """Typed representation of a loaded and validated soul sheet."""

    soul_id: str
    soul_version: str
    agent_role: str
    name: str
    description: str
    owner: str
    inherits: str | None
    tags: list[str] = field(default_factory=list)

    # Model config
    default_tier: str = "standard"
    preferred_model: str = ""
    temperature: float = 0.2
    max_tokens: int = 8192
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0

    # Prompt templates (raw mustache/handlebars strings)
    system_template: str = ""
    context_injection: str = ""
    tool_use_preamble: str = ""

    # Behavior
    principles: list[str] = field(default_factory=list)
    auto_invoke: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    require_human_confirm: list[str] = field(default_factory=list)
    session_mode: str = "daemon"
    max_idle_minutes: int = 10
    checkpoint_interval_seconds: int = 30
    heartbeat_interval_seconds: int = 5
    graceful_shutdown_seconds: int = 30

    # Memory
    short_term_window: int = 10
    long_term_retrieval_k: int = 5
    graph_traversal_depth: int = 2

    # CLI
    cli_enabled: bool = True
    argument_binding: dict = field(default_factory=dict)
    environment_injection: dict = field(default_factory=dict)
    exit_codes: dict = field(default_factory=dict)

    # Raw dict for template rendering compat
    raw: dict = field(default_factory=dict)


class SoulLoader:
    """Loads, validates, and resolves soul sheets with inheritance."""

    def __init__(self, souls_dir: Path | None = None):
        self._dir = souls_dir or SOULS_DIR
        self._cache: dict[str, Soul] = {}
        self._mtimes: dict[str, float] = {}
        self._on_reload = None

    def load(self, soul_id: str) -> Soul:
        """Load a soul by ID with validation and inheritance resolution."""
        if soul_id in self._cache:
            return self._cache[soul_id]

        raw = self._read_raw(soul_id)
        self._validate(raw)

        # Resolve inheritance chain (merge parent, then child)
        resolved = self._resolve_inheritance(raw)

        soul = self._from_dict(resolved)
        self._cache[soul_id] = soul
        self._mtimes[soul_id] = self._file_mtime(soul_id)
        return soul

    def _read_raw(self, soul_id: str) -> dict[str, Any]:
        """Find and parse a soul YAML file by soul_id."""
        for p in self._dir.glob("*.yaml"):
            with open(p, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
                if isinstance(doc, dict) and doc.get("soul_id") == soul_id:
                    return doc
        raise FileNotFoundError(f"Soul '{soul_id}' not found in {self._dir}")

    def _validate(self, raw: dict[str, Any]) -> None:
        """Validate a raw soul dict against the JSON Schema."""
        try:
            import jsonschema
            jsonschema.validate(raw, SOUL_SCHEMA)
        except ImportError:
            # Soft fallback: warn but don't block if jsonschema not installed
            import sys
            print(f"[soul] jsonschema not installed, skipping validation for {raw.get('soul_id', '?')}",
                  file=sys.stderr)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"Soul '{raw.get('soul_id', '?')}' validation failed: {exc.message}") from exc

    def _resolve_inheritance(self, raw: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
        """Resolve inheritance chain by deep-merging parent then child."""
        if _depth > 5:
            raise ValueError(f"Inheritance depth exceeded for soul '{raw.get('soul_id', '?')}'")

        parent_id = raw.get("inherits")
        if not parent_id:
            return raw

        parent_raw = self._read_raw(parent_id)
        parent_resolved = self._resolve_inheritance(parent_raw, _depth + 1)

        return self._deep_merge(parent_resolved, raw)

    def _deep_merge(self, base: dict, overlay: dict) -> dict:
        """Deep merge overlay onto base. Arrays are replaced, not appended."""
        result = dict(base)
        for key, value in overlay.items():
            if key == "inherits":
                continue  # already resolved
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _from_dict(self, raw: dict[str, Any]) -> Soul:
        """Convert a resolved raw dict into a typed Soul dataclass."""
        meta = raw.get("metadata", {})
        model = raw.get("model", {})
        behavior = raw.get("behavior", {})
        tool_policy = behavior.get("tool_policy", {})
        session = behavior.get("session", {})
        memory = raw.get("memory", {})
        cli = raw.get("cli", {})

        return Soul(
            soul_id=raw["soul_id"],
            soul_version=raw["soul_version"],
            agent_role=raw["agent_role"],
            name=meta.get("name", raw["soul_id"]),
            description=meta.get("description", ""),
            owner=meta.get("owner", ""),
            inherits=raw.get("inherits"),
            tags=meta.get("tags", []),
            default_tier=model.get("default_tier", "standard"),
            preferred_model=model.get("preferred_model", ""),
            temperature=model.get("temperature", 0.2),
            max_tokens=model.get("max_tokens", 8192),
            top_p=model.get("top_p", 1.0),
            frequency_penalty=model.get("frequency_penalty", 0.0),
            presence_penalty=model.get("presence_penalty", 0.0),
            system_template=raw.get("prompt", {}).get("system_template", ""),
            context_injection=raw.get("prompt", {}).get("context_injection", ""),
            tool_use_preamble=raw.get("prompt", {}).get("tool_use_preamble", ""),
            principles=behavior.get("principles", []),
            auto_invoke=tool_policy.get("auto_invoke", False),
            allowed_tools=tool_policy.get("allowed_tools", []),
            denied_tools=tool_policy.get("denied_tools", []),
            require_human_confirm=tool_policy.get("require_human_confirm", []),
            session_mode=session.get("mode", "daemon"),
            max_idle_minutes=session.get("max_idle_minutes", 10),
            checkpoint_interval_seconds=session.get("checkpoint_interval_seconds", 30),
            heartbeat_interval_seconds=session.get("heartbeat_interval_seconds", 5),
            graceful_shutdown_seconds=session.get("graceful_shutdown_seconds", 30),
            short_term_window=memory.get("short_term_window", 10),
            long_term_retrieval_k=memory.get("long_term_retrieval_k", 5),
            graph_traversal_depth=memory.get("graph_traversal_depth", 2),
            cli_enabled=cli.get("enabled", True),
            argument_binding=cli.get("argument_binding", {}),
            environment_injection=cli.get("environment_injection", {}),
            exit_codes=cli.get("exit_codes", {}),
            raw=raw,
        )

    def _file_mtime(self, soul_id: str) -> float | None:
        """Get the current mtime of a soul file, or None if not found."""
        p = self._dir / f"{soul_id}.yaml"
        if not p.exists():
            return None
        return p.stat().st_mtime

    def is_stale(self, soul_id: str) -> bool:
        """Check if a loaded soul has been modified on disk since last load."""
        current = self._file_mtime(soul_id)
        cached = self._mtimes.get(soul_id)
        if current is None:
            return False
        if cached is None:
            return True
        return current > cached

    def reload_if_stale(self, soul_id: str):
        """Reload if the file on disk changed. Returns new Soul or None."""
        if not self.is_stale(soul_id):
            return None
        import sys
        print(f"[soul] hot-reloading {soul_id}", file=sys.stderr, flush=True)
        self._cache.pop(soul_id, None)
        self._mtimes.pop(soul_id, None)
        return self.load(soul_id)

    def watch_loop(self, interval: float = 5.0, on_reload=None) -> None:
        """Blocking watcher: polls soul files every interval seconds."""
        import time, sys
        print(f"[soul] watcher started (interval={interval:.0f}s)", file=sys.stderr, flush=True)
        while True:
            for soul_id in self.list_all():
                if self.is_stale(soul_id):
                    try:
                        new_soul = self.reload_if_stale(soul_id)
                        if new_soul and on_reload:
                            on_reload(soul_id, new_soul)
                    except Exception as exc:
                        print(f"[soul] hot-reload failed for {soul_id}: {exc}", file=sys.stderr, flush=True)
            time.sleep(interval)
    def list_all(self) -> list[str]:
        """Return all available soul IDs."""
        ids = []
        for p in self._dir.glob("*.yaml"):
            try:
                with open(p, encoding="utf-8") as f:
                    doc = yaml.safe_load(f)
                    if isinstance(doc, dict) and doc.get("soul_id"):
                        ids.append(doc["soul_id"])
            except Exception:
                pass
        return sorted(ids)
