from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from rasa.gui.chat import list_souls, send_message, reset_conversation
from rasa.gui import metrics
from rasa.gui.health import HealthChecker
from rasa.gui.process import AlreadyRunningError, NotRunningError, ProcessManager
from rasa.gui.registry import build_registry, get_service_map
from rasa.orchestrator.runtime import OrchestratorRuntime
from rasa.orchestrator.reviews import ReviewManager

# ── Claude Code relay directories ──

RELAY_DIR = Path(__file__).parent.parent.parent / ".orch_relay"
INBOX_DIR = RELAY_DIR / "inbox"
OUTBOX_DIR = RELAY_DIR / "outbox"

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
        _orchestrator = OrchestratorRuntime(
            process_manager=process_manager,
            health_cache=_health_cache,
            health_cache_lock=_health_cache_lock,
            service_map=service_map,
            registry=registry,
        )
    return _orchestrator


async def orchestrator_send(request):
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)
    project_id = body.get("project_id")
    mode = body.get("mode")

    # Write message to Claude Code relay inbox
    ticket_id = str(uuid.uuid4())
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    msg = {
        "ticket_id": ticket_id,
        "message": message,
        "project_id": project_id,
        "mode": mode,
        "timestamp": time.time(),
    }
    (INBOX_DIR / f"{ticket_id}.json").write_text(json.dumps(msg, indent=2))

    # Poll for response (up to 300s)
    ticket_path = OUTBOX_DIR / f"{ticket_id}.json"
    deadline = time.time() + 300
    while time.time() < deadline:
        if ticket_path.exists():
            response = json.loads(ticket_path.read_text())
            ticket_path.unlink(missing_ok=True)
            return JSONResponse(response)
        await asyncio.sleep(0.5)

    return JSONResponse(
        {"error": "Orchestrator did not respond within 300s. "
                   "Is the orchestrator (Claude Code) running and monitoring .orch_relay/inbox/?"},
        status_code=504,
    )


async def orchestrator_direct(request):
    """Call OrchestratorRuntime.send_message() directly (not file relay)."""
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
        print(f"ERROR in orchestrator_direct: {type(e).__name__}: {e}", flush=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def orchestrator_direct_stream(request):
    """SSE streaming endpoint for OrchestratorRuntime.send_message()."""
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

    async def event_generator():
        try:
            async def emit(event: dict):
                sse = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                yield sse

            # Wrapper that yields SSE-formatted bytes via the emit closure
            async def on_event(evt: dict):
                sse = f"event: {evt['type']}\ndata: {json.dumps(evt)}\n\n"
                # We can't yield from a callback, so we push into a queue
                await _stream_queue.put(sse)

            _stream_queue: asyncio.Queue = asyncio.Queue()
            _stream_task = asyncio.create_task(orch.send_message(message, on_event=on_event))

            # Yield events as they arrive
            while True:
                try:
                    sse = await asyncio.wait_for(_stream_queue.get(), timeout=300)
                    yield sse.encode("utf-8")
                    if '"type": "done"' in sse:
                        break
                except asyncio.TimeoutError:
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'text': 'Response timed out'})}\n\n".encode("utf-8")
                    break

            # Ensure task completed
            await _stream_task
        except Exception as e:
            print(f"ERROR in orchestrator_direct_stream: {type(e).__name__}: {e}", flush=True)
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'text': str(e)})}\n\n".encode("utf-8")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    project_id = project["id"]

    # Auto-trigger planner interview for the new project
    try:
        from rasa.orchestrator.delegator import TaskDelegator
        td = TaskDelegator()
        task_id = td.create_task(
            soul_id="planner-v1",
            title=f"Plan: {name}",
            description=(
                f"A new project has been created: **{name}**.\n\n"
                f"Goal: {goal or 'Not specified'}\n"
                f"Description: {description or 'Not specified'}\n\n"
                "Conduct an interview with the user to refine the project scope, "
                "break down the goal into actionable tasks, and produce a plan. "
                "Ask clarifying questions about requirements, timeline, and constraints."
            ),
        )
        td.assign_task(task_id)
    except Exception as e:
        print(f"WARN: Failed to auto-create planner task: {e}", flush=True)

    return JSONResponse({"project": project})


async def orchestrator_capabilities(request):
    from rasa.orchestrator.capabilities import CapabilityRegistry
    cr = CapabilityRegistry()
    caps = cr.list_capabilities()
    return JSONResponse({"capabilities": caps})


async def orchestrator_register_capability(request):
    body = await request.json()
    soul_id = body.get("soul_id", "").strip()
    if not soul_id:
        return JSONResponse({"error": "soul_id is required"}, status_code=400)
    from rasa.orchestrator.capabilities import CapabilityRegistry
    cr = CapabilityRegistry()
    cap = cr.register_capability(
        soul_id=soul_id,
        agent_role=body.get("agent_role", ""),
        display_name=body.get("display_name", ""),
        description=body.get("description", ""),
        capabilities=body.get("capabilities"),
        access_level=body.get("access_level", "read-only"),
    )
    return JSONResponse({"capability": cap})


# ── Human-in-the-Loop Reviews ──

_review_mgr: ReviewManager | None = None


def _get_review_mgr() -> ReviewManager:
    global _review_mgr
    if _review_mgr is None:
        _review_mgr = ReviewManager()
    return _review_mgr


async def reviews_list(request):
    mgr = _get_review_mgr()
    limit = int(request.query_params.get("limit", 50))
    status = request.query_params.get("status")
    offset = int(request.query_params.get("offset", 0))
    reviews = mgr.list_reviews(limit=limit, offset=offset, status_filter=status)
    pending_count = len(mgr.get_pending_reviews())
    return JSONResponse({"reviews": reviews, "pending_count": pending_count})


async def reviews_respond(request):
    body = await request.json()
    review_id = request.path_params["id"]
    response = body.get("response", "").strip()
    if not response:
        return JSONResponse({"error": "Response text is required"}, status_code=400)
    mgr = _get_review_mgr()
    ok = mgr.respond_to_review(
        review_id=review_id,
        response=response,
        reviewer=body.get("reviewer", "dashboard"),
    )
    if not ok:
        return JSONResponse(
            {"error": f"Review {review_id} not found or already answered"},
            status_code=404,
        )
    review = mgr.get_review(review_id)
    return JSONResponse({"review": review})


# ── App ──

from contextlib import asynccontextmanager


_relay_cleanup_task: asyncio.Task | None = None


async def _relay_cleanup_loop():
    """Remove stale relay files older than 1 hour."""
    while True:
        now = time.time()
        for d in (INBOX_DIR, OUTBOX_DIR):
            if not d.exists():
                continue
            for p in d.glob("*.json"):
                try:
                    if now - p.stat().st_mtime > 3600:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass
        await asyncio.sleep(600)  # every 10 minutes



# Metrics endpoints

async def metrics_summary(request):
    return JSONResponse(metrics.get_all_metrics())

async def metrics_tasks(request):
    return JSONResponse(metrics.get_task_summary())

async def metrics_agents(request):
    return JSONResponse(metrics.get_agent_uptime())

async def metrics_souls(request):
    return JSONResponse({
        "performance": metrics.get_soul_performance(),
        "drift": metrics.get_drift_status(),
    })

async def metrics_live_agents(request):
    return JSONResponse(metrics.get_live_agents())

async def metrics_resources(request):
    """Host-level resource usage via psutil."""
    try:
        import psutil
        return JSONResponse({
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "cpu_count": psutil.cpu_count(),
            "memory": dict(psutil.virtual_memory()._asdict()),
            "disk": dict(psutil.disk_usage(str(Path(__file__).parent.parent.parent))._asdict()),
        })
    except ImportError:
        return JSONResponse({"error": "psutil not installed"}, status_code=500)

@asynccontextmanager
async def lifespan(app):
    await health_checker.start()
    global _health_task, _relay_cleanup_task
    _health_task = asyncio.create_task(_health_loop())
    _relay_cleanup_task = asyncio.create_task(_relay_cleanup_loop())
    yield
    if _health_task:
        _health_task.cancel()
    if _relay_cleanup_task:
        _relay_cleanup_task.cancel()
    await process_manager.stop_all()
    await health_checker.stop()


routes = [
    Route("/api/metrics", metrics_summary),
    Route("/api/metrics/tasks", metrics_tasks),
    Route("/api/metrics/agents", metrics_agents),
    Route("/api/metrics/souls", metrics_souls),
    Route("/api/metrics/live-agents", metrics_live_agents),
    Route("/api/metrics/resources", metrics_resources),
    Route("/api/services", list_services),
    Route("/api/services/{id}/start", start_service, methods=["POST"]),
    Route("/api/services/{id}/stop", stop_service, methods=["POST"]),
    Route("/api/slash-commands", list_slash_commands),
    Route("/api/about", about_info),
    Route("/api/chat/send", chat_send, methods=["POST"]),
    Route("/api/chat/reset", chat_reset, methods=["POST"]),
    Route("/api/chat/souls", chat_souls),
    Route("/api/orchestrator/send", orchestrator_send, methods=["POST"]),
    Route("/api/orchestrator/direct", orchestrator_direct, methods=["POST"]),
    Route("/api/orchestrator/direct/stream", orchestrator_direct_stream, methods=["POST"]),
    Route("/api/orchestrator/reset", orchestrator_reset, methods=["POST"]),
    Route("/api/orchestrator/tasks", orchestrator_tasks),
    Route("/api/orchestrator/projects", orchestrator_projects),
    Route("/api/orchestrator/projects", orchestrator_create_project, methods=["POST"]),
    Route("/api/orchestrator/capabilities", orchestrator_capabilities),
    Route("/api/orchestrator/capabilities", orchestrator_register_capability, methods=["POST"]),
    Route("/api/reviews", reviews_list),
    Route("/api/reviews/{id}/respond", reviews_respond, methods=["POST"]),
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
