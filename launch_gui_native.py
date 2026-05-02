#!/usr/bin/env python3
"""Launch the RASA native GUI command center.

Starts the Starlette backend server if not already running, then opens
the Tkinter desktop application.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

SERVER_URL = "http://127.0.0.1:8400/api/about"

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..")) if os.path.basename(HERE) == "rasa" else HERE


def _is_server_running() -> bool:
    """Check if the backend server is already running."""
    try:
        import httpx
        r = httpx.get(SERVER_URL, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _start_server() -> subprocess.Popen | None:
    """Start the Starlette server as a background process."""
    python = sys.executable
    server_path = os.path.join(PROJECT_ROOT, "rasa", "gui", "server.py")

    if not os.path.isfile(server_path):
        print(f"Server not found at {server_path}", file=sys.stderr)
        return None

    print("Starting backend server on :8400 ...")
    proc = subprocess.Popen(
        [python, server_path],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return proc


def main():
    # Ensure server is running
    if not _is_server_running():
        proc = _start_server()
        if proc is None:
            sys.exit(1)
        # Wait for server to be ready
        for i in range(30):
            if _is_server_running():
                print("Server is ready.")
                break
            time.sleep(0.5)
        else:
            print("Server failed to start within 15 seconds.", file=sys.stderr)
            print(f"Check {PROJECT_ROOT}/server.log for details.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Backend server is already running.")

    # Launch GUI
    print("Starting RASA Command Center...")
    sys.path.insert(0, PROJECT_ROOT)
    from rasa.gui_native.app import launch
    launch()


if __name__ == "__main__":
    main()
