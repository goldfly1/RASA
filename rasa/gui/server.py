from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from rasa.gui.chat import list_souls, send_message, reset_conversation
from rasa.gui.health import HealthChecker
from rasa.gui.process import AlreadyRunningError, NotRunningError, ProcessManager
from rasa.gui.registry import build_registry, get_service_map
from rasa.orchestrator.runtime import OrchestratorRuntime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

registry = build_registry()
service_map = get_service_map()
health_checker = HealthChecker(registry)
process_manager = ProcessManager(PROJECT_ROOT)
health_checker.process_manager = process_manager

# ── Background health-check loop ──

_health_task: asyncio.Task | None = None
_health_cache: dict[str, dict] = {}
_health_cache_lock = asyncio.Lock()


async def _health_loop():
    while True:
        results = await health_checker.check_all()
        # Build serializable response
        cache = {}
        for svc in registry:
            status = results.get(svc.id)
            cache[svc.id] = {
                "id": svc.id,
                "display_name": svc.display_name,
                "group": svc.group.value,
                "port": svc.port,
                "min_version": svc.min_version,
                "can_start": svc.can_start,
                "is_external": svc.is_external,
                "depends_on": svc.depends_on,
                "status": status.status if status else "unknown",
                "status_detail": status.status_detail if status else "",
                "pid": status.pid if status else None,
                "uptime_seconds": status.uptime_seconds if status else None,
                "managed": status.managed if status else False,
            }
        async with _health_cache_lock:
            _health_cache.clear()
            _health_cache.update(cache)
        await asyncio.sleep(5)


# ── Routes ──


async def list_services(request):
    async with _health_cache_lock:
        services = list(_health_cache.values()) if _health_cache else []
    if not services:
        # First run — build from registry with unknown status
        services = [
            {
                "id": svc.id,
                "display_name": svc.display_name,
                "group": svc.group.value,
                "port": svc.port,
                "min_version": svc.min_version,
                "can_start": svc.can_start,
                "is_external": svc.is_external,
                "depends_on": svc.depends_on,
                "status": "unknown",
                "status_detail": "Initializing...",
                "pid": None,
                "uptime_seconds": None,
                "managed": False,
            }
            for svc in registry
        ]
    return JSONResponse({"services": services, "poll_interval_seconds": 5})


async def start_service(request):
    service_id = request.path_params["id"]
    svc = service_map.get(service_id)
    if not svc:
        return JSONResponse({"detail": f"Service '{service_id}' not found"}, status_code=404)
    if not svc.can_start:
        return JSONResponse({"detail": f"Service '{service_id}' cannot be started from GUI"}, status_code=400)

    # Check dependencies
    deps_down = []
    for dep_id in svc.depends_on:
        async with _health_cache_lock:
            dep_status = _health_cache.get(dep_id, {}).get("status", "unknown")
        if dep_status != "running":
            deps_down.append(dep_id)
    if deps_down:
        return JSONResponse(
            {"detail": f"Service '{service_id}' requires: {', '.join(deps_down)} (not running)"},
            status_code=400,
        )

    try:
        pid = await process_manager.start(svc)
        # Give process a moment to crash (e.g. missing deps)
        await asyncio.sleep(0.5)
        exit_code = process_manager.get_exit_code(svc.id)
        if exit_code is not None:
            stderr = process_manager.get_stderr(svc.id)
            detail = f"Service '{service_id}' exited immediately (code {exit_code})"
            if stderr:
                detail += f": {stderr.strip()[:200]}"
            await process_manager.stop(svc.id)
            return JSONResponse({"detail": detail}, status_code=500)
        # Register with health checker
        health_checker.managed_pids[svc.id] = pid
        health_checker.managed_start_times[svc.id] = time.time()
        return JSONResponse({"id": service_id, "status": "starting", "pid": pid}, status_code=202)
    except AlreadyRunningError:
        return JSONResponse({"detail": f"Service '{service_id}' is already running"}, status_code=409)


async def stop_service(request):
    service_id = request.path_params["id"]
    # Always clean up health checker state
    health_checker.managed_pids.pop(service_id, None)
    health_checker.managed_start_times.pop(service_id, None)
    try:
        await process_manager.stop(service_id)
        return JSONResponse({"id": service_id, "status": "stopped"})
    except NotRunningError:
        return JSONResponse({"id": service_id, "status": "stopped", "detail": "Not managed"})


SLASH_COMMANDS = [
    ("Navigation", [
        ("/help", "Show help and available commands"),
        ("/clear", "Clear the conversation history"),
        ("/compact", "Compact conversation to save context tokens"),
        ("/summary", "Show a summary of the conversation so far"),
        ("/rename", "Rename the current conversation"),
    ]),
    ("Workflow", [
        ("/plan", "Create an implementation plan for the current task"),
        ("/review", "Review pull request changes"),
        ("/init", "Initialize CLAUDE.md in the project"),
        ("/loop", "Run a prompt on a recurring schedule (e.g., /loop 5m /status)"),
        ("/search", "Search the web or codebase"),
        ("/shell", "Run a shell command"),
        ("/ask", "Ask a general knowledge question"),
        ("/tutorial", "Start an interactive Claude Code tutorial"),
    ]),
    ("Debugging & Info", [
        ("/cost", "Show token usage and cost for the session"),
        ("/doctor", "Run environment diagnostics"),
        ("/bug", "Report a bug to the Claude Code team"),
    ]),
    ("Configuration", [
        ("/effort", "Set effort level for Claude's tool usage (1-5)"),
        ("/config", "View or modify settings"),
    ]),
    ("RASA", [
        ("/deploy", "Deploy a task to the RASA agent pool"),
        ("/agents", "Show status of all RASA agents"),
    ]),
]


async def list_slash_commands(request):
    commands = []
    for category, items in SLASH_COMMANDS:
        for cmd, desc in items:
            commands.append({"command": cmd, "description": desc, "category": category})
    return JSONResponse({"commands": commands})


async def about_info(request):
    import platform
    return JSONResponse({
        "rasa_version": "0.1.0",
        "python_version": sys.version.split()[0],
        "go_version": "1.24",
        "os": f"{platform.system()} {platform.release()}",
        "hostname": platform.node(),
        "project_root": PROJECT_ROOT,
    })


# ── Chat ──


async def chat_send(request):
    body = await request.json()
    soul = body.get("soul", "coder-v2-dev")
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)
    try:
        result = await send_message(soul, message)
        return JSONResponse(result)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": f"LLM call failed: {e.response.status_code}"}, status_code=502)
    except httpx.TimeoutException:
        return JSONResponse({"error": "LLM call timed out. The model took too long to respond. Try resetting the conversation and sending a simpler message."}, status_code=504)
    except Exception as e:
        print(f"ERROR in chat_send: {type(e).__name__}: {e}", flush=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def chat_reset(request):
    body = await request.json()
    soul = body.get("soul", "coder-v2-dev")
    reset_conversation(soul)
    return JSONResponse({"status": "reset"})


async def chat_souls(request):
    return JSONResponse({"souls": list_souls()})


# ── Orchestrator ──

_orchestrator: OrchestratorRuntime | None = None


def _get_orchestrator() -> OrchestratorRuntime:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = OrchestratorRuntime()
    return _orchestrator


async def orchestrator_send(request):
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)
    project_id = body.get("project_id")
    mode = body.get("mode")

    orch = _get_orchestrator()
    if project_id:
        orch.set_project(project_id)
    if mode:
        orch.set_mode(mode)

    try:
        result = await orch.send_message(message)
        return JSONResponse(result)
    except Exception as e:
        print(f"ERROR in orchestrator_send: {type(e).__name__}: {e}", flush=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def orchestrator_reset(request):
    _get_orchestrator().reset()
    return JSONResponse({"status": "reset"})


async def orchestrator_tasks(request):
    orch = _get_orchestrator()
    pid = request.query_params.get("project_id") or orch.project_id
    if not pid:
        return JSONResponse({"tasks": []})
    from rasa.orchestrator.delegator import TaskDelegator
    delegator = TaskDelegator()
    tasks = delegator.list_project_tasks(pid)
    return JSONResponse({"tasks": tasks})


async def orchestrator_projects(request):
    from rasa.orchestrator.project import ProjectManager
    pm = ProjectManager()
    projects = pm.list_projects()
    return JSONResponse({"projects": projects})


async def orchestrator_create_project(request):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Project name is required"}, status_code=400)
    goal = body.get("goal", "")
    description = body.get("description", "")
    from rasa.orchestrator.project import ProjectManager
    pm = ProjectManager()
    project = pm.create_project(name, goal, description)
    return JSONResponse({"project": project})


# ── App ──

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    await health_checker.start()
    global _health_task
    _health_task = asyncio.create_task(_health_loop())
    yield
    if _health_task:
        _health_task.cancel()
    await process_manager.stop_all()
    await health_checker.stop()


routes = [
    Route("/api/services", list_services),
    Route("/api/services/{id}/start", start_service, methods=["POST"]),
    Route("/api/services/{id}/stop", stop_service, methods=["POST"]),
    Route("/api/slash-commands", list_slash_commands),
    Route("/api/about", about_info),
    Route("/api/chat/send", chat_send, methods=["POST"]),
    Route("/api/chat/reset", chat_reset, methods=["POST"]),
    Route("/api/chat/souls", chat_souls),
    Route("/api/orchestrator/send", orchestrator_send, methods=["POST"]),
    Route("/api/orchestrator/reset", orchestrator_reset, methods=["POST"]),
    Route("/api/orchestrator/tasks", orchestrator_tasks),
    Route("/api/orchestrator/projects", orchestrator_projects),
    Route("/api/orchestrator/projects", orchestrator_create_project, methods=["POST"]),
]

# Only mount static if the directory exists and has an index.html
if os.path.isdir(STATIC_DIR):
    routes.append(Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="static"))

app = Starlette(
    debug=False,
    routes=routes,
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rasa.gui.server:app", host="127.0.0.1", port=8400, log_level="info")
