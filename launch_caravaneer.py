"""
Caravaneer to the Stars — One-Click Launcher

Starts the NiceGUI web server and opens the game in your browser.
"""

import os
import sys
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
GUI_PORT = 8500
GUI_URL = f"http://127.0.0.1:{GUI_PORT}"


def main():
    os.environ.setdefault("RASA_DB_PASSWORD", "8764")
    sys.path.insert(0, str(PROJECT_ROOT))

    print(f"  Caravaneer to the Stars")
    print(f"  Launching on {GUI_URL} ...")
    print()

    # Open browser after a short delay
    import threading
    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(GUI_URL)

    threading.Thread(target=open_browser, daemon=True).start()

    # Start the NiceGUI app
    from caravaneer.gui.app import run
    run(host="127.0.0.1", port=GUI_PORT)


if __name__ == "__main__":
    main()
