from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from rasa.gui.registry import HealthType, ServiceDef


@dataclass
class ServiceStatus:
    status: str  # "running" | "stopped" | "unknown" | "error"
    status_detail: str = ""
    pid: Optional[int] = None
    uptime_seconds: Optional[float] = None
    managed: bool = False


class HealthChecker:
    def __init__(self, registry: list[ServiceDef]):
        self._registry = registry
        self._cache: dict[str, ServiceStatus] = {}
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None
        # managed process tracking set by ProcessManager
        self.managed_pids: dict[str, int] = {}
        self.managed_start_times: dict[str, float] = {}
        self.process_manager: "ProcessManager | None" = None  # set externally

    async def start(self):
        self._http_client = httpx.AsyncClient(timeout=3.0, verify=False)

    async def stop(self):
        if self._http_client:
            await self._http_client.aclose()

    async def check_all(self) -> dict[str, ServiceStatus]:
        results: dict[str, ServiceStatus] = {}
        for svc in self._registry:
            results[svc.id] = await self._check_one(svc)
        async with self._lock:
            self._cache = results
        return results

    async def _check_one(self, svc: ServiceDef) -> ServiceStatus:
        # If managed, check subprocess handle first (fast path)
        if svc.id in self.managed_pids:
            pid = self.managed_pids[svc.id]
            start_time = self.managed_start_times.get(svc.id, time.time())
            uptime = time.time() - start_time

            # Give HTTP services a 10-second startup grace period
            if svc.health.type == HealthType.HTTP_GET and uptime > 10:
                if self._http_client:
                    result = await self._check_http(svc)
                    if result.status == "running":
                        result.managed = True
                        result.pid = pid
                        result.uptime_seconds = uptime
                        return result
                    # HTTP check failed — process probably died
                    exit_code = None
                    if self.process_manager:
                        exit_code = self.process_manager.get_exit_code(svc.id)
                    del self.managed_pids[svc.id]
                    if exit_code is not None:
                        return ServiceStatus(
                            status="stopped",
                            status_detail=f"Process exited (code {exit_code}) — check dependencies",
                        )
                    return ServiceStatus(status="stopped", status_detail="Process died — check dependencies")
            else:
                return ServiceStatus(
                    status="starting" if uptime <= 10 else "running",
                    status_detail="Starting..." if uptime <= 10 else "Managed subprocess",
                    pid=pid,
                    uptime_seconds=uptime,
                    managed=True,
                )

        match svc.health.type:
            case HealthType.HTTP_GET:
                return await self._check_http(svc)
            case HealthType.TCP_PORT:
                return await self._check_tcp(svc)
            case HealthType.PROCESS:
                return await self._check_process(svc)
            case _:
                return ServiceStatus(status="unknown", status_detail="Unsupported health check type")

    async def _check_http(self, svc: ServiceDef) -> ServiceStatus:
        if not self._http_client:
            return ServiceStatus(status="unknown", status_detail="HTTP client not initialized")
        url = f"http://{svc.health.host}:{svc.health.port}{svc.health.path}"
        try:
            resp = await self._http_client.get(url)
            if resp.is_success:
                return ServiceStatus(
                    status="running",
                    status_detail=f"HTTP {resp.status_code}",
                    pid=None,
                )
            return ServiceStatus(status="error", status_detail=f"HTTP {resp.status_code}: {resp.reason_phrase}")
        except httpx.ConnectError:
            return ServiceStatus(status="stopped", status_detail=f"Connection refused on port {svc.health.port}")
        except httpx.TimeoutException:
            return ServiceStatus(status="stopped", status_detail=f"Timeout on port {svc.health.port}")
        except Exception as e:
            return ServiceStatus(status="error", status_detail=str(e))

    async def _check_tcp(self, svc: ServiceDef) -> ServiceStatus:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(svc.health.host, svc.health.port),
                timeout=2.0,
            )
            writer.close()
            await writer.wait_closed()
            return ServiceStatus(status="running", status_detail=f"TCP connected on port {svc.health.port}")
        except (ConnectionRefusedError, OSError):
            return ServiceStatus(status="stopped", status_detail=f"Port {svc.health.port}: connection refused")
        except asyncio.TimeoutError:
            return ServiceStatus(status="stopped", status_detail=f"Port {svc.health.port}: timeout")
        except Exception as e:
            return ServiceStatus(status="error", status_detail=str(e))

    async def _check_process(self, svc: ServiceDef) -> ServiceStatus:
        proc_name = svc.health.process_name or "unknown.exe"
        cmdline_match = svc.health.cmdline_match
        try:
            proc = await asyncio.create_subprocess_exec(
                "tasklist", "/FO", "CSV", "/FI", f"IMAGENAME eq {proc_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode(errors="replace")

            # tasklist CSV format: "Image Name","PID","Session Name","Session#","Mem Usage"
            lines = [l.strip() for l in output.splitlines() if l.strip()]
            # Filter processes by command-line match if needed
            if cmdline_match and len(lines) > 1:
                matching_pids = await self._filter_by_cmdline(proc_name, cmdline_match)
                if matching_pids:
                    return ServiceStatus(
                        status="running",
                        status_detail=f"Process running (PID: {matching_pids[0]})",
                        pid=matching_pids[0],
                    )
                return ServiceStatus(status="stopped", status_detail=f"No process matching '{cmdline_match}'")

            if len(lines) > 1:  # header + at least one row
                return ServiceStatus(status="running", status_detail=f"Process '{proc_name}' is running")
            return ServiceStatus(status="stopped", status_detail=f"Process '{proc_name}' not found")

        except Exception as e:
            return ServiceStatus(status="error", status_detail=str(e))

    async def _filter_by_cmdline(self, proc_name: str, match: str) -> list[int]:
        """Use wmic to find processes matching a command-line substring."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "wmic", "process", "where", f"name='{proc_name}'", "get", "ProcessId,CommandLine",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode(errors="replace")
            pids = []
            for line in output.splitlines():
                if match in line:
                    parts = line.strip().split()
                    if parts and parts[-1].isdigit():
                        pids.append(int(parts[-1]))
            return pids
        except Exception:
            return []
