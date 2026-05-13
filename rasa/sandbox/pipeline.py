"""Sandbox Pipeline — isolate, scan, build, test, and promote agent output."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from rasa.bus import Envelope, Metadata, PostgresSubscriber, PostgresPublisher
from rasa.sandbox.scanner import scan_directory, ScanResult


class Gate(Enum):
    CLONING = "CLONING"
    SCANNING = "SCANNING"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    PROMOTING = "PROMOTING"
    CLEANUP = "CLEANUP"


@dataclass
class PipelineResult:
    task_id: str
    soul_id: str
    passed: bool
    gates: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    scan_findings: list[dict] = field(default_factory=list)
    build_output: str = ""
    test_output: str = ""
    duration_ms: int = 0


class SandboxPipeline:
    """Isolated build+test runner for agent-produced code changes."""

    def __init__(self, data_dir: str = "data/sandbox", working_dir: str | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.working_dir = Path(working_dir or os.getcwd())
        self._running = False

    async def _reap_orphans(self, max_age_minutes: int = 30) -> None:
        """Background task: delete sandbox dirs older than max_age_minutes."""
        while self._running:
            try:
                now = time.monotonic()
                for sandbox_dir in self.data_dir.iterdir():
                    if not sandbox_dir.is_dir():
                        continue
                    try:
                        mtime = sandbox_dir.stat().st_mtime
                        age_minutes = (now - mtime) / 60
                        if age_minutes > max_age_minutes:
                            shutil.rmtree(sandbox_dir, ignore_errors=True)
                            print(f"[sandbox] reaped orphan: {sandbox_dir.name} ({age_minutes:.0f}m old)", flush=True)
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(300)

    async def start(self, dbname: str = "rasa_orch") -> None:
        self._running = True

        pg_sub = PostgresSubscriber(dbname=dbname)
        await pg_sub.setup()
        await pg_sub.subscribe("sandbox_execute", self._handle_execute)
        await pg_sub.listen("sandbox_execute")

        self._pg_pub = PostgresPublisher(dbname=dbname)
        await self._pg_pub.setup()

        print("[sandbox] listening on sandbox_execute")
        reaper_task = asyncio.create_task(self._reap_orphans())
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass

    async def _handle_execute(self, env: Envelope) -> None:
        task_id = env.metadata.task_id
        soul_id = env.metadata.soul_id
        print(f"[sandbox] executing pipeline for task {task_id[:8]} (soul={soul_id})")

        result = await self.run_pipeline(
            task_id=task_id,
            soul_id=soul_id,
            payload=env.payload,
        )

        await self._publish_result(result)
        print(f"[sandbox] pipeline {task_id[:8]} -> {'PASS' if result.passed else 'FAIL'} ({result.duration_ms}ms)")

    async def run_pipeline(
        self, task_id: str, soul_id: str, payload: dict[str, Any]
    ) -> PipelineResult:
        start = time.monotonic()
        result = PipelineResult(task_id=task_id, soul_id=soul_id, passed=False)

        sandbox_dir = self.data_dir / task_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Gate 1: CLONING
            result.gates["clone"] = await self._clone(sandbox_dir, payload)
            if not result.gates["clone"]:
                result.failures.append("clone failed")
                return result

            # Gate 2: SCANNING
            scan = scan_directory(sandbox_dir)
            result.scan_findings = scan.findings
            result.gates["scan"] = scan.passed
            if not scan.passed:
                result.failures.append(
                    f"secret scan: {sum(1 for f in scan.findings if f['action'] == 'deny')} denials"
                )
                return result

            # Gate 3: BUILDING — only if build command exists
            result.gates["build"] = await self._build(sandbox_dir)
            if not result.gates["build"]:
                result.failures.append("build failed")
                return result

            # Gate 4: TESTING — only if test command exists
            test_ok, test_out = await self._test(sandbox_dir)
            result.gates["test"] = test_ok
            result.test_output = test_out
            if not test_ok:
                result.failures.append("tests failed")
                return result

            # Gate 5: PROMOTING
            result.gates["promote"] = await self._promote(sandbox_dir)
            if not result.gates["promote"]:
                result.failures.append("promotion failed")
                return result

            result.passed = True
            return result

        finally:
            # Gate 6: CLEANUP (always)
            await self._cleanup(sandbox_dir)
            elapsed = (time.monotonic() - start) * 1000
            result.duration_ms = int(elapsed)

    # ── gates ──────────────────────────────────────────────────────

    async def _clone(self, sandbox_dir: Path, payload: dict) -> bool:
        try:
            # Copy relevant project files into sandbox
            # For pilot: copy source files listed in payload, or key directories
            changed_files = payload.get("changed_files", [])
            ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv", "node_modules", "data", "checkpoints", "*.exe")

            if changed_files:
                for f in changed_files:
                    src = self.working_dir / f
                    dst = sandbox_dir / f
                    if src.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
            else:
                # Full sandbox: copy just the source tree (rasa/, cmd/, internal/, tests/)
                for subdir in ["rasa", "internal", "tests", "cmd", "config", "souls", "migrations"]:
                    src_dir = self.working_dir / subdir
                    if src_dir.exists():
                        shutil.copytree(src_dir, sandbox_dir / subdir, ignore=ignore, dirs_exist_ok=True)

                # Copy key root files
                for f in self.working_dir.glob("*.py"):
                    shutil.copy2(f, sandbox_dir / f.name)
                for f in self.working_dir.glob("*.toml"):
                    shutil.copy2(f, sandbox_dir / f.name)
                for f in self.working_dir.glob("go.*"):
                    shutil.copy2(f, sandbox_dir / f.name)

            return True
        except Exception as exc:
            print(f"[sandbox] clone error: {exc}")
            return False

    async def _build(self, sandbox_dir: Path) -> bool:
        # Try Go build if Go source exists
        go_mod = sandbox_dir / "go.mod"
        if go_mod.exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    "go", "build", "./cmd/...",
                    cwd=str(sandbox_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode != 0:
                    print(f"[sandbox] build failed: {stderr.decode()[:200]}")
                    return False
                return True
            except asyncio.TimeoutError:
                print("[sandbox] build timeout (30s)")
                return False
            except Exception as exc:
                print(f"[sandbox] build error: {exc}")
                return False

        # No build needed (Python-only sandbox)
        return True

    async def _test(self, sandbox_dir: Path) -> tuple[bool, str]:
        # Run Go tests if Go module exists
        go_mod = sandbox_dir / "go.mod"
        if go_mod.exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    "go", "test", "./internal/...", "-count=1", "-short",
                    cwd=str(sandbox_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                output = stdout.decode()
                ok = proc.returncode == 0
                if not ok:
                    print(f"[sandbox] test failed: {stderr.decode()[:200]}")
                return ok, output
            except asyncio.TimeoutError:
                return False, "test timeout (60s)"
            except Exception as exc:
                return False, f"test error: {exc}"

        # Run Python tests
        setup_cfg = sandbox_dir / "pyproject.toml"
        if setup_cfg.exists():
            try:
                venv_python = os.environ.get("RASA_PYTHON", str(self.working_dir / ".venv" / "Scripts" / "python.exe"))
                proc = await asyncio.create_subprocess_exec(
                    venv_python, "-m", "pytest", "tests/", "-v", "-x", "--timeout=30",
                    cwd=str(sandbox_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                output = stdout.decode()
                ok = proc.returncode == 0
                return ok, output
            except asyncio.TimeoutError:
                return False, "test timeout (60s)"
            except Exception as exc:
                return False, f"test error: {exc}"

        return True, "no tests configured"

    async def _promote(self, sandbox_dir: Path) -> bool:
        """Copy changed files from sandbox back to working directory."""
        try:
            for p in sandbox_dir.rglob("*"):
                if p.is_dir():
                    continue
                rel = p.relative_to(sandbox_dir)
                dest = self.working_dir / rel
                if not dest.exists() or p.read_bytes() != dest.read_bytes():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
            return True
        except Exception as exc:
            print(f"[sandbox] promote error: {exc}")
            return False

    async def _cleanup(self, sandbox_dir: Path) -> None:
        try:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass

    async def _publish_result(self, result: PipelineResult) -> None:
        meta = Metadata(
            soul_id=result.soul_id,
            task_id=result.task_id,
            timestamp_ms=int(time.time() * 1000),
        )
        env = Envelope.new(
            source="sandbox-pipeline",
            destination="orchestrator",
            payload={
                "task_id": result.task_id,
                "soul_id": result.soul_id,
                "passed": result.passed,
                "gates": result.gates,
                "failures": result.failures,
                "duration_ms": result.duration_ms,
            },
            metadata=meta,
        )
        try:
            await self._pg_pub.publish("sandbox_result", env)
        except Exception as exc:
            print(f"[sandbox] publish error: {exc}")

    async def stop(self) -> None:
        self._running = False
