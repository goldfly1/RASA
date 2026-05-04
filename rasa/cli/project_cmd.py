"""Project management commands."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from rasa.orchestrator.project import ProjectManager


def run_project(command: str, name: str | None = None, goal: str | None = None) -> None:
    """Dispatch project subcommands."""
    mgr = ProjectManager()

    if command == "list":
        _list_projects(mgr)
    elif command == "create":
        if not name:
            print("Usage: rasa project create <name> [--goal <goal>]")
            return
        proj = mgr.create_project(name, goal or "")
        console = Console()
        console.print(f"[green]Created project:[/green] {proj['name']} ({proj['id']})")
        console.print(f"  Goal: {proj.get('goal', 'Not specified')}")
    else:
        print(f"Unknown project command: {command}")
        print("Available: list, create")


def _list_projects(mgr: ProjectManager) -> None:
    console = Console()
    projects = mgr.list_projects()
    if not projects:
        console.print("[dim]No projects found. Create one with: rasa project create <name>[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", width=30)
    table.add_column("ID", width=38)
    table.add_column("Status", width=10)
    table.add_column("Goal", width=30)

    for p in projects:
        goal = (p.get("goal") or "")[:28]
        table.add_row(p["name"], p["id"], p.get("status", ""), goal)

    console.print(table)
