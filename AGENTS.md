# Repository Guidelines

## Project Structure & Module Organization

```
rasa/          Python package: agent runtime, LLM gateway, pool controller, DB, GUI
cmd/           Go service stubs for control-plane binaries (orchestrator, pool-controller, eval-aggregator, etc.)
config/        YAML configuration (gateway.yaml, pool.yaml)
souls/         Agent soul sheets in YAML: system_template, model, tool_policy per role
migrations/    Numbered PostgreSQL migrations (001-110)
scripts/       PowerShell helpers for Windows setup, schema bootstrap, lore ingestion
tests/         pytest suite: smoke, bus, LLM gateway
benchmarks/    Performance benchmarks; schema/: architecture docs; docs/: design notes
```

## Build, Test, and Development Commands

| Command | Purpose |
|----------|----------|
| `pip install -e ".[dev]"` | Install Python package with dev extras (ruff, mypy, pytest-cov) |
| `pytest tests/ -v` | Run full test suite (requires PostgreSQL + `RASA_DB_PASSWORD`) |
| `ruff check rasa/ tests/` | Lint Python source (line-length 120, py312 target) |
| `mypy rasa/` | Type-check under strict mode |
| `go build ./cmd/.../` | Compile all Go control-plane binaries |
| `powershell -File scripts/bootstrap_schema.ps1` | Apply all PostgreSQL migrations |

## Coding Style & Naming Conventions

- **Python**: ruff (line-length 120), mypy strict. Use `from __future__ import annotations` in all modules.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_CASE` for constants.
- **Go**: standard `gofmt` style; one package per `cmd/<service>/main.go`.
- **SQL migrations**: numbered sequentially (`NNN_description.sql`), idempotent where practical.
- Prefer `structlog` for structured logging; `pydantic` for model validation.

## Testing Guidelines

- Framework: `pytest` + `pytest-asyncio` (all tests async by default).
- Naming: `test_<module>.py` with `test_<behavior>` function names.
- No coverage target enforced yet; use `pytest --cov=rasa` optionally.

## Commit & Pull Request Guidelines

- **Commit style**: imperative mood, capitalized first word, no trailing period.
  - Good: `Add capability_query tool for dynamic agent discovery`
  - Good: `Fix orchestrator 500 errors with LLM retry logic`
- **PRs**: Link to the issue if one exists. Note new env vars or schema migrations.

## Key Architecture Patterns

- **DB as bus**: `rasa_orch.tasks` is the durable job queue. `LISTEN/NOTIFY` for push, `SELECT FOR UPDATE SKIP LOCKED` for agent polling.
- **Agent lifecycle**: Pool controller spawns `powershell.exe -Command "python -m rasa.agent.dispatcher --soul <name> --task-id <uuid>"`.
- **Soul sheets**: Every agent role has a YAML soul in `souls/` with `system_template`, `context_injection`, `tool_policy`, `model`, and `behavior` blocks. Inheritance via `inherits` field (merge parent then child). All souls validated against JSON Schema on load.
- **Crash recovery**: `_poll_for_task()` scans RUNNING tasks with existing checkpoints and re-claims them on startup.
- **Replay bundles**: Every completed task writes a full replay to `data/replays/{task_id}/` (soul sheet, prompt, conversation, result, metadata).
- **Review sampling**: 1-in-20 task assignments trigger a pending human review in `rasa_policy.human_reviews`.
- **Capability matching**: `CapabilityRegistry.score_match()` ranks agents by keyword overlap; tasks without an explicit soul get auto-assigned the best match.
- **Canonical reconciler**: Pool controller background task (every 6h) syncs soul sheets into `agent_capabilities` and prunes stale entries.
- **Sandbox pipeline**: CLONE,SCAN,BUILD,TEST gates with per-framework reporting; graceful skip on network/DB failures.
- **Environment**: `RASA_DB_PASSWORD` must be set. See `.env.example` for all required variables.