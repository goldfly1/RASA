"""Centralized model configuration — single source of truth for agent launch.

Pattern: ollama launch claude --model deepseek-v4-pro:cloud
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass(frozen=True)
class ModelConfig:
    provider: str       # "ollama"
    model: str          # "deepseek-v4-pro:cloud"
    base_url: str       # "http://127.0.0.1:11434/v1"
    api_key: str        # "ollama"


def resolve_model(tier: str = "premium") -> ModelConfig:
    """Resolve model config from environment or defaults.

    Default pattern: ollama launch claude --model deepseek-v4-pro:cloud
    """
    # Priority: explicit env vars > defaults
    model = os.environ.get("RASA_MODEL")  # Single override for everything
    if not model:
        if tier == "premium":
            model = os.environ.get("RASA_PREMIUM_MODEL", "deepseek-v4-pro:cloud")
        else:
            model = os.environ.get("RASA_DEFAULT_MODEL", "deepseek-v4-flash:cloud")

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    api_key = os.environ.get("OLLAMA_API_KEY", "ollama")

    return ModelConfig(
        provider="ollama",
        model=model,
        base_url=base_url,
        api_key=api_key,
    )


def ensure_ollama_running() -> bool:
    """Check that ollama is serving. Returns True if reachable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("127.0.0.1", 11434))
        s.close()
        return result == 0
    except Exception:
        return False


def launch_claude_code(
    *,
    model: str = "deepseek-v4-pro:cloud",
    prompt: str | None = None,
    system_prompt_file: str | None = None,
    extra_args: list[str] | None = None,
    cwd: str | None = None,
) -> int:
    """Launch Claude Code with ollama model — the canonical agent launch.

    Follows the pattern: ollama launch claude --model deepseek-v4-pro:cloud
    """
    # Ensure ollama is running
    if not ensure_ollama_running():
        print("ollama is not running. Start it with: ollama serve")
        return 1

    claude_path = _find_claude()
    if not claude_path:
        print("Error: 'claude' not found on PATH. Is Claude Code installed?")
        return 1

    cmd = [claude_path, "--model", model]

    if system_prompt_file:
        cmd.extend(["--append-system-prompt", system_prompt_file])
    if prompt:
        cmd.extend(["--prompt", prompt])
    if extra_args:
        cmd.extend(extra_args)

    print(f"launch: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or PROJECT_ROOT)
    return result.returncode


def _find_claude() -> str | None:
    import shutil
    return shutil.which("claude")
