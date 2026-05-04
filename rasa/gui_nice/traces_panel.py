"""Traces panel — task state change feed with status badges."""

from __future__ import annotations

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.state import state
from rasa.gui_nice.theme import DIM, ERROR, FG, STATUS_COLORS, SUCCESS, SURFACE_BG, TASK_STATUS_COLORS, WARNING


class TracesPanel:
    """Live task transition feed."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._feed: ui.column | None = None
        self._counter: ui.label | None = None
        self._known: dict[str, str] = {}  # task_id -> last known status
        self._prev_count = 0

    def build(self):
        with ui.column().classes('w-full gap-2'):
            with ui.row().classes('w-full items-center gap-2'):
                ui.label("Task State Changes").classes('text-sm font-bold')
                ui.space()
                self._counter = ui.label("0 events").classes('text-dim text-xs mono')

            with ui.card().classes('w-full'):
                with ui.scroll_area().classes('w-full h-[600px]'):
                    self._feed = ui.column().classes('w-full gap-1')
                    ui.label("Watching for task state changes...").classes('text-dim text-sm mono')

        ui.timer(5.0, self._refresh)

    async def _refresh(self):
        result = await self.api.get_tasks()
        if not result.ok:
            return

        data = result.data
        tasks = data if isinstance(data, list) else data.get("tasks", [])

        # Detect new/changed tasks
        for t in tasks:
            tid = t.get("id", "")
            status = t.get("status", "PENDING")
            prev = self._known.get(tid)

            if prev is None:
                self._known[tid] = status
                self._add_event("NEW", t, status)
            elif prev != status:
                self._known[tid] = status
                self._add_event(f"{prev} → {status}", t, status)

        if self._counter:
            self._counter.text = f"{len(self._known)} tasks tracked"

    def _add_event(self, transition: str, task: dict, status: str):
        if not self._feed:
            return

        color = TASK_STATUS_COLORS.get(status, DIM)
        title = task.get("title", task.get("name", task.get("id", "?")[:12]))
        ts = task.get("updated_at", task.get("created_at", ""))

        with self._feed:
            with ui.row().classes('items-center gap-2 text-xs mono w-full'):
                ui.badge(transition, color=color).props("size=sm")
                ui.label(title).classes('text-white')
                ui.label(ts).classes('text-dim')
