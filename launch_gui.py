"""RASA Agent GUI — one-click launcher.

Opens the web GUI in your browser. Starts the server if not already running.
"""

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
GUI_URL = "http://127.0.0.1:8400"


def is_server_running() -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", 8400)) == 0


def main():
    if not is_server_running():
        os.environ["RASA_DB_PASSWORD"] = "8764"
        python = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
        subprocess.Popen(
            [python, "-m", "rasa.gui"],
            cwd=PROJECT_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        import time
        time.sleep(3)

    webbrowser.open(GUI_URL)
    print(f"RASA Agent GUI opened at {GUI_URL}")
    print("Close this window or press Enter to exit.")
    input()


if __name__ == "__main__":
    main()
