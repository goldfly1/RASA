"""Async HTTP client for the RASA API server on :8400."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


BASE = os.environ.get("RASA_API_URL", "http://127.0.0.1:8400")
TIMEOUT = 300.0


@dataclass
class ApiResult:
    ok: bool
    data: Any = None
    error: str = ""


class ApiClient:
    """Thin async wrapper around the :8400 API."""

    def __init__(self, base: str = BASE):
        self.base = base
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base, timeout=TIMEOUT)
        return self._client

    async def _get(self, path: str) -> ApiResult:
        client = await self._get_client()
        try:
            r = await client.get(path)
            r.raise_for_status()
            return ApiResult(ok=True, data=r.json())
        except httpx.HTTPStatusError as e:
            return ApiResult(ok=False, error=f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            return ApiResult(ok=False, error=str(e))

    async def _post(self, path: str, data: dict | None = None) -> ApiResult:
        client = await self._get_client()
        try:
            r = await client.post(path, json=data or {})
            r.raise_for_status()
            return ApiResult(ok=True, data=r.json())
        except httpx.HTTPStatusError as e:
            return ApiResult(ok=False, error=f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            return ApiResult(ok=False, error=str(e))

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Services ──

    async def get_services(self) -> ApiResult:
        return await self._get("/api/services")

    async def start_service(self, service_id: str) -> ApiResult:
        return await self._post(f"/api/services/{service_id}/start")

    async def stop_service(self, service_id: str) -> ApiResult:
        return await self._post(f"/api/services/{service_id}/stop")

    # ── Projects ──

    async def get_projects(self) -> ApiResult:
        return await self._get("/api/orchestrator/projects")

    async def create_project(self, name: str, goal: str = "", description: str = "") -> ApiResult:
        return await self._post("/api/orchestrator/projects", {
            "name": name, "goal": goal, "description": description,
        })

    # ── Tasks ──

    async def get_tasks(self, project_id: str | None = None) -> ApiResult:
        path = f"/api/orchestrator/tasks?project_id={project_id}" if project_id else "/api/orchestrator/tasks"
        return await self._get(path)

    # ── Capabilities ──

    async def get_capabilities(self) -> ApiResult:
        return await self._get("/api/orchestrator/capabilities")

    async def register_capability(
        self, soul_id: str, agent_role: str = "",
        display_name: str = "", description: str = "",
        capabilities: list | None = None, access_level: str = "read-only",
    ) -> ApiResult:
        return await self._post("/api/orchestrator/capabilities", {
            "soul_id": soul_id, "agent_role": agent_role,
            "display_name": display_name, "description": description,
            "capabilities": capabilities or [], "access_level": access_level,
        })

    # ── Chat / Relay ──

    async def send_message(self, message: str, project_id: str | None = None, mode: str | None = None) -> ApiResult:
        payload = {"message": message}
        if project_id:
            payload["project_id"] = project_id
        if mode:
            payload["mode"] = mode
        return await self._post("/api/orchestrator/send", payload)

    async def orchestrator_send_direct(
        self,
        message: str,
        project_id: str | None = None,
        mode: str | None = None,
    ) -> ApiResult:
        """Send a message directly to the OrchestratorRuntime (not file relay)."""
        payload = {"message": message}
        if project_id:
            payload["project_id"] = project_id
        if mode:
            payload["mode"] = mode
        return await self._post("/api/orchestrator/direct", payload)

    async def reset_orchestrator(self) -> ApiResult:
        return await self._post("/api/orchestrator/reset")

    # ── System ──

    async def get_about(self) -> ApiResult:
        return await self._get("/api/about")

    # ── Human-in-the-Loop Reviews ──

    async def get_reviews(
        self,
        limit: int = 50,
        status: str | None = None,
        offset: int = 0,
    ) -> ApiResult:
        params = f"limit={limit}&offset={offset}"
        if status:
            params += f"&status={status}"
        return await self._get(f"/api/reviews?{params}")

    async def respond_to_review(
        self,
        review_id: str,
        response: str,
        reviewer: str = "dashboard",
    ) -> ApiResult:
        return await self._post(f"/api/reviews/{review_id}/respond", {
            "response": response,
            "reviewer": reviewer,
        })
