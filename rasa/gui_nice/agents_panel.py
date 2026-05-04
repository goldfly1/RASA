"""Agents panel — capability registry table with expandable rows."""

from __future__ import annotations

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.theme import DIM, SURFACE_BG


class AgentsPanel:
    """Capability registry browser."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._table: ui.table | None = None
        self._error_label: ui.label | None = None

    def build(self):
        with ui.column().classes('w-full gap-2'):
            self._error_label = ui.label("").classes('text-error text-sm')

            columns = [
                {"name": "soul", "label": "Soul", "field": "soul", "sortable": True, "align": "left"},
                {"name": "role", "label": "Role", "field": "role", "sortable": True, "align": "left"},
                {"name": "display", "label": "Display Name", "field": "display", "sortable": True, "align": "left"},
                {"name": "access", "label": "Access", "field": "access", "sortable": True, "align": "center"},
                {"name": "capabilities", "label": "Capabilities", "field": "capabilities", "align": "left"},
            ]

            self._table = ui.table(columns=columns, rows=[], row_key="soul").classes('w-full')
            self._table.add_slot(
                "body-cell-capabilities",
                """
                <q-td :props="props">
                    <q-badge v-for="cap in props.value" :key="cap.name"
                             color="primary" class="q-mr-xs q-mb-xs" style="font-weight:400;">
                        {{ cap.name }}
                    </q-badge>
                </q-td>
                """,
            )

        ui.timer(30.0, self._refresh)

    async def _refresh(self):
        result = await self.api.get_capabilities()
        if not result.ok:
            if self._error_label:
                self._error_label.text = f"Failed to fetch capabilities: {result.error}"
            return

        if self._error_label:
            self._error_label.text = ""

        data = result.data
        caps = data if isinstance(data, list) else data.get("capabilities", [])

        rows = []
        for c in caps:
            rows.append({
                "soul": c.get("soul_id", ""),
                "role": c.get("agent_role", ""),
                "display": c.get("display_name", ""),
                "access": c.get("access_level", ""),
                "capabilities": [cap.get("name", "") for cap in c.get("capabilities", [])],
            })

        if self._table:
            self._table.rows = rows
            self._table.update()
