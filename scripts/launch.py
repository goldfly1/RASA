"""Staged launcher — starts RASA services in dependency order with health checks.

Usage:
    python scripts/launch.py                     # interactive (Ctrl+C to stop)
    python scripts/launch.py --detach            # spawn windows, don't wait
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

os.environ.setdefault("RASA_DB_PASSWORD", "8764")


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def _check_host_port(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _wait_for_service(label: str, host: str, port: int, timeout: float = 15.0) -> bool:
    log(f"  Waiting for {label} ({host}:{port})...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await _check_host_port(host, port):
            log(f"  {label} ready")
            return True
        await asyncio.sleep(1)
    log(f"  WARN: {label} not available after {timeout}s — continuing anyway")
    return False


async def _wait_for_http(url: str, timeout: float = 15.0) -> bool:
    import httpx
    log(f"  Waiting for HTTP {url}...")
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(url)
                if r.is_success:
                    log(f"  HTTP {url} ready")
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(1)
    log(f"  WARN: {url} not responding after {timeout}s")
    return False


class ManagedProc:
    """Wrapper around an asyncio subprocess."""

    def __init__(self, name: str, cmd: list[str], cwd: str = PROJECT_ROOT):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.proc: asyncio.subprocess.Process | None = None
        self.start_time: float = 0.0

    async def start(self):
        log(f"  Starting {self.name}...")
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.start_time = time.time()

    async def poll(self) -> bool:
        if self.proc is None:
            return False
        if self.proc.returncode is not None:
            return False
        return True

    async def stop(self):
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        self.proc = None


class StagedLauncher:
    """Starts services layer by layer, waiting for health before proceeding."""

    def __init__(self):
        self.procs: list[ManagedProc] = []
        self.layer_names = {
            0: "Layer 0 — Infrastructure check",
            1: "Layer 1 — API Server (:8400)",
            2: "Layer 2 — Pool Controller",
            3: "Layer 3 — Dashboard + Agents",
            4: "Layer 4 — Heartbeat Monitor",
        }

    async def check_infrastructure(self) -> bool:
        log("Checking external infrastructure...")
        pg = await _wait_for_service("PostgreSQL", "127.0.0.1", 5432, timeout=5)
        rd = await _wait_for_service("Redis", "127.0.0.1", 6379, timeout=5)
        ol = await _wait_for_service("Ollama", "127.0.0.1", 11434, timeout=5)
        if not pg:
            log("  ERROR: PostgreSQL is required — start it first")
            return False
        return True

    async def start_layer1(self):
        log("--- Layer 1: API Server ---")
        p = ManagedProc("api", [PYTHON, "-m", "rasa.gui.server"])
        await p.start()
        self.procs.append(p)
        await _wait_for_http("http://127.0.0.1:8400/about", timeout=20)
        return p

    async def start_layer2(self):
        log("--- Layer 2: Pool Controller ---")
        p = ManagedProc("pool-controller", [PYTHON, "-m", "rasa.pool.controller", "--pool-file", "config/pool.yaml"])
        await p.start()
        self.procs.append(p)
        return p

    async def start_layer3(self):
        log("--- Layer 3: Dashboard + Agents ---")
        dashboard = ManagedProc("gui-nice", [PYTHON, "-m", "rasa.gui_nice"])
        await dashboard.start()
        self.procs.append(dashboard)
        # Daemon agents
        for soul, name in [
            ("souls/planner-v1.yaml", "agent-planner"),
            ("souls/coder-v2-dev.yaml", "agent-coder"),
            ("souls/coder-v2-dev.yaml", "agent-coder-2"),
            ("souls/reviewer-v1.yaml", "agent-reviewer"),
            ("souls/architect-v1.yaml", "agent-architect"),
        ]:
            p = ManagedProc(name, [PYTHON, "-m", "rasa.agent.runtime", "--soul", soul])
            await p.start()
            self.procs.append(p)
            await asyncio.sleep(0.5)

    async def start_layer4(self):
        log("--- Layer 4: Heartbeat Monitor ---")
        p = ManagedProc("heartbeat", [PYTHON, "scripts/heartbeat_monitor.py", "--loop", "--interval", "30"])
        await p.start()
        self.procs.append(p)

    async def run(self):
        # Layer 0
        log("=" * 50)
        log("RASA Staged Launcher")
        log("=" * 50)
        ok = await self.check_infrastructure()
        if not ok:
            return

        # Layer 1
        await self.start_layer1()

        # Layer 2
        await self.start_layer2()

        # Layer 3
        await self.start_layer3()

        # Layer 4
        await self.start_layer4()

        log("=" * 50)
        log("All services started. Ctrl+C to stop.")
        log(f"  Dashboard: http://127.0.0.1:8401")
        log(f"  API:       http://127.0.0.1:8400")
        log("=" * 50)

        # Monitor all processes, restart any that die
        while True:
            for p in list(self.procs):
                alive = await p.poll()
                if not alive:
                    log(f"  {p.name} died — restarting...")
                    await p.start()
            await asyncio.sleep(5)

    async def stop_all(self):
        log("Shutting down...")
        for p in reversed(self.procs):
            await p.stop()


def main():
    parser = argparse.ArgumentParser(description="Staged RASA service launcher")
    parser.add_argument("--detach", action="store_true", help="Spawn processes without monitoring")
    args = parser.parse_args()

    launcher = StagedLauncher()
    try:
        asyncio.run(launcher.run())
    except KeyboardInterrupt:
        asyncio.run(launcher.stop_all())
        log("Shutdown complete.")


if __name__ == "__main__":
    main()
