"""Services panel — grouped health cards with start/stop controls."""

from __future__ import annotations

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.theme import (
    GROUP_LABELS,
    GROUP_ORDER,
    STATUS_COLORS,
    DIM,
    SURFACE_BG,
)


class ServicesPanel:
    """Service monitoring with group cards, status dots, and start/stop."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._container: ui.column | None = None
        self._error_label: ui.label | None = None

    def build(self):
        with ui.column().classes('w-full gap-2'):
            self._error_label = ui.label("").classes('text-error text-sm')
            self._container = ui.column().classes('w-full gap-4')

        ui.timer(5.0, self._refresh)

    async def _refresh(self):
        result = await self.api.get_services()
        if not result.ok:
            if self._error_label:
                self._error_label.text = f"Failed to fetch services: {result.error}"
            return

        if self._error_label:
            self._error_label.text = ""

        data = result.data
        services = data if isinstance(data, list) else data.get("services", [])

        groups: dict[str, list[dict]] = {}
        for svc in services:
            g = svc.get("group", "other")
            groups.setdefault(g, []).append(svc)

        if not self._container:
            return

        self._container.clear()

        for group_id in GROUP_ORDER:
            if group_id not in groups:
                continue
            label = GROUP_LABELS.get(group_id, group_id)
            items = sorted(groups[group_id], key=lambda s: s.get("display_name", ""))

            with self._container:
                ui.label(label).classes('text-dim text-xs uppercase tracking-wider mt-2')

            for svc in items:
                status = svc.get("status", "unknown")
                color = STATUS_COLORS.get(status, DIM)
                detail = svc.get("status_detail", "")
                can_start = svc.get("can_start", False)
                is_running = status == "running"
                svc_id = svc["id"]

                with self._container:
                    with ui.card().classes('w-full'):
                        with ui.row().classes('w-full items-center gap-3'):
                            ui.icon("circle", color=color).props("size=14px")
                            with ui.column().classes('gap-0 flex-1'):
                                ui.label(svc.get("display_name", svc_id)).classes('text-sm font-bold')
                                ui.label(detail).classes('text-xs text-dim')
                            if can_start:
                                if is_running:
                                    ui.button("Stop", color="negative",
                                              on_click=lambda i=svc_id: self._do_stop(i)).props("size=sm")
                                else:
                                    ui.button("Start", color="positive",
                                              on_click=lambda i=svc_id: self._do_start(i)).props("size=sm")

    async def _do_start(self, service_id: str):
        result = await self.api.start_service(service_id)
        if not result.ok:
            ui.notify(f"Start failed: {result.error}", color="negative")
        else:
            ui.notify(f"Starting {service_id}...", color="positive")

    async def _do_stop(self, service_id: str):
        result = await self.api.stop_service(service_id)
        if not result.ok:
            ui.notify(f"Stop failed: {result.error}", color="negative")
        else:
            ui.notify(f"Stopping {service_id}...", color="warning")
