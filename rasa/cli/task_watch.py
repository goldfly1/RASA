"""Real-time task watching and querying."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from rasa.orchestrator.delegator import TaskDelegator
from rasa.orchestrator.project import ProjectManager

STATUS_COLORS = {
    "PENDING": "#8b949e",
    "ASSIGNED": "#58a6ff",
    "RUNNING": "#eab308",
    "CHECKPOINTED": "#d2a8ff",
    "COMPLETED": "#22c55e",
    "FAILED": "#ef4444",
    "CANCELLED": "#8b949e",
}


def run_task(command: str, task_id: str | None = None, project_id: str | None = None) -> None:
    """Dispatch task subcommands."""
    if command == "watch":
        _watch_tasks(task_id, project_id)
    elif command == "list":
        _list_tasks(project_id)
    elif command == "query":
        if not task_id:
            print("Usage: rasa task query <task_id>")
            return
        _query_task(task_id)
    else:
        print(f"Unknown task command: {command}")
        print("Available: watch, list, query")


def _watch_tasks(task_id: str | None = None, project_id: str | None = None) -> None:
    """Stream task status changes in real time using polling."""
    console = Console()
    delegator = TaskDelegator()

    console.print("[bold]RASA Task Watch[/bold]")
    if task_id:
        console.print(f"Watching task: {task_id}")
    elif project_id:
        console.print(f"Watching project: {project_id}")
    else:
        console.print("Watching all active tasks")

    console.print("Press Ctrl+C to exit\n")

    try:
        _poll_loop(console, delegator, task_id, project_id)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def _poll_loop(console: Console, delegator: TaskDelegator, task_id: str | None, project_id: str | None) -> None:
    seen_states: dict[str, str] = {}  # task_id -> last known status

    while True:
        tasks: list[dict] = []
        if task_id:
            t = delegator.query_task(task_id)
            if t:
                tasks = [t]
        elif project_id:
            tasks = delegator.list_project_tasks(project_id)
        else:
            tasks = delegator.list_project_tasks(None)

        table = _build_task_table(tasks)
        console.clear()
        console.print(table)
        console.print(f"\n[dim]Last refresh: {time.strftime('%H:%M:%S')} — Ctrl+C to stop[/dim]")

        time.sleep(2)


def _list_tasks(project_id: str | None = None) -> None:
    console = Console()
    delegator = TaskDelegator()
    tasks = delegator.list_project_tasks(project_id)
    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return
    table = _build_task_table(tasks)
    console.print(table)


def _query_task(task_id: str) -> None:
    console = Console()
    delegator = TaskDelegator()
    task = delegator.query_task(task_id)
    if not task:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        return

    console.print(f"[bold]Task: {task.get('title', task_id)}[/bold]")
    console.print(f"  ID:         {task['id']}")
    console.print(f"  Status:     [{STATUS_COLORS.get(task['status'], '#8b949e')}]{task['status']}[/]")
    console.print(f"  Soul:       {task.get('soul_id', '')}")
    console.print(f"  Created:    {task.get('created_at', '')}")
    console.print(f"  Started:    {task.get('started_at', '')}")
    console.print(f"  Completed:  {task.get('completed_at', '')}")
    if task.get("description"):
        console.print(f"\n  Description:\n  {task['description']}")
    if task.get("result"):
        console.print(f"\n  Result:\n  {task['result'][:1000]}")
    if task.get("error_message"):
        console.print(f"\n  [red]Error: {task['error_message']}[/red]")


def _build_task_table(tasks: list[dict]) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", width=14)
    table.add_column("Title", width=40)
    table.add_column("Status", width=14)
    table.add_column("Soul", width=20)
    table.add_column("Created", width=20)

    for t in tasks:
        tid = t["id"][:12]
        title = (t.get("title") or "")[:38]
        status = t.get("status", "PENDING")
        soul = (t.get("soul_id") or "")[:18]
        created = (t.get("created_at") or "")[:19].replace("T", " ")

        color = STATUS_COLORS.get(status, "#8b949e")
        status_text = Text(status, style=color)

        table.add_row(tid, title, status_text, soul, created)

    return table
