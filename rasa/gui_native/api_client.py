"""HTTP API client for the RASA backend server."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

import httpx

BASE_URL = "http://127.0.0.1:8400"
TIMEOUT = 300.0


def _run_async(coro, on_done: Callable[[Any], None] | None = None):
    """Run an async coroutine in a background thread and dispatch result to callback."""
    def _run():
        try:
            import asyncio
            result = asyncio.run(coro)
            if on_done:
                on_done(result)
        except Exception as e:
            if on_done:
                on_done({"error": str(e)})
    threading.Thread(target=_run, daemon=True).start()


# ── Synchronous wrappers (for Tkinter) ──

def _fetch(url: str) -> Any:
    """Synchronous HTTP GET."""
    r = httpx.get(f"{BASE_URL}{url}", timeout=30)
    r.raise_for_status()
    return r.json()


def _post(url: str, data: dict) -> Any:
    """Synchronous HTTP POST."""
    r = httpx.post(f"{BASE_URL}{url}", json=data, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── API methods (async, for background threads) ──

async def _send_async(message: str, project_id: str | None = None, mode: str | None = None) -> dict:
    payload = {"message": message}
    if project_id:
        payload["project_id"] = project_id
    if mode:
        payload["mode"] = mode
    return await _post_async("/api/orchestrator/send", payload)


async def _post_async(url: str, data: dict) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT)) as c:
        r = await c.post(f"{BASE_URL}{url}", json=data)
        r.raise_for_status()
        return r.json()


async def _get_async(url: str) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as c:
        r = await c.get(f"{BASE_URL}{url}")
        r.raise_for_status()
        return r.json()


# ── Public API (callback-based, Tkinter-safe) ──

def send_message(
    message: str,
    on_done: Callable[[dict], None],
    project_id: str | None = None,
    mode: str | None = None,
) -> None:
    """Send a message to the orchestrator."""
    _run_async(_send_async(message, project_id, mode), on_done)


def fetch_projects(on_done: Callable[[list], None]) -> None:
    """Fetch all projects."""
    def _wrap(result):
        if isinstance(result, dict) and "error" in result:
            on_done([])
        else:
            on_done(result.get("projects", []))
    _run_async(_get_async("/api/orchestrator/projects"), _wrap)


def fetch_tasks(project_id: str, on_done: Callable[[list], None]) -> None:
    """Fetch tasks for a project."""
    def _wrap(result):
        if isinstance(result, dict) and "error" in result:
            on_done([])
        else:
            on_done(result.get("tasks", []))
    _run_async(_get_async(f"/api/orchestrator/tasks?project_id={project_id}"), _wrap)


def fetch_services(on_done: Callable[[list], None]) -> None:
    """Fetch service statuses."""
    def _wrap(result):
        if isinstance(result, dict) and "error" in result:
            on_done([])
        else:
            on_done(result.get("services", []))
    _run_async(_get_async("/api/services"), _wrap)


def create_project(name: str, goal: str = "", description: str = "",
                   on_done: Callable[[dict], None] | None = None) -> None:
    """Create a new project."""
    _run_async(_post_async("/api/orchestrator/projects", {
        "name": name, "goal": goal, "description": description,
    }), on_done)


def reset_orchestrator(on_done: Callable[[bool], None] | None = None) -> None:
    """Reset the orchestrator conversation."""
    def _wrap(result):
        if on_done:
            on_done(True)
    _run_async(_post_async("/api/orchestrator/reset", {}), _wrap)


def start_service(service_id: str, on_done: Callable[[dict], None] | None = None) -> None:
    """Start a service by ID."""
    _run_async(_post_async(f"/api/services/{service_id}/start", {}), on_done)


def stop_service(service_id: str, on_done: Callable[[dict], None] | None = None) -> None:
    """Stop a service by ID."""
    _run_async(_post_async(f"/api/services/{service_id}/stop", {}), on_done)


def fetch_capabilities(on_done: Callable[[list], None]) -> None:
    """Fetch all agent capabilities from the registry."""
    def _wrap(result):
        if isinstance(result, dict) and "error" in result:
            on_done([])
        else:
            on_done(result.get("capabilities", []))
    _run_async(_get_async("/api/orchestrator/capabilities"), _wrap)


def register_capability(
    soul_id: str,
    agent_role: str,
    display_name: str,
    description: str,
    capabilities: list | None = None,
    access_level: str = "read-only",
    on_done: Callable[[dict], None] | None = None,
) -> None:
    """Register or update an agent's capabilities in the registry."""
    _run_async(_post_async("/api/orchestrator/capabilities", {
        "soul_id": soul_id,
        "agent_role": agent_role,
        "display_name": display_name,
        "description": description,
        "capabilities": capabilities or [],
        "access_level": access_level,
    }), on_done)
