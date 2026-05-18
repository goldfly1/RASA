# Repository Guidelines

## Project Structure

- `rasa/` — Python source modules (agent runtime, bus, CLI, DB, eval, GUI, LLM gateway, memory, orchestrator, policy, pool, sandbox)
- `cmd/` — Go control-plane services (orchestrator, pool-controller, memory-controller, recovery-controller, eval-aggregator, policy-engine)
- `tests/` — pytest suites
- `souls/` — YAML soul sheets defining agent roles and prompt templates
- `docs/` — Architecture and subsystem documentation
- `scripts/` — Bootstrap, ingestion, and helper scripts
- `migrations/` — Numbered SQL migration files (`NNN_description.sql`)
- `data/` — Replays, sandbox artifacts, and runtime data

## Build, Test, and Development Commands

```powershell
# Full bootstrap (creates venv, installs deps, builds Go binaries)
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1

# Rebuild Go control-plane binaries only
.\scripts\build.ps1

# Lint and type-check before committing
ruff check rasa/ tests/          # line-length=120, target py312
mypy rasa/                       # strict mode

# Run tests (requires PostgreSQL + Redis)
.venv\Scripts\python.exe -m pytest tests/ -v

# Start all services (PostgreSQL + Redis must be running)
honcho start
```

## Coding Style & Naming Conventions

- Python: `snake_case` functions and variables; `PascalCase` classes. Use `from __future__ import annotations` in new files.
- Go: standard `gofmt`; one package per `cmd/<service>/main.go`.
- Templates: Soul sheets use **Mustache (chevron)**, not Jinja2.
- Logging: `print(..., flush=True)` is the convention; no centralized logger is wired.

## Testing Guidelines

- Framework: `pytest` + `pytest-asyncio` (async by default).
- Key files: `test_smoke.py` (e2e), `test_bus.py`, `test_llm_gateway.py`.
- Run the full suite before opening a pull request.

## Commit & Pull Request Guidelines

- Write descriptive commit messages summarizing the change.
- Run `ruff check` and `mypy` before submitting.
- Reference related issues in PR descriptions.
- Ensure tests pass and Go binaries build cleanly (`go build ./cmd/...`).

## Security & Configuration Tips

- Copy `.env.example` to `.env` and fill in secrets. `.env` is gitignored.
- Default DB password is `8764`; six PostgreSQL databases are required (`rasa_orch`, `rasa_pool`, `rasa_policy`, `rasa_memory`, `rasa_eval`, `rasa_recovery`).
- Apply migrations with: `powershell -File scripts/bootstrap_schema.ps1`
