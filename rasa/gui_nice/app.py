"""RASA NiceGUI web dashboard — main app with tab layout."""

from __future__ import annotations

from nicegui import ui

from rasa.gui_nice import theme
from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.overview_panel import OverviewPanel
from rasa.gui_nice.services_panel import ServicesPanel
from rasa.gui_nice.projects_panel import ProjectsPanel
from rasa.gui_nice.agents_panel import AgentsPanel
from rasa.gui_nice.traces_panel import TracesPanel
from rasa.gui_nice.reviews_panel import ReviewsPanel
from rasa.gui_nice.terminal_panel import TerminalPanel


def create(api: ApiClient) -> None:
    """Build the full dashboard UI."""
    theme.apply()

    # ── Header with fixed tabs ──
    with ui.header(elevated=True).classes('items-center px-4 py-2'):
        with ui.row().classes('w-full items-center'):
            ui.label("RASA Command Center").classes('text-xl font-bold')
            ui.space()
            status_label = ui.label("Connecting...").classes('text-dim text-sm mono')
            ui.icon("cloud", color=theme.DIM).props("size=18px")

        with ui.tabs().classes('w-full') as tabs:
            ui.tab("Overview", icon="dashboard")
            ui.tab("Services", icon="health_and_safety")
            ui.tab("Projects", icon="folder")
            ui.tab("Agents", icon="smart_toy")
            ui.tab("Traces", icon="timeline")
            ui.tab("Reviews", icon="rate_review")
            ui.tab("Terminal", icon="terminal")

    # ── Tab panels ──
    with ui.tab_panels(tabs, value="Overview").classes('w-full'):
        with ui.tab_panel("Overview"):
            OverviewPanel(api).build()
        with ui.tab_panel("Services"):
            ServicesPanel(api).build()
        with ui.tab_panel("Projects"):
            ProjectsPanel(api).build()
        with ui.tab_panel("Agents"):
            AgentsPanel(api).build()
        with ui.tab_panel("Traces"):
            TracesPanel(api).build()
        with ui.tab_panel("Reviews"):
            ReviewsPanel(api).build()
        with ui.tab_panel("Terminal"):
            TerminalPanel(api).build()

    # ── Footer ──
    with ui.footer().classes('bg-surface border-custom items-center px-4 py-1'):
        ui.label("v0.1.0").classes('text-dim text-xs')
        ui.space()
        ui.label("RASA Command Center").classes('text-dim text-xs')

    # ── Health check on timer ──
    async def _check_server():
        result = await api.get_about()
        if result.ok:
            status_label.text = "Connected"
            status_label.classes(replace='text-success text-sm mono')
        else:
            status_label.text = "Offline"
            status_label.classes(replace='text-error text-sm mono')

    ui.timer(5.0, _check_server)


def run(host: str = "127.0.0.1", port: int = 8401) -> None:
    """Launch the NiceGUI dashboard."""
    api = ApiClient()
    create(api)
    ui.run(
        host=host,
        port=port,
        title="RASA Command Center",
        favicon="🤖",
        dark=True,
        reload=False,
        show=False,
    )


if __name__ == "__main__":
    run()
