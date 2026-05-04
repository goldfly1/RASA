"""Watch .orch_relay/inbox/ for new messages. Silent until a message arrives.

Emits NEW_MESSAGE:<ticket_id> for each new file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

RELAY_DIR = Path(__file__).parent.parent / ".orch_relay"
INBOX_DIR = RELAY_DIR / "inbox"

_seen: set[str] = set()


def main():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    # Seed with existing files
    for p in INBOX_DIR.glob("*.json"):
        _seen.add(p.name)

    while True:
        for p in sorted(INBOX_DIR.glob("*.json")):
            if p.name in _seen:
                continue
            _seen.add(p.name)
            try:
                msg = json.loads(p.read_text())
                ticket_id = msg.get("ticket_id", p.stem)
                print(f"NEW_MESSAGE:{ticket_id}", flush=True)
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(2.0)


if __name__ == "__main__":
    main()
