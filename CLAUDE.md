# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RASA (Reliable Autonomous System of Agents) is a multi-agent orchestration platform running on a single-node lab machine. **This Claude Code instance is the orchestrator** — it receives requests (via terminal or GUI relay), works directly with real tools (file I/O, git, shell, DB), and delegates to specialist agents as needed. Task records in PostgreSQL provide an audit trail.

- **Repo**: https://github.com/goldfly1/rasa
- **Hardware**: Intel Ultra 7 255, 64GB RAM, RTX 5060 8GB, 1TB SSD (~250GB free)
- **Stack**: Go 1.24+ (control plane stubs), Python 3.12+ (agent runtime, pool controller, GUI server), PostgreSQL 16+ (6 databases), Redis, Ollama
- **Current phase**: Phase 1 — pilot scaffolded; relay bridge between GUI and Claude Code operational

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Claude Code (orchestrator) — this session           │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Real tools: Read, Edit, Write, Bash, Agent,    │ │
│  │  Grep, Glob, WebSearch, WebFetch, TaskCreate    │ │
│  └─────────────────────────────────────────────────┘ │
│         │                          ▲                  │
│         ▼                          │                  │
│  ┌──────────┐    ┌──────────────────┴──────┐         │
│  │  Tkinter │───►│  .orch_relay/ (files)    │         │
│  │  GUI     │◄───│  inbox/  →  outbox/      │         │
│  └──────────┘    └──────────┬───────────────┘         │
│                             │                          │
│         ┌───────────────────┴────────────┐            │
│         │  PostgreSQL (rasa_orch)         │            │
│         │  ├─ tasks (audit trail)         │            │
│         │  ├─ projects                    │            │
│         │  ├─ agent_capabilities          │            │
│         │  └─ bus_messages (LISTEN/NOTIFY)│            │
│         └───────────────────┬────────────┘            │
│                             │                          │
│         ┌───────────────────┴────────────┐            │
│         │  Python agent subprocesses      │            │
│         │  (pool controller → dispatcher) │            │
│         └────────────────────────────────┘            │
└─────────────────────────────────────────────────────┘
```

- **Claude Code is the orchestrator**: Not a Python LLM-calling loop. Receives messages, works directly with real tools, writes results. The Python `OrchestratorRuntime` is deprecated for orchestrator duties.
- **File relay for GUI**: `.orch_relay/inbox/` and `.orch_relay/outbox/` bridge the Tkinter ChatPane to Claude Code. Server at `:8400` writes inbox, polls outbox. Claude Code reads inbox, processes, writes outbox.
- **PostgreSQL as audit trail**: `rasa_orch.tasks` records all delegated work. Not the message bus — the trace. LISTEN/NOTIFY still used for pool controller wake-up.
- **Task state machine**: `PENDING → ASSIGNED → RUNNING → CHECKPOINTED/COMPLETED/FAILED`.

## Commands

### Python (venv at `.venv\Scripts\python.exe`)

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
ruff check rasa/
mypy rasa/
pytest tests/ -v
pytest tests/ -v -k "test_name"

# GUI server (port 8400)
python -m rasa.gui.server

# Native Tkinter GUI
python -m rasa.gui_native.launch_gui_native

# Pool controller (spawns agent subprocesses from DB tasks)
python -m rasa.pool.controller --pool-file config/pool.yaml

# One-shot agent dispatch
python -m rasa.agent.dispatcher --soul coder-v2-dev --task-id <uuid> --one-shot

# LLM Gateway
python -m rasa.llm_gateway --config config/gateway.yaml

# Honcho (all services)
honcho start
honcho start <service>
```

### Database

```bash
psql -U postgres -d rasa_orch -c "SELECT status, COUNT(*) FROM tasks GROUP BY status;"
psql -U postgres -d rasa_orch -f migrations/010_rasa_orch.sql
set PGPASSWORD=8764  # then psql works without prompt
```

## Repository Layout

| Directory | Purpose |
|-----------|---------|
| `rasa/` | Python package — agent runtime, pool controller, DB layer, GUI server |
| `rasa/gui/` | Starlette web server (port 8400) — services, chat, orchestrator relay |
| `rasa/gui_native/` | Tkinter desktop GUI — services pane, project pane, chat pane |
| `rasa/orchestrator/` | Task delegator, project manager, capability registry |
| `rasa/bus/` | PostgreSQL LISTEN/NOTIFY + Redis Pub/Sub messaging |
| `rasa/pool/` | Pool controller — subscribes to task assignments, spawns workers |
| `rasa/agent/` | Agent runtime and dispatcher — soul sheet → LLM → tool execution |
| `cmd/*/main.go` | Go service stubs (orchestrator, pool-controller, etc.) |
| `internal/` | Go shared packages |
| `config/` | gateway.yaml, pool.yaml |
| `souls/` | Agent soul sheets (YAML): coder, reviewer, planner, architect, orchestrator |
| `migrations/` | PostgreSQL DDL for all databases |
| `scripts/` | PowerShell + Python helpers |
| `.orch_relay/` | File relay bridge between Tkinter GUI and Claude Code |
| `.hermes/` | (Deprecated) old orchestrator context files |

## Key Architecture Decisions

- **Claude Code = orchestrator**: The Python `OrchestratorRuntime` called an LLM with stub tools. This was replaced by Claude Code itself, which has real file I/O, git, shell, and agent delegation capabilities. The orchestration loop is this session.
- **File relay over HTTP polling**: The Tkinter ChatPane sends messages to the Starlette server (`:8400`), which writes them to `.orch_relay/inbox/` and polls `.orch_relay/outbox/`. Claude Code monitors the inbox (via `scripts/watch_orch_relay.py`), processes with real tools, and writes responses back. Simple, no WebSockets needed.
- **DB as audit trail, not message bus**: Tasks are written to `rasa_orch.tasks` for traceability. The pool controller uses LISTEN/NOTIFY to wake workers. Redis Pub/Sub only for loss-tolerant ephemeral messages (heartbeats).
- **Soul sheets**: YAML files defining agent personality and model routing. Rendered via `chevron` (Mustache). The orchestrator soul sheet (`souls/orchestrator-v1.yaml`) is informative — Claude Code doesn't use it as a runtime template.
- **6 PostgreSQL databases**: `rasa_orch`, `rasa_pool`, `rasa_policy`, `rasa_memory`, `rasa_eval`, `rasa_recovery`.
- **Capability Registry**: DB-backed `agent_capabilities` table. Agents register their capabilities, the orchestrator queries them. Migration `100_rasa_capabilities.sql`.

## Agent Delegation

When work requires a specialist agent, there are two paths:

### 1. Claude Code Agent tool (complex/creative work)

Use the `Agent` tool to spawn a specialist Claude Code sub-agent. This gives the sub-agent full file I/O, git, shell access — the same capabilities as the orchestrator.

```
Agent(description="Task summary", prompt="Detailed instructions", subagent_type="general-purpose")
```

Best for: code generation, debugging, research, file operations, anything requiring real judgment.

### 2. TaskDelegator + pool controller (automated Python agents)

Create a task record in `rasa_orch.tasks` for audit trail, then let the pool controller spawn a Python agent subprocess to execute it:

```python
from rasa.orchestrator.delegator import TaskDelegator
d = TaskDelegator()
tid = d.create_task(soul_id="coder-v2-dev", title="Fix DB migration", description="...")
d.assign_task(tid)  # marks ASSIGNED + PG NOTIFY → pool controller picks it up
```

Best for: well-defined automated tasks, batch processing, operations that don't need Claude Code's full toolset.

The pool controller (`rasa/pool/controller.py`) listens for `tasks_assigned` notifications and spawns `rasa.agent.dispatcher` subprocesses.

### DB task queries from Claude Code

```bash
# Direct psql
PGPASSWORD=8764 psql -U postgres -d rasa_orch -c "SELECT id, title, status, soul_id FROM tasks ORDER BY created_at DESC LIMIT 10;"

# Via Python API
python -c "from rasa.orchestrator.delegator import TaskDelegator; import json; d=TaskDelegator(); print(json.dumps(d.list_project_tasks(), indent=2))"
```

Task state machine: `PENDING → ASSIGNED → RUNNING → CHECKPOINTED/COMPLETED/FAILED`.

## Known Pitfalls

1. The legacy dispatcher's Handlebars→Jinja2 regex translation is lossy. New code should use `runtime.py` with chevron.
2. `GatewayClient.__init__` creates a new cache pool on every instantiation — known leak, deferred.
3. `.hermes/` and `AGENTS.md` are deprecated. Auto-memory in `.claude/projects/` replaces them.
4. The monitor script (`scripts/watch_orch_relay.py`) spams system notifications at 1s intervals, which makes the CLI look locked. Stop it with `TaskStop` when using the terminal directly.
5. DB password is `8764` — set `PGPASSWORD=8764` or `RASA_DB_PASSWORD=8764` for psql.
6. Go stubs in `cmd/` haven't been touched in a while — Python services do the real work now.
