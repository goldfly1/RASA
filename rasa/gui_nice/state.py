"""Shared state across dashboard panels.

Plain Python attributes — data is refreshed via timers, not reactivity.
"""

from __future__ import annotations


class DashboardState:
    selected_project_id: str | None = None
    selected_project_name: str = "(none)"
    mode: str = "step_by_step"
    last_task_snapshot: list[dict] = []


state = DashboardState()
