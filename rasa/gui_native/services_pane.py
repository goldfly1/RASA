"""Services health panel for the RASA native GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from rasa.gui_native import api_client

# ── Group display order & colors ──

GROUP_ORDER = ["infrastructure", "control-plane", "agents", "observability", "other"]
GROUP_LABELS = {
    "infrastructure": "Infrastructure",
    "control-plane": "Control Plane",
    "agents": "Agents",
    "observability": "Observability",
    "other": "Other",
}
STATUS_COLORS = {
    "running": "#22c55e",
    "starting": "#eab308",
    "stopped": "#ef4444",
    "unknown": "#8b949e",
    "error": "#ef4444",
}


class ServicesPane(ttk.Frame):
    """Services health panel with auto-refresh."""

    def __init__(self, parent: ttk.Notebook, **kwargs):
        super().__init__(parent, **kwargs)
        self._services: list[dict] = []
        self._after_id: str | None = None

        self._build_ui()
        self._refresh()

    def _build_ui(self):
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(header, text="Services", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        self._status_label = ttk.Label(header, text="", font=("Segoe UI", 10))
        self._status_label.pack(side=tk.RIGHT, padx=(0, 4))

        # Treeview
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        columns = ("status", "name", "version", "port", "uptime", "actions")
        self._tree = ttk.Treeview(
            container, columns=columns, show="tree headings", height=20
        )
        self._tree.heading("#0", text="Group")
        self._tree.heading("status", text="")
        self._tree.heading("name", text="Service")
        self._tree.heading("version", text="Version")
        self._tree.heading("port", text="Port")
        self._tree.heading("uptime", text="Uptime")
        self._tree.heading("actions", text="Actions")

        self._tree.column("#0", width=140, minwidth=120)
        self._tree.column("status", width=60, anchor="center")
        self._tree.column("name", width=200, minwidth=150)
        self._tree.column("version", width=80, anchor="center")
        self._tree.column("port", width=70, anchor="center")
        self._tree.column("uptime", width=80, anchor="center")
        self._tree.column("actions", width=80, anchor="center")

        scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("running", foreground=STATUS_COLORS["running"])
        self._tree.tag_configure("stopped", foreground=STATUS_COLORS["stopped"])
        self._tree.tag_configure("unknown", foreground=STATUS_COLORS["unknown"])

        self._tree.bind("<ButtonRelease-1>", self._on_click)

    def _refresh(self):
        api_client.fetch_services(self._on_services)
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(5000, self._refresh)

    def _on_services(self, services: list[dict]):
        self._services = services

        for item in self._tree.get_children():
            self._tree.delete(item)

        # Group services
        groups: dict[str, list[dict]] = {}
        for svc in services:
            grp = svc.get("group", "other")
            groups.setdefault(grp, []).append(svc)

        # Render in display order
        total_running = 0
        for grp in GROUP_ORDER:
            items = groups.pop(grp, None)
            if not items:
                continue
            label = GROUP_LABELS.get(grp, grp)
            parent = self._tree.insert("", tk.END, text=label, open=True,
                                       tags=("group",))
            for svc in items:
                status = svc.get("status", "unknown")
                if status == "running":
                    total_running += 1

                status_dot = self._status_dot(status)
                port = svc.get("port") or ""
                uptime = self._format_uptime(svc.get("uptime_seconds"))
                actions = self._action_label(svc)
                self._tree.insert(
                    parent, tk.END,
                    text="",  # group column handled by parent
                    values=(status_dot, svc.get("display_name", svc["id"]),
                            svc.get("min_version", ""), port, uptime, actions),
                    tags=(status, svc["id"]),
                )

        # Remaining uncategorized groups
        for grp, items in groups.items():
            parent = self._tree.insert("", tk.END, text=grp, open=True, tags=("group",))
            for svc in items:
                status = svc.get("status", "unknown")
                status_dot = self._status_dot(status)
                port = svc.get("port") or ""
                uptime = self._format_uptime(svc.get("uptime_seconds"))
                actions = self._action_label(svc)
                self._tree.insert(
                    parent, tk.END,
                    text="",
                    values=(status_dot, svc.get("display_name", svc["id"]),
                            svc.get("min_version", ""), port, uptime, actions),
                    tags=(status, svc["id"]),
                )

        self._status_label.config(
            text=f"{total_running}/{len(services)} running"
        )

    @staticmethod
    def _status_dot(status: str) -> str:
        """Return a coloured status indicator."""
        colors = {"running": "●", "starting": "◐", "stopped": "○", "error": "●"}
        return colors.get(status, "○")

    @staticmethod
    def _format_uptime(seconds: int | None) -> str:
        if not seconds:
            return ""
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"

    @staticmethod
    def _action_label(svc: dict) -> str:
        if not svc.get("can_start"):
            return ""
        status = svc.get("status", "unknown")
        return "Stop" if status == "running" else "Start"

    def _on_click(self, event):
        region = self._tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self._tree.identify_column(event.x)
        if int(col.replace("#", "")) != 6:  # actions column
            return
        item = self._tree.identify_row(event.y)
        if not item:
            return
        values = self._tree.item(item, "values")
        if not values or len(values) < 6:
            return
        action = values[5]
        if action not in ("Start", "Stop"):
            return

        tags = self._tree.item(item, "tags")
        svc_id = tags[1] if len(tags) > 1 else None
        if not svc_id:
            return

        if action == "Start":
            api_client.start_service(svc_id, on_done=self._refresh)
        else:
            api_client.stop_service(svc_id, on_done=self._refresh)

    def destroy(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        super().destroy()
