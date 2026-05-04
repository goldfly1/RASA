"""Projects panel — project list, task DAG tree, create dialog."""

from __future__ import annotations

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.state import state
from rasa.gui_nice.theme import DIM, STATUS_COLORS, SURFACE_BG, TASK_STATUS_COLORS


class ProjectsPanel:
    """Project management with task DAG visualization."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._project_select: ui.select | None = None
        self._task_tree: ui.tree | None = None
        self._detail_card: ui.card | None = None
        self._detail_content: ui.column | None = None
        self._projects: list[dict] = []
        self._all_tasks: list[dict] = []

    def build(self):
        with ui.row().classes('w-full gap-2 items-center'):
            ui.label("Project:").classes('text-dim text-sm')
            self._project_select = ui.select(
                [], value=None, on_change=self._on_project_change,
            ).classes('min-w-[250px]').props("clearable")

            ui.button("New Project", icon="add", on_click=self._show_create_dialog).props("size=sm")

        with ui.row().classes('w-full gap-4'):
            with ui.column().classes('flex-1 gap-2'):
                ui.label("Task DAG").classes('text-dim text-xs uppercase tracking-wider')
                self._task_tree = ui.tree(
                    [], label_key="label", on_select=self._on_task_select,
                ).classes('w-full')

            with ui.column().classes('w-[380px] gap-2'):
                ui.label("Details").classes('text-dim text-xs uppercase tracking-wider')
                self._detail_card = ui.card().classes('w-full')
                with self._detail_card:
                    self._detail_content = ui.column().classes('w-full gap-1')
                    ui.label("Select a task to view details").classes('text-dim text-sm')

        ui.timer(10.0, self._refresh)

    async def _refresh(self):
        proj_result = await self.api.get_projects()
        if proj_result.ok:
            data = proj_result.data
            self._projects = data if isinstance(data, list) else data.get("projects", [])

            options = {p["id"]: f"{p['name']} ({p.get('status', '?')})" for p in self._projects}
            if self._project_select:
                old = self._project_select.value
                self._project_select.options = options
                self._project_select.set_value(old if old in options else None)
                self._project_select.update()

        # Refresh tasks if project selected
        if self._project_select and self._project_select.value:
            await self._load_tasks(self._project_select.value)

    async def _on_project_change(self):
        pid = self._project_select.value if self._project_select else None
        if pid:
            state.selected_project_id = pid
            proj = next((p for p in self._projects if p["id"] == pid), None)
            state.selected_project_name = proj["name"] if proj else pid
            await self._load_tasks(pid)
        else:
            state.selected_project_id = None
            state.selected_project_name = "(none)"
            if self._task_tree:
                self._task_tree.nodes = []
                self._task_tree.update()

    async def _load_tasks(self, project_id: str):
        result = await self.api.get_tasks(project_id)
        if not result.ok:
            return

        data = result.data
        self._all_tasks = data if isinstance(data, list) else data.get("tasks", [])
        state.last_task_snapshot = self._all_tasks
        self._build_task_tree()

    def _build_task_tree(self):
        if not self._task_tree:
            return

        # Build parent-child mapping
        children_of: dict[str, list[dict]] = {}
        task_map: dict[str, dict] = {}
        for t in self._all_tasks:
            tid = t.get("id", "")
            task_map[tid] = t
            pid = t.get("parent_id") or t.get("project_id", "")
            children_of.setdefault(pid, []).append(t)

        def make_node(t: dict) -> dict:
            tid = t["id"]
            status = t.get("status", "PENDING")
            label = f"{t.get('title', t.get('name', tid[:8]))}  [{status}]"
            children = children_of.get(tid, [])
            return {
                "id": tid,
                "label": label,
                "children": [make_node(c) for c in children],
            }

        roots = [t for t in self._all_tasks if t.get("parent_id") is None]
        nodes = [make_node(t) for t in roots]

        self._task_tree.nodes = nodes
        self._task_tree.expand()
        self._task_tree.update()

    async def _on_task_select(self, event):
        tid = event.value
        if not tid:
            return
        t = next((x for x in self._all_tasks if x["id"] == tid), None)
        if not t or not self._detail_content:
            return

        self._detail_content.clear()
        status = t.get("status", "?")
        color = TASK_STATUS_COLORS.get(status, DIM)

        with self._detail_content:
            ui.label(t.get("title", t.get("name", tid))).classes('text-sm font-bold')
            ui.badge(status, color=color).props("size=sm")
            if t.get("description"):
                ui.label(t["description"]).classes('text-sm text-dim mt-1')
            ui.separator().classes('my-1')
            _field(self._detail_content, "ID", tid[:12] + "...")
            _field(self._detail_content, "Soul", t.get("soul_id", "-"))
            _field(self._detail_content, "Agent", t.get("agent_role", "-"))
            _field(self._detail_content, "Created", t.get("created_at", "-"))
            _field(self._detail_content, "Updated", t.get("updated_at", "-"))

    def _show_create_dialog(self):
        with ui.dialog() as dialog, ui.card().classes('w-[400px]'):
            ui.label("Create Project").classes('text-lg font-bold')
            name = ui.input("Name", placeholder="Project name").classes('w-full')
            goal = ui.input("Goal", placeholder="Project goal").classes('w-full')
            desc = ui.textarea("Description", placeholder="Optional description").classes('w-full')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button("Cancel", on_click=dialog.close)
                ui.button("Create", color="primary", on_click=lambda: self._do_create(
                    name.value, goal.value, desc.value, dialog
                ))

        dialog.open()

    async def _do_create(self, name: str, goal: str, desc: str, dialog):
        if not name.strip():
            ui.notify("Name is required", color="negative")
            return
        result = await self.api.create_project(name.strip(), goal, desc)
        if result.ok:
            ui.notify(f"Project '{name}' created", color="positive")
            dialog.close()
            await self._refresh()
        else:
            ui.notify(f"Failed: {result.error}", color="negative")


def _field(parent: ui.column, label: str, value: str):
    with parent:
        with ui.row().classes('items-center gap-2 w-full'):
            ui.label(f"{label}:").classes('text-dim text-xs mono')
            ui.label(value).classes('text-xs mono')
