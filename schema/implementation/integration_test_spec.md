# Integration Test Spec — End-to-End Task Flow

> **Status:** Draft  
> **Owner:** TBD  
> **Last Updated:** 2026-05-12

---

## 1. Purpose

Defines a single concrete end-to-end scenario that validates every layer of the RASA stack:
orchestrator → pool controller → agent runtime → LLM gateway → sandbox pipeline → policy engine.

This is the canonical integration checkpoint. If this test passes, the PoC is working.

---

## 2. Prerequisites

All commands assume a PowerShell terminal on Windows 11. WSL is **not** required for this
smoke test — everything runs natively on Windows.

### 2.1 Services That Must Be Running

| Service | How to Start | Health Check |
|---------|-------------|--------------|
| PostgreSQL 18 | Already running (Windows service) | `pg_isready -h localhost -p 5432` |
| Redis | `redis-server --port 6379` | `redis-cli PING` → `PONG` |
| Ollama desktop app | Start from tray / Start Menu | `curl http://127.0.0.1:11434/v1/models` |

### 2.2 Environment

```powershell
$env:RASA_DB_PASSWORD = "<your-password>"
$env:RASA_DB_HOST = "localhost"
$env:RASA_DB_PORT = "5432"
$env:RASA_DB_USER = "postgres"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
$env:OLLAMA_API_KEY = "ollama"
$env:RASA_MODEL = "deepseek-v4-pro:cloud"
```

### 2.3 Schema Bootstrap (one-time)

```powershell
# Apply all migrations
Get-ChildItem migrations/*.sql | Sort-Object Name | ForEach-Object {
    psql -h localhost -U postgres -f $_.FullName
}
```

Verify:
```sql
SELECT datname FROM pg_database WHERE datname LIKE 'rasa_%';
-- Expected: rasa_orch, rasa_pool, rasa_policy, rasa_memory, rasa_eval, rasa_recovery
```

---

## 3. Test Scenario

**Task:** _"Add a docstring to the `_pg_dsn` function in `rasa/agent/runtime.py`."_

- **Soul:** `coder-v2-dev`
- **Tier:** `standard`
- **Expected tools:** `file_read`, `file_write`
- **Expected outcome:** File modified, sandbox gates pass, task marked COMPLETED

---

## 4. Step-by-Step Walkthrough

### Step 1 — Submit the Task

```powershell
python -c "
from rasa.orchestrator.delegator import TaskDelegator
d = TaskDelegator()
task_id = d.create_task(
    soul_id='coder-v2-dev',
    title='Add docstring to _pg_dsn',
    description='Add a docstring to the _pg_dsn function in rasa/agent/runtime.py explaining its parameters and return value.',
)
print(f'Created task: {task_id}')
d.assign_task(task_id)
print(f'Assigned task: {task_id}')
"
```

**Expected DB state:**
```sql
SELECT id, status, soul_id, title FROM rasa_orch.tasks ORDER BY created_at DESC LIMIT 1;
-- status = 'ASSIGNED'
-- soul_id = 'coder-v2-dev'
```

**Expected NOTIFY:** Channel `tasks_assigned` fires with payload `{"task_id": "<uuid>", "soul_id": "coder-v2-dev"}`.

---

### Step 2 — Agent Picks Up the Task

Launch the agent in one-shot mode (simulates daemon polling):

```powershell
python -m rasa.agent.dispatcher --soul coder-v2-dev --task-id <TASK_ID_FROM_STEP_1> --one-shot
```

**What happens internally:**

1. `SoulLoader.load("coder-v2-dev")` — validates YAML against JSON Schema, resolves inheritance.
2. `GatewayClient.complete(...)` called with:
   - `system_prompt`: Rendered from `soul.prompt.system_template` via chevron
   - `user_prompt`: Task description
   - `tier`: `"standard"` (from `soul.model.default_tier`)
   - `temperature`: `0.2`, `max_tokens`: `8192`
   - `tools`: `[file_read, file_write, shell_exec, git_diff]` (from `soul.behavior.tool_policy.allowed_tools`)
3. LLM returns tool calls (e.g., `file_read` to see current code, then `file_write` to add docstring).
4. Each tool call is evaluated by `PolicyClient.evaluate(...)`:
   - Checks `auto_invoke` (currently `false` on coder-v2-dev → would block, but dispatcher skips this for one-shot)
   - Checks org guardrails from `rasa_policy.policy_rules`
   - Checks `denied_tools`: `file_write:/etc/*` does not match
   - Checks `require_human_confirm`: neither `file_read` nor `file_write` match
   - Returns `{"decision": "allow"}`
5. Tools execute, results fed back to LLM.
6. `save_checkpoint(...)` called after final result — writes to Redis (`checkpoint:{task_id}`), flat file, PostgreSQL.
7. `save_replay(...)` writes replay bundle to `data/replays/{task_id}/`.
8. Task status updated to `COMPLETED`.

**Expected DB state:**
```sql
SELECT id, status, result FROM rasa_orch.tasks WHERE id = '<TASK_ID>';
-- status = 'COMPLETED'
-- result IS NOT NULL
```

**Expected files:**
- `data/checkpoints/<TASK_ID>.json` exists
- `data/replays/<TASK_ID>/conversation.jsonl` exists
- `data/replays/<TASK_ID>/soul_sheet.yaml` exists

---

### Step 3 — Sandbox Pipeline (Background)

If the sandbox pipeline daemon is running, it picks up the task result via `sandbox_execute` NOTIFY:

```powershell
python -m rasa.sandbox --data-dir data/sandbox
```

**What happens internally:**

1. **CLONING** — Copies project to `data/sandbox/<TASK_ID>/`, applies agent's file changes.
2. **SCANNING** — `scan_directory(...)` runs:
   - Semgrep with `p/python` config (from `scanners/coder.yaml` overlay)
   - `detect-secrets` on sandbox directory
   - Returns `ScanResult` with findings
3. **BUILDING** — Checks for `go.mod`; none found → skips (Python-only).
4. **TESTING** — Checks for `pyproject.toml`; runs `pytest tests/ -v -x` in sandbox.
5. **PROMOTING** — Copies changed files from sandbox back to working directory.
6. **CLEANUP** — Deletes `data/sandbox/<TASK_ID>/`.

**Expected output:**
```
[sandbox] executing pipeline for task <TASK_ID[:8]> (soul=coder-v2-dev)
[sandbox] pipeline <TASK_ID[:8]> -> PASS (<N>ms)
```

**Expected NOTIFY:** Channel `sandbox_result` fires with `{"task_id": "<uuid>", "passed": true, ...}`.

---

### Step 4 — Verify the Change

```powershell
git diff rasa/agent/runtime.py
```

Should show the docstring addition to `_pg_dsn`.

---

## 5. Recovery Scenario (Bonus)

Simulate a crash mid-task to validate checkpoint recovery:

1. Submit a task and let the agent start executing.
2. Kill the agent process mid-execution (`Ctrl+C` or `taskkill`).
3. Check that task status is not COMPLETED.
4. Launch agent again with same `--task-id`.
5. Verify it loads the checkpoint and resumes:
   - Log line: `[agent-...] restored checkpoint for <TASK_ID>: turn=N, M messages`
6. Verify task completes successfully.

---

## 6. Wire-Level Reference

### 6.1 PostgreSQL NOTIFY Channels

| Channel | Publisher | Subscriber | Payload |
|---------|-----------|------------|---------|
| `tasks_assigned` | Orchestrator (`assign_task`) | Pool Controller | `{"task_id": "<uuid>", "soul_id": "<id>"}` |
| `task_completed` | Agent Runtime (`_write_result`) | Orchestrator, Eval Engine | `{"task_id": "<uuid>", "new_status": "COMPLETED"}` |
| `sandbox_execute` | Agent Runtime (post-task) | Sandbox Pipeline | `{"task_id": "<uuid>", ...}` |
| `sandbox_result` | Sandbox Pipeline (`_publish_result`) | Orchestrator, Eval Engine | `{"task_id": "<uuid>", "passed": bool, "gates": {...}}` |
| `souls_loaded` | Bootstrap (`_ingest_souls`) | Pool Controller | `{"soul_ids": [...], "count": N}` |

### 6.2 Redis Channels

| Channel | Publisher | Subscriber | Purpose |
|---------|-----------|------------|---------|
| `agents.heartbeat.{agent_id}` | Agent Runtime | Pool Controller | Heartbeat (every 5s) |
| `agents.control.{agent_id}` | Pool Controller / CLI | Agent Runtime | pause / resume / recover |

### 6.3 Task Status Transitions

```
PENDING → ASSIGNED → RUNNING → CHECKPOINTED ─┬─→ COMPLETED
                        ↓                      │
                       PAUSED ──→ RESUMING ────┘
                                              ↓
                      RECOVERING ──→ ACTIVE ──┘
                                              ↓
                                            FAILED
```

---

## 7. Success Criteria

| # | Check | Method |
|---|-------|--------|
| 1 | Task reaches COMPLETED status | `SELECT status FROM rasa_orch.tasks WHERE id = '<ID>'` |
| 2 | Task result is non-null JSON | `SELECT result IS NOT NULL FROM rasa_orch.tasks WHERE id = '<ID>'` |
| 3 | Checkpoint file exists | `Test-Path data/checkpoints/<ID>.json` |
| 4 | Replay bundle exists | `Test-Path data/replays/<ID>/conversation.jsonl` |
| 5 | Soul validated without errors | No `[soul]` error lines in agent output |
| 6 | LLM Gateway returned content | Agent output contains non-empty reply |
| 7 | Policy audit log written | `SELECT COUNT(*) FROM rasa_policy.audit_log WHERE agent_id = 'coder-v2-dev'` |
| 8 | No Semgrep/detect-secrets blockers | Sandbox SCANNING gate passes |
| 9 | File change applied to working dir | `git diff` shows expected change |
| 10 | Sandbox temp dir cleaned up | `Test-Path data/sandbox/<ID>` is `$false` |

---

## 8. Known Gaps This Test Exposes

1. **`auto_invoke: false`** on coder-v2-dev would block all tool calls in daemon mode. Either the soul
   sheet needs updating or the policy evaluation must handle one-shot mode differently.
2. **No sandbox daemon** is running by default — the test must either start it or accept that
   sandbox gating is skipped for one-shot dispatches.
3. **Replay bundle** requires `pyyaml` installed (`save_replay` uses `yaml.dump`).
4. **Ollama availability** — if the Ollama desktop app isn't running with the expected model,
   the LLM call will fail. The test should first verify: `curl http://127.0.0.1:11434/v1/models`.

---

*This document ties together the contracts defined in `orchestrator.md`, `agent_runtime.md`,
`sandbox_pipeline.md`, `policy_engine.md`, and `pilot_bootstrap.md` into a single verifiable flow.*
