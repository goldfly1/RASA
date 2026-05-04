#!/usr/bin/env python
"""Launch the NiceGUI web dashboard on :8401.

Usage:
    python launch_gui_nice.py
    python launch_gui_nice.py --port 8401 --host 0.0.0.0
"""

from rasa.gui_nice.app import run

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Launch RASA NiceGUI dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8401, help="Port to bind (default: 8401)")
    args = parser.parse_args()

    run(host=args.host, port=args.port)
