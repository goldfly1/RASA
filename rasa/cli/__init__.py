"""RASA CLI — terminal-native interface for the orchestrator and agents."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rasa",
        description="RASA — Reliable Autonomous System of Agents",
    )
    sub = parser.add_subparsers(dest="command", title="commands")

    # rasa repl
    sub.add_parser("repl", help="Interactive orchestrator REPL (chat)")

    # rasa claude
    claude_p = sub.add_parser("claude", help="Launch Claude Code with RASA context")
    claude_p.add_argument("--project", "-p", dest="project_name", help="Project name to load context for")
    claude_p.add_argument("--goal", "-g", help="Goal/message to pass to Claude Code")
    claude_p.add_argument("--soul", "-s", default="orchestrator-v1", help="Soul sheet to use for system prompt")
    claude_p.add_argument("--file", "-f", action="append", dest="extra_files",
                          help="Extra context files to attach (repeatable)")

    # rasa task
    task_p = sub.add_parser("task", help="Task operations")
    task_sub = task_p.add_subparsers(dest="task_command")
    task_watch = task_sub.add_parser("watch", help="Watch tasks in real time")
    task_watch.add_argument("id", nargs="?", help="Task ID (omit for all active)")
    task_watch.add_argument("--project", "-p", dest="project_id", help="Filter by project ID")
    task_sub.add_parser("list", help="List recent tasks")
    task_query = task_sub.add_parser("query", help="Query a specific task")
    task_query.add_argument("id", help="Task ID")

    # rasa observe
    sub.add_parser("observe", help="Live dashboard (services, tasks, pool)")

    # rasa project
    proj_p = sub.add_parser("project", help="Project management")
    proj_sub = proj_p.add_subparsers(dest="project_command")
    proj_sub.add_parser("list", help="List projects")
    proj_create = proj_sub.add_parser("create", help="Create a new project")
    proj_create.add_argument("name", help="Project name")
    proj_create.add_argument("--goal", "-g", default="", help="Project goal")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "repl":
        from rasa.cli.repl import run_repl
        run_repl()

    elif args.command == "claude":
        from rasa.cli.claude import run_claude
        run_claude(
            project_name=args.project_name,
            goal=args.goal,
            soul=args.soul,
            extra_files=args.extra_files,
        )

    elif args.command == "task":
        from rasa.cli.task_watch import run_task
        run_task(
            command=args.task_command,
            task_id=getattr(args, "id", None),
            project_id=getattr(args, "project_id", None),
        )

    elif args.command == "observe":
        from rasa.cli.observe import run_observe
        run_observe()

    elif args.command == "project":
        from rasa.cli.project_cmd import run_project
        run_project(
            command=args.project_command,
            name=getattr(args, "name", None),
            goal=getattr(args, "goal", None),
        )


if __name__ == "__main__":
    main()
