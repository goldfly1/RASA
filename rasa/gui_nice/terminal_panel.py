"""Terminal panel — agent operations console with delegation tree."""

from __future__ import annotations

import json
import time

import httpx
from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.state import state


class TerminalPanel:
    """Agent operations console — dispatch work and track sub-agent delegation."""

    COLORS = {
        "user": "#b3f6c0",
        "orch": "#e6e1cf",
        "tool": "#79c0ff",
        "task": "#d2a8ff",
        "done": "#56d364",
        "error": "#ff7b72",
        "info": "#8b949e",
    }

    def __init__(self, api: ApiClient):
        self.api = api
        self._tree: ui.column | None = None
        self._input: ui.input | None = None
        self._ready = True

    def build(self):
        with ui.column().classes("w-full gap-0 p-0"):
            # Console header bar
            with ui.row().classes("w-full items-center px-3 py-1 bg-black"):
                ui.label("RASA Agent Console").classes("text-green-400 text-xs mono font-bold")
                ui.space()
                self._project_label = ui.label().classes("text-dim text-xs mono")

            # Scrollable agent activity tree
            self._tree = ui.column().classes("w-full gap-0 p-2").style(
                "max-height:68vh; overflow-y:auto; "
                "background:#0d1117; border-radius:4px; "
                "font-family:Consolas,'Courier New',monospace; font-size:13px;"
            )

            self._entry("info", "Agent Console ready — type a goal to dispatch work to sub-agents.")

            # Command line
            with ui.row().classes("w-full items-center gap-0 px-2 py-1 bg-black"):
                ui.label("▶").classes("text-green-400 text-sm mono px-1")
                self._input = ui.input(
                    placeholder="type a goal for the orchestrator..."
                ).props("outlined dense").classes("flex-1").style(
                    "font-family:Consolas,'Courier New',monospace; "
                    "font-size:13px; background:#0d1117; color:#e6e1cf;"
                )
                self._input.on("keydown.enter", self._on_submit)

        self._update_project_label()

    def _update_project_label(self):
        if not hasattr(self, "_project_label") or not self._project_label:
            return
        pid = state.selected_project_id
        name = state.selected_project_name
        if pid:
            self._project_label.text = f"project: {name}"
            self._project_label.classes(replace="text-info text-xs mono")
        else:
            self._project_label.text = "no project — queue context unavailable"
            self._project_label.classes(replace="text-warning text-xs mono")

    def _entry(self, kind: str, text: str, detail: str | None = None):
        """Add a structured entry to the activity tree."""
        if not self._tree:
            return
        color = self.COLORS.get(kind, "#e6e1cf")
        icon = {"user": "❯", "orch": "◈", "tool": "◇", "task": "▷", "done": "✓", "error": "✗", "info": "·"}.get(kind, "·")
        stamp = time.strftime("%H:%M:%S")

        with self._tree:
            row = ui.row().classes("items-start gap-2 py-0")
            row.style(f"color:{color}")
            ui.label(f"{stamp} {icon}").classes("text-xs mono whitespace-nowrap")
            content = ui.label(text).classes("text-sm mono")
            content.style(f"color:{color}")

            if detail:
                detail_short = detail[:80] + "…" if len(detail) > 80 else detail
                detail_label = ui.label(f"({detail_short})").classes("text-xs mono").style("color:#8b949e")
                row.on("click", lambda d=detail: ui.notify(
                    d, position="top-right", multi_line=True, timeout=8000
                ))
                row.classes("cursor-pointer")

    async def _run_orch(self, cmd: str) -> dict | None:
        """Send a command to the orchestrator and return the result."""
        try:
            base = self.api.base.rstrip("/")
            async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as client:
                resp = await client.post(
                    f"{base}/api/orchestrator/direct",
                    json={"message": cmd, "project_id": state.selected_project_id},
                )
                if resp.status_code == 200:
                    return resp.json()
                self._entry("error", f"HTTP {resp.status_code}", resp.text[:300])
        except Exception as e:
            self._entry("error", str(e))
        return None

    async def _on_submit(self):
        if not self._input or not self._ready:
            return
        cmd = self._input.value.strip()
        if not cmd:
            return
        self._input.value = ""
        self._ready = False

        self._update_project_label()

        # Local commands
        if cmd == "/clear":
            self._tree.clear()
            self._entry("info", "Session cleared.")
            self._ready = True
            return

        # Show user command
        self._entry("user", cmd)

        # Dispatch to orchestrator
        data = await self._run_orch(cmd)
        if not data:
            self._ready = True
            return

        # Show orchestrator's main reply
        reply = data.get("reply", "")
        steps = data.get("steps", [])

        # Show each delegation step as an expandable tree entry
        for s in steps:
            name = s.get("name", "?")
            args = s.get("args", {})
            result = s.get("result", "")

            if name in ("task_create", "task_assign"):
                # Agent delegation — show as task node
                summary = f"delegating to {args.get('soul_id', '?')}: {args.get('title', result)[:120]}"
                self._entry("task", summary, result[:500])
            elif name == "task_query":
                # Status check — show compact
                tid = args.get("task_id", "?")[:12]
                status = "?"
                try:
                    rdata = json.loads(result) if isinstance(result, str) else result
                    status = rdata.get("status", "?")
                except Exception:
                    pass
                self._entry("tool", f"check task {tid} → {status}", result[:500])
            elif name in ("request_human_input", "check_human_response"):
                self._entry("tool", name.replace("_", " "), result[:500])
            else:
                # Generic tool call
                arg_preview = json.dumps(args)[:80] if args else ""
                self._entry("tool", f"{name} {arg_preview}", result[:500])

        # Show final orchestrator response
        if reply:
            self._entry("orch", reply[:2000] + ("…" if len(reply) > 2000 else ""))

        usage = data.get("usage", {})
        elapsed = data.get("elapsed_seconds", 0)
        self._entry("info", f"done — {elapsed}s · {usage.get('prompt_tokens', 0)}↑ {usage.get('completion_tokens', 0)}↓")

        self._ready = True
