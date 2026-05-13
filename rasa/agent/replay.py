
"""Replay bundle writer for post-hoc session debugging."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.parent
REPLAYS_DIR = PROJECT_ROOT / "data" / "replays"


def save_replay(
    task_id: str,
    soul_id: str,
    soul_raw: dict[str, Any],
    system_prompt: str,
    messages: list[dict[str, Any]],
    result: dict[str, Any] | None = None,
    memory_context: dict[str, Any] | None = None,
    model: str | None = None,
    token_usage: dict[str, Any] | None = None,
) -> str:
    """Write a complete replay bundle to data/replays/{task_id}/. Returns bundle path."""
    bundle_dir = REPLAYS_DIR / task_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    import yaml

    # Soul sheet
    (bundle_dir / "soul_sheet.yaml").write_text(
        yaml.dump(soul_raw, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    # Final prompt
    (bundle_dir / "prompt_final.txt").write_text(system_prompt, encoding="utf-8")

    # Full conversation
    with open(bundle_dir / "conversation.jsonl", "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")

    # Memory context
    (bundle_dir / "memory_context.json").write_text(
        json.dumps(memory_context or {}, indent=2, default=str),
        encoding="utf-8",
    )

    # Result
    (bundle_dir / "result.json").write_text(
        json.dumps(result or {}, indent=2, default=str),
        encoding="utf-8",
    )

    # Metadata
    metadata = {
        "task_id": task_id,
        "soul_id": soul_id,
        "model": model,
        "message_count": len(messages),
        "token_usage": token_usage,
        "saved_at": int(time.time()),
    }
    (bundle_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    return str(bundle_dir)
