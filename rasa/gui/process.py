from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from rasa.gui.registry import ServiceDef


class AlreadyRunningError(Exception):
    pass


class NotRunningError(Exception):
    pass


@dataclass
class ManagedProcess:
    service_id: str
    process: asyncio.subprocess.Process
    start_time: float


def _load_dotenv(path: str) -> dict[str, str]:
    """Load a .env file and return the variables."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                # Strip optional quotes
                if len(val) > 1 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                env[key] = val
    except FileNotFoundError:
        pass
    return env


class ProcessManager:
    def __init__(self, project_root: str):
        self._processes: dict[str, ManagedProcess] = {}
        self._project_root = project_root
        self._env = _load_dotenv(os.path.join(project_root, ".env"))
        self._stderr_capture: dict[str, str] = {}

    @property
    def managed_ids(self) -> set[str]:
        return set(self._processes.keys())

    def is_running(self, service_id: str) -> bool:
        mp = self._processes.get(service_id)
        if mp is None:
            return False
        if mp.process.returncode is not None:
            # Process exited; clean up
            del self._processes[service_id]
            return False
        return True

    async def start(self, svc: ServiceDef) -> int:
        if self.is_running(svc.id):
            raise AlreadyRunningError(f"Service '{svc.id}' is already running")

        # Merge .env vars into the current environment for the subprocess
        subprocess_env = os.environ.copy()
        subprocess_env.update(self._env)

        proc = await asyncio.create_subprocess_exec(
            *svc.start_command,
            cwd=self._project_root,
            env=subprocess_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        self._processes[svc.id] = ManagedProcess(
            service_id=svc.id,
            process=proc,
            start_time=time.time(),
        )
        # Capture stderr in background for error reporting
        self._stderr_capture[svc.id] = ""
        asyncio.create_task(self._capture_stderr(svc.id, proc))
        return proc.pid

    async def _capture_stderr(self, svc_id: str, proc: asyncio.subprocess.Process):
        try:
            data = await proc.stderr.read()
            self._stderr_capture[svc_id] = data.decode(errors="replace")
        except Exception:
            pass

    def get_stderr(self, service_id: str) -> str:
        return self._stderr_capture.get(service_id, "")

    def _cleanup(self, service_id: str):
        self._processes.pop(service_id, None)
        self._stderr_capture.pop(service_id, None)

    async def stop(self, service_id: str, force: bool = False) -> None:
        mp = self._processes.get(service_id)
        if mp is None:
            raise NotRunningError(f"Service '{service_id}' is not running")

        pid = mp.process.pid

        # Try graceful terminate first
        if not force:
            mp.process.terminate()
            try:
                await asyncio.wait_for(mp.process.wait(), timeout=5)
                self._cleanup(service_id)
                return
            except asyncio.TimeoutError:
                pass

        # Force kill via taskkill
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/PID", str(pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await kill_proc.communicate()
        except Exception:
            pass

        if svc_id := self._processes.get(service_id):
            try:
                await asyncio.wait_for(svc_id.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

        self._cleanup(service_id)

    async def stop_all(self) -> None:
        for sid in list(self._processes.keys()):
            try:
                await self.stop(sid, force=True)
            except NotRunningError:
                pass

    def get_pid(self, service_id: str) -> Optional[int]:
        mp = self._processes.get(service_id)
        if mp and mp.process.returncode is None:
            return mp.process.pid
        return None

    def get_exit_code(self, service_id: str) -> Optional[int]:
        """Return the exit code of a managed process, or None if still running or unknown."""
        mp = self._processes.get(service_id)
        if mp and mp.process.returncode is not None:
            return mp.process.returncode
        return None

    def get_start_time(self, service_id: str) -> Optional[float]:
        mp = self._processes.get(service_id)
        if mp and mp.process.returncode is None:
            return mp.start_time
        return None
