# Testing Strategy

> **Status:** Pilot state — Go tests pass (6/6 packages), Python tests pass (35/42), smoke test functional  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Defines how the Rasa system itself is tested — not how agents test code (that's the Sandbox Pipeline), but how we verify that the Orchestrator, Pool Controller, Agent Runtime, and every other component work correctly. This document is a **pilot placeholder** intended to be fleshed out as the system matures.

---

## 2. Testing Layers

### 2.1 Unit Tests (Component Level)

Every component should have unit tests for its core logic, with external dependencies mocked.

| Component | Language | Framework | What to Test |
|-----------|----------|-----------|-------------|
| Orchestrator | Go | `testing` + `testify/mock` | Task state machine transitions, capability matching, cycle detection, retry logic |
| Pool Controller | Go | `testing` + `testify/mock` | Agent registry, heartbeat timeout, soul distribution enforcement |
| Recovery Controller | Go | `testing` + `testify/mock` | Checkpoint replay, idempotency ledger, soul version mismatch |
| Policy Engine | Go | `testing` + `testify/mock` | Rule evaluation flow (all 4 layers), glob matching, audit log |
| Agent Runtime | Python | `pytest` + `unittest.mock` | Soul sheet loading, prompt assembly, state machine transitions, heartbeat emission |
| LLM Gateway | Python | `pytest` + `responses` (HTTP mock) | Cache hit/miss, tier routing, fallback chain, seed passthrough |
| Sandbox Pipeline | Python | `pytest` + `tempfile` | Temp-dir lifecycle, scanner chain, build/test timeouts, promotion/rollback |
| Memory Subsystem | Go / Python | `testing` + `pytest` | Embedding pipeline, context assembly, eviction, canonical model CRUD |
| Evaluation Engine | Go / Python | `testing` + `pytest` | EvaluationRecord creation, benchmark execution, drift detection math |
| Message Bus | Go | `testing` + Redis test server or PG test database | Envelope publishing/consuming, dead-letter behavior |

**Mock strategy:**
- **Redis/PG:** Use ephemeral Redis/PG instances for integration tests, or mock the client interface for pure unit tests.
- **PostgreSQL:** Use a test database (`rasa_test`) that is dropped and recreated before each test run, or use `goqu`/`testcontainers` for disposable instances.
- **LLM API:** Mock HTTP responses — never call a real model in unit tests.
- **Redis:** Use `miniredis` (Go) or `fakeredis` (Python) for in-process mocking.
- **Filesystem:** Use `tempfile.TemporaryDirectory` (Python) or `os.MkdirTemp` (Go) for soul sheet loading, sandbox, and archive tests.

### 2.2 Integration Tests (Component Pairs)

Test the contract between two components with real (or near-real) dependencies.

| Pair | What to Verify |
|------|---------------|
| Orchestrator ↔ Pool Controller | Task assignment via PostgreSQL LISTEN/NOTIFY, NACK handling, agent registry updates |
| Pool Controller ↔ Agent Runtime | Heartbeat reception, soul_id matching, agent timeout detection |
| Agent Runtime ↔ LLM Gateway | ModelRequest envelope, cache response, fallback triggering |
| Agent Runtime ↔ Policy Engine | Tool call allow/deny, require_human_confirm blocking |
| Agent Runtime ↔ Memory Subsystem | Context assembly, checkpoint save/restore |
| Sandbox Pipeline ↔ Policy Engine | Scanner rules loaded per soul_id, promotion gated by role |
| Orchestrator ↔ Evaluation Engine | Eval records published on task completion, drift alerts consumed |

### 2.3 End-to-End Smoke Test

A single script that validates the entire system works end-to-end. This is the **demand** test — the one to run before demoing the system.

```powershell
# smoke_test.ps1 — placeholder
# Prerequisites: All services running (Procfile started), target repo available

Write-Host "1. Submit a refactor task via CLI..."
# orchestrator submit --soul coder-v2-dev --task '{"title": "Add docstring to main.py", "type": "REFACTOR"}'

Write-Host "2. Wait for completion (poll every 5s, max 60s)..."
# Check logs for TASK_COMPLETE or TASK_ESCALATED

Write-Host "3. Verify changes in workspace..."
# git diff —show-changes

Write-Host "4. Verify eval record created..."
# Check PostgreSQL evaluation_records table for the task

Write-Host "5. Check no errors in logs..."
# Select-String logs/*.log -Pattern '"level":"error"' -SimpleMatch

Write-Host "Smoke test complete."
```

### 2.4 Human Review Sampling

The Evaluation Engine's `score` field (0–1) is populated by either an automated heuristic or human review. For the pilot:

- **Sampling rate:** 1 in 20 tasks are flagged for human review.
- **Review process:** The reviewer inspects the replay bundle at `<data_root>/replays/{task_id}/` and assigns a score (0 = unusable, 0.5 = needs rework, 1 = production-ready).
- **Automated heuristic (fallback):** Tasks not sampled for human review get a score based on gate results (1.0 if all gates pass, 0.0 if any gate fails).
- **Score drift:** If human scores diverge from automated scores by > 0.2, increase the sampling rate to 1 in 10 (see Evaluation Engine §2.3).

---

## 3. Test Infrastructure (Pilot)

| Concern | Pilot Approach |
|---------|---------------|
| **Test runner (Go)** | `go test ./...` — standard Go toolchain |
| **Test runner (Python)** | `pytest -v` — with `pytest-cov` for coverage |
| **Test database** | `rasa_test` — dropped/recreated per run |
| **Mock Redis/PG** | `redis-server --port 6380` (ephemeral) fired up by test harness |
| **CI/CD** | Manual for pilot (`go test ./... && python -m pytest` before demos) |
| **Coverage target** | None for pilot — establish baseline during development |
| **Smoke test cadence** | Before each demo or significant change |

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should components be tested in isolation (mocked Redis/PG) or against real dependencies? | **Recommend both:** unit tests with mocks for fast feedback, integration tests with real deps for contract verification. |
| 2 | What is the target code coverage for the pilot? | **Open:** Suggest no target for pilot — focus on critical paths (state machines, soul loading, policy evaluation). |
| 3 | How is human review sampling triggered? | **Open:** CLI prompt after task completion, or a web dashboard in the upgrade. Recommend CLI for pilot. |
| 4 | Should the smoke test be automated in CI, or manual? | **Open:** Manual for pilot. Automate when graduated rollout begins. |

---

## 5. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Initial draft: testing layers, mock strategy, smoke test outline, human review sampling. Placeholder for future development. | Codex |

