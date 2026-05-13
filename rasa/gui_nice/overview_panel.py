"""Overview panel — summary cards, charts, recent activity."""

from __future__ import annotations

import asyncio

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.theme import ACCENT, DIM, ERROR, FG, STATUS_COLORS, SUCCESS, SURFACE_BG, WARNING


class OverviewPanel:
    """Dashboard overview with metrics and charts."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._cards: dict[str, ui.element] = {}

    def build(self):
        with ui.column().classes('w-full gap-4'):
            self._build_metric_row()
            self._build_chart_row()
            self._build_activity_list()

        ui.timer(10.0, self._refresh)

    def _build_metric_row(self):
        with ui.row().classes('w-full gap-4 flex-wrap'):
            self._cards["tasks_done"] = self._metric_card(
                "Tasks Done", "?", "Completed (24h)"
            )
            self._cards["tasks_failed"] = self._metric_card(
                "Failed", "?", "Failed (24h)"
            )
            self._cards["agents_live"] = self._metric_card(
                "Live Agents", "?", "Daemon agents with heartbeat"
            )
            self._cards["cpu"] = self._metric_card(
                "CPU", "?", "Host CPU %"
            )
            self._cards["memory"] = self._metric_card(
                "Memory", "?", "Host RAM used %"
            )

    def _metric_card(self, title: str, value: str, subtitle: str) -> ui.element:
        with ui.card().classes('flex-1 min-w-[150px]') as card:
            with ui.column().classes('items-center gap-1'):
                ui.label(title).classes('text-dim text-xs uppercase tracking-wider')
                val = ui.label(value).classes('text-3xl font-bold mono')
                ui.label(subtitle).classes('text-dim text-xs')
        card.val_label = val
        return card

    def _build_chart_row(self):
        with ui.row().classes('w-full gap-4'):
            with ui.card().classes('flex-1'):
                ui.label("Service Status").classes('text-sm font-bold mb-2')
                self._doughnut = ui.echart(
                    {
                        "tooltip": {"trigger": "item"},
                        "legend": {"bottom": 0, "textStyle": {"color": DIM}},
                        "series": [{
                            "type": "pie",
                            "radius": ["50%", "70%"],
                            "avoidLabelOverlap": False,
                            "label": {"show": False},
                            "data": [
                                {"value": 0, "name": "Running", "itemStyle": {"color": SUCCESS}},
                                {"value": 0, "name": "Stopped", "itemStyle": {"color": ERROR}},
                            ],
                        }],
                    }
                ).classes('h-48')

            with ui.card().classes('flex-1'):
                ui.label("Task Distribution").classes('text-sm font-bold mb-2')
                self._bar = ui.echart(
                    {
                        "tooltip": {"trigger": "axis"},
                        "legend": {"show": False},
                        "xAxis": {
                            "type": "category",
                            "data": ["PENDING", "RUNNING", "COMPLETED", "FAILED"],
                            "axisLabel": {"color": DIM},
                        },
                        "yAxis": {
                            "type": "value",
                            "axisLabel": {"color": DIM},
                            "splitLine": {"lineStyle": {"color": "#30363d"}},
                            "minInterval": 1,
                        },
                        "series": [{
                            "type": "bar",
                            "data": [
                                {"value": 0, "itemStyle": {"color": DIM}},
                                {"value": 0, "itemStyle": {"color": WARNING}},
                                {"value": 0, "itemStyle": {"color": SUCCESS}},
                                {"value": 0, "itemStyle": {"color": ERROR}},
                            ],
                            "barWidth": "40%",
                        }],
                    }
                ).classes('h-48')

    def _build_activity_list(self):
        with ui.card().classes('w-full'):
            ui.label("Recent Activity").classes('text-sm font-bold mb-2')
            self._activity_log = ui.column().classes('w-full gap-1')

    async def _refresh(self):
        svc_result, proj_result, cap_result, task_result, metrics_result, res_result = await asyncio.gather(
            self.api.get_services(),
            self.api.get_projects(),
            self.api.get_capabilities(),
            self.api.get_tasks(),
            self.api.get_metrics_tasks(),
            self.api.get_metrics_resources(),
        )

        if svc_result.ok:
            services = svc_result.data.get("services", []) if isinstance(svc_result.data, dict) else svc_result.data
            total = len(services)
            running = sum(1 for s in services if s.get("status") == "running")
            self._cards["services"].val_label.text = str(total)
            self._cards["running"].val_label.text = str(running)

            self._doughnut.options["series"][0]["data"][0]["value"] = running
            self._doughnut.options["series"][0]["data"][1]["value"] = total - running
            self._doughnut.update()

        if proj_result.ok:
            projects = proj_result.data if isinstance(proj_result.data, list) else proj_result.data.get("projects", [])
            self._cards["projects"].val_label.text = str(len(projects))

        if cap_result.ok:
            caps = cap_result.data if isinstance(cap_result.data, list) else cap_result.data.get("capabilities", [])
            self._cards["agents"].val_label.text = str(len(caps))

        if task_result.ok:
            tasks = task_result.data if isinstance(task_result.data, list) else task_result.data.get("tasks", [])
            statuses = {"PENDING": 0, "RUNNING": 0, "COMPLETED": 0, "FAILED": 0}
            for t in tasks:
                s = t.get("status", "PENDING")
                if s in statuses:
                    statuses[s] += 1
            self._bar.options["series"][0]["data"][0]["value"] = statuses["PENDING"]
            self._bar.options["series"][0]["data"][1]["value"] = statuses["RUNNING"]
            self._bar.options["series"][0]["data"][2]["value"] = statuses["COMPLETED"]
            self._bar.options["series"][0]["data"][3]["value"] = statuses["FAILED"]
            self._bar.update()

        if metrics_result.ok:
            data = metrics_result.data or {}
            by_status = data.get("by_status", [])
            status_map = {r.get("status", ""): r.get("count", 0) for r in by_status}
            completed = status_map.get("COMPLETED", 0)
            failed = status_map.get("FAILED", 0)
            self._cards["tasks_done"].val_label.text = str(completed)
            self._cards["tasks_failed"].val_label.text = str(failed)

        if res_result.ok:
            res = res_result.data or {}
            self._cards["cpu"].val_label.text = f"{res.get('cpu_percent', 0):.0f}%"
            mem = res.get("memory", {})
            mem_pct = mem.get("percent", 0)
            self._cards["memory"].val_label.text = f"{mem_pct:.0f}%"

        # Live agent count from metrics or capabilities
        live_result = await self.api.get_metrics_live_agents()
        if live_result.ok:
            agents = live_result.data if isinstance(live_result.data, list) else []
            self._cards["agents_live"].val_label.text = str(len(agents))

            self._activity_log.clear()
            recent = sorted(tasks, key=lambda t: t.get("updated_at", ""), reverse=True)[:10]
            for t in recent:
                with self._activity_log:
                    with ui.row().classes('items-center gap-2 text-xs mono w-full'):
                        st = t.get("status", "?")
                        color = STATUS_COLORS.get(st.lower(), DIM)
                        ui.badge(st, color=color).props("size=sm")
                        ui.label(t.get("title", t.get("name", "?"))).classes('text-white')
                        ui.label(t.get("updated_at", "")).classes('text-dim')

