# Repository Guidelines

## Project Structure & Module Organization

```
rasa/          Python package — agent runtime, LLM gateway, pool controller, DB, GUI
cmd/           Go service stubs for control-plane binaries (orchestrator, pool-controller, etc.)
config/        YAML configuration (gateway.yaml, pool.yaml, nats-server.conf)
souls/         Agent soul sheets in YAML — system_template, model, tool_policy per role
migrations/    Numbered PostgreSQL migrations (001–110) covering orch, pool, policy, memory, eval
scripts/       PowerShell helpers for Windows setup, schema bootstrap, and lore ingestion
tests/         pytest suite — smoke, bus, LLM gateway (test_*.py, run with pytest)
benchmarks/    Performance benchmarks; schema/ — architecture docs; docs/ — design notes
```

## Build, Test, and Development Commands

| Command | Purpose |
|---------|---------|
| `pip install -e ".[dev]"` | Install the Python package with dev extras (ruff, mypy, pytest-cov) |
| `pytest tests/ -v` | Run the full test suite (requires PostgreSQL and `RASA_DB_PASSWORD`) |
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
- Smoke test (`tests/test_smoke.py`) is the integration checkpoint—run it after schema changes.
- PowerShell smoke script (`tests/smoke_dispatcher.ps1`) tests the dispatcher from Windows side.
- No coverage target enforced yet; use `pytest --cov=rasa` optionally.

## Commit & Pull Request Guidelines

- **Commit style**: imperative mood, capitalized first word, no trailing period.
  - Good: `Add capability_query tool for dynamic agent discovery`
  - Good: `Fix orchestrator 500 errors with LLM retry logic`
- **Gates**: Phase 1 implementation followed numbered gates; reference the gate in the body if applicable.
- **PRs**: link to the issue if one exists. Describe what changed and why. Note any new env vars or schema migrations.

## Key Patterns

- **DB as bus**: PostgreSQL `rasa_orch.tasks` is the durable job queue. Use `LISTEN/NOTIFY` for push and `SELECT FOR UPDATE SKIP LOCKED` for agent polling.
- **Windows invocation**: agents run via `powershell.exe -Command "python -m rasa.agent.dispatcher --soul <name> --task-id <uuid>"`.
- **Soul sheets**: every agent role has a YAML soul in `souls/` with `system_template`, `context_injection`, `tool_policy`, and a `model` block.
- **Environment**: `RASA_DB_PASSWORD` must be set. See `.env.example` for all required variables.
