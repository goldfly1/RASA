"""Watch for incoming orchestrator messages in .orch_relay/inbox/.

Emits one line per new message so Claude Code's Monitor tool can pick it up.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

RELAY_DIR = Path(__file__).parent.parent / ".orch_relay"
INBOX_DIR = RELAY_DIR / "inbox"
OUTBOX_DIR = RELAY_DIR / "outbox"
POLL_INTERVAL = 1.0  # seconds

# Track files we've already seen
_seen: set[str] = set()


def main():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    # Seed with existing files
    for p in INBOX_DIR.glob("*.json"):
        _seen.add(p.name)

    print("[orch_relay] watching for messages...", flush=True)

    while True:
        for p in sorted(INBOX_DIR.glob("*.json")):
            if p.name in _seen:
                continue
            _seen.add(p.name)
            try:
                msg = json.loads(p.read_text())
                ticket_id = msg.get("ticket_id", p.stem)
                print(f"NEW_MESSAGE:{ticket_id}", flush=True)
            except (json.JSONDecodeError, OSError) as e:
                print(f"ERROR:{p.name}:{e}", flush=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
