"""Launch Claude Code sessions with pre-loaded RASA project context.

Canonical pattern: ollama launch claude --model deepseek-v4-pro:cloud
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rasa.cli.model_config import launch_claude_code, ensure_ollama_running, PROJECT_ROOT
from rasa.orchestrator.delegator import TaskDelegator
from rasa.orchestrator.project import ProjectManager


def run_claude(
    project_name: str | None = None,
    goal: str | None = None,
    soul: str = "orchestrator-v1",
    extra_files: list[str] | None = None,
) -> None:
    """Generate context, ensure ollama is serving, launch Claude Code with model.

    ollama launch claude --model deepseek-v4-pro:cloud
    """
    if not ensure_ollama_running():
        print("Error: ollama is not running. Start it with: ollama serve")
        sys.exit(1)

    mgr = ProjectManager()
    delegator = TaskDelegator()

    # Resolve project
    project = None
    if project_name:
        projects = mgr.list_projects()
        for p in projects:
            if p["id"].startswith(project_name) or p.get("name", "").lower() == project_name.lower():
                project = mgr.get_project(p["id"])
                break
        if not project:
            print(f"Project '{project_name}' not found. Creating it.")
            project = mgr.create_project(project_name, goal or "")

    # Build context payload
    context = _build_context(mgr, delegator, project, goal or "")
    context_file = _write_context_file(context)

    print(f"RASA → ollama launch claude --model deepseek-v4-pro:cloud")
    if project:
        print(f"  Project: {project.get('name', 'unnamed')} ({project['id'][:12]})")
    print(f"  Context: {context_file}")
    print()

    rc = launch_claude_code(
        model="deepseek-v4-pro:cloud",
        prompt=goal,
        system_prompt_file=context_file,
    )
    sys.exit(rc)


def _build_context(
    mgr: ProjectManager,
    delegator: TaskDelegator,
    project: dict | None,
    goal: str,
) -> str:
    """Assemble the context block injected into Claude Code's system prompt."""
    parts: list[str] = []

    parts.append("## RASA Orchestrator Context")
    parts.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    parts.append("")

    if project:
        parts.append(f"### Active Project: {project.get('name', 'unnamed')}")
        parts.append(f"- Project ID: `{project['id']}`")
        parts.append(f"- Goal: {project.get('goal', 'Not specified')}")
        parts.append(f"- Status: {project.get('status', 'unknown')}")
        parts.append("")
        counts = project.get("task_counts", {})
        if counts:
            parts.append(f"Task summary: {json.dumps(counts)}")
            parts.append("")

        # Active tasks
        tasks = delegator.list_project_tasks(project["id"])
        if tasks:
            parts.append("### Current Tasks")
            for t in tasks:
                status = t["status"]
                title = t["title"]
                soul = t.get("soul_id", "")
                tid = t["id"][:12]
                parts.append(f"- [{status}] {title} (soul={soul}, id={tid})")
            parts.append("")

    if goal:
        parts.append(f"### Requested Goal\n{goal}")
        parts.append("")

    # Agent capabilities
    try:
        from rasa.orchestrator.capabilities import CapabilityRegistry
        cr = CapabilityRegistry()
        caps = cr.list_capabilities()
        if caps:
            parts.append("### Available Agents")
            for c in caps:
                soul_id = c.get("soul_id", "")
                role = c.get("agent_role", "")
                name = c.get("display_name", soul_id)
                parts.append(f"- {name} (`{soul_id}`) — {role}")
            parts.append("")
    except Exception:
        pass

    # Tool reference
    parts.append("### Orchestrator Tools")
    parts.append("You have access to: task_create, task_assign, task_query, task_list, "
                 "project_status, capability_query")
    parts.append("Plus file tools: file_read, file_write, shell_exec, git_diff")
    parts.append("")
    parts.append("Use task_create + task_assign to delegate work to specialist agents.")
    parts.append("The PostgreSQL database acts as the message bus — tasks are durable queues.")
    parts.append("")

    return "\n".join(parts)


def _write_context_file(context: str) -> str:
    hermes_dir = PROJECT_ROOT / ".hermes"
    hermes_dir.mkdir(exist_ok=True)
    ctx_path = hermes_dir / "claude_context.md"
    ctx_path.write_text(context, encoding="utf-8")
    return str(ctx_path)
