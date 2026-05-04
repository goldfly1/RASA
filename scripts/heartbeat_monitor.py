"""Heartbeat monitor — checks service health and restarts dead services.

Usage:
    python scripts/heartbeat_monitor.py          # run once (for cron)
    python scripts/heartbeat_monitor.py --loop   # run continuously (for launcher)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

os.environ["RASA_DB_PASSWORD"] = "8764"

from rasa.gui.health import HealthChecker
from rasa.gui.registry import build_registry
from rasa.gui.process import ProcessManager


LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "heartbeat.log")
COOLDOWN_SECONDS = 60  # minimum seconds between restart attempts per service


def log(msg: str):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


class HeartbeatMonitor:
    def __init__(self):
        self.registry = build_registry()
        self.service_map = {svc.id: svc for svc in self.registry}
        self.health_checker = HealthChecker(self.registry)
        # Separate ProcessManager just for auto-restarts
        self.process_manager = ProcessManager(PROJECT_ROOT)
        self.health_checker.process_manager = self.process_manager
        # Track last restart time per service to avoid restart loops
        self._last_restart: dict[str, float] = {}

    async def check_and_heal(self) -> list[str]:
        """Run health checks and restart any dead services. Returns actions taken."""
        results = await self.health_checker.check_all()
        actions: list[str] = []
        now = time.time()

        for svc in self.registry:
            if not svc.can_start or svc.is_external:
                continue  # don't auto-start DB/Redis/Ollama or services without commands

            status = results.get(svc.id)
            if status is None:
                continue

            if status.status in ("stopped", "error"):
                # Check cooldown
                last = self._last_restart.get(svc.id, 0)
                if now - last < COOLDOWN_SECONDS:
                    continue

                try:
                    pid = await self.process_manager.start(svc, capture_output=False)
                    self._last_restart[svc.id] = now
                    msg = f"Restarted {svc.display_name} (PID {pid})"
                    log(msg)
                    actions.append(msg)
                except Exception as e:
                    msg = f"Failed to restart {svc.display_name}: {e}"
                    log(msg)
                    actions.append(msg)

        return actions

    async def run_loop(self, interval: int = 30):
        """Run checks continuously every `interval` seconds."""
        log(f"Heartbeat monitor started (interval={interval}s)")
        log(f"Monitoring {len([s for s in self.registry if s.can_start and not s.is_external])} services")

        while True:
            try:
                actions = await self.check_and_heal()
                if not actions:
                    log("All services healthy")
            except Exception as e:
                log(f"Check cycle failed: {e}")

            await asyncio.sleep(interval)

    async def cleanup(self):
        await self.health_checker.stop()


def main():
    parser = argparse.ArgumentParser(description="Heartbeat monitor — restart dead services")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    args = parser.parse_args()

    monitor = HeartbeatMonitor()

    if args.loop:
        asyncio.run(monitor.run_loop(interval=args.interval))
    else:
        actions = asyncio.run(monitor.check_and_heal())
        if actions:
            print(f"Actions: {actions}")
        else:
            print("All services healthy")


if __name__ == "__main__":
    main()
