"""Live dashboard — services, tasks, and pool status."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import psycopg
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from rasa.orchestrator.delegator import _dsn as _orch_dsn


def run_observe() -> None:
    """Run the live dashboard. Refreshes every 3 seconds."""
    console = Console()
    console.print("[bold]RASA Dashboard[/bold] — Ctrl+C to exit\n")

    layout = Layout()
    layout.split_column(
        Layout(name="services"),
        Layout(name="tasks"),
    )
    layout["tasks"].split_row(
        Layout(name="task_summary"),
        Layout(name="recent_tasks"),
    )

    try:
        with Live(layout, console=console, refresh_per_second=1, screen=True) as live:
            while True:
                _update_layout(layout)
                time.sleep(3)
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard closed.[/dim]")


def _update_layout(layout: Layout) -> None:
    dsn = _orch_dsn()

    # ── Services panel ──
    services_table = _services_table(dsn)
    layout["services"].update(Panel(services_table, title="Services"))

    # ── Task summary panel ──
    summary_table = _task_summary(dsn)
    layout["task_summary"].update(Panel(summary_table, title="Task Summary"))

    # ── Recent tasks panel ──
    recent_table = _recent_tasks(dsn)
    layout["recent_tasks"].update(Panel(recent_table, title="Recent Tasks"))


def _services_table(dsn: str) -> Table:
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Service", width=20)
    table.add_column("Status", width=12)
    table.add_column("Port", width=8)
    table.add_column("Uptime", width=14)

    services = [
        {"name": "PostgreSQL", "port": 5432},
        {"name": "Redis", "port": 6379},
        {"name": "Ollama", "port": 11434},
        {"name": "GUI Backend", "port": 8400},
    ]

    for svc in services:
        status, uptime = _check_service(dsn, svc["port"])
        color = "green" if status == "up" else "red"
        table.add_row(
            svc["name"],
            f"[{color}]{status}[/{color}]",
            str(svc["port"]),
            uptime or "—",
        )

    return table


def _check_service(dsn: str, port: int) -> tuple[str, str]:
    """Lightweight port check."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        if result == 0:
            return "up", ""
        return "down", ""
    except Exception:
        return "error", ""


def _task_summary(dsn: str) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=14)
    table.add_column("Count", width=8)

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status ORDER BY status")
                rows = cur.fetchall()
        total = sum(r[1] for r in rows)
        for status, count in rows:
            color = {
                "COMPLETED": "green", "FAILED": "red", "RUNNING": "yellow",
                "ASSIGNED": "blue", "PENDING": "dim",
            }.get(status, "white")
            table.add_row(f"[{color}]{status}[/{color}]", str(count))
        table.add_row("──", "──")
        table.add_row("[bold]TOTAL[/bold]", f"[bold]{total}[/bold]")
    except Exception as e:
        table.add_row("error", str(e)[:40])

    return table


def _recent_tasks(dsn: str) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", width=10)
    table.add_column("Title", width=24)
    table.add_column("Status", width=12)
    table.add_column("Soul", width=14)

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, title, status, soul_id FROM tasks
                       ORDER BY created_at DESC LIMIT 10"""
                )
                rows = cur.fetchall()

        for r in rows:
            tid = str(r[0])[:10]
            title = (r[1] or "")[:22]
            status = r[2] or ""
            soul = (r[3] or "")[:12]
            color = {
                "COMPLETED": "green", "FAILED": "red", "RUNNING": "yellow",
            }.get(status, "white")
            table.add_row(tid, title, f"[{color}]{status}[/{color}]", soul)

        if not rows:
            table.add_row("—", "no tasks", "", "")
    except Exception as e:
        table.add_row("err", str(e)[:40], "", "")

    return table
