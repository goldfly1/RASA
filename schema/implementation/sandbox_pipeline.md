# Sandbox Pipeline

> **Architectural Reference:** `architectural_schema_v2.1.md` §6.1  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — scanner chain role-based overlays; artifact promotion gated by agent role   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Receives agent output, runs it through a static scanner, secret/PII detector, and isolated build+test execution before applying changes to the working directory. Validates that the agent's actions comply with its soul sheet `behavior.tool_policy`.

---

## 2. Internal Design

### 2.1 Internal State Machine

`IDLE → CLONING → SCANNING → BUILDING → TESTING → PROMOTING → CLEANUP`

**Soul-aware transitions:**

| State | Trigger | Pilot Action |
|-------|---------|--------------|
| **CLONING** | Task output received from Agent Runtime | Copy project files to `<data_root>/sandbox/{task_id}/`. Apply agent's changes. Load `soul_id` from task envelope; resolve `behavior.tool_policy` from cached soul sheet. |
| **SCANNING** | Copy complete | Run Semgrep rules + detect-secrets. Additional custom rules per `agent_role` (stricter rules for ARCHITECT). |
| **BUILDING** | Scan passes | Build in temp directory with subprocess timeout (30s default). Resource limits not enforced in pilot (single-machine). |
| **TESTING** | Build succeeds | Execute test suite. Test coverage gate enforced for CODER; relaxed for PLANNER. Subprocess timeout: 60s. |
| **PROMOTING** | All gates pass | Copy changed files from sandbox back to working directory. `soul_id` and `prompt_version_hash` stamped into promotion record. |
| **CLEANUP** | Promotion complete or gate failure | Delete temp directory. Emit `SANDBOX_RESULT` event with `soul_id`, gate results, and `task_id` via PostgreSQL LISTEN/NOTIFY. |

### 2.2 Soul-Aware Scanner Rules

The scanner chain loads a base rule set plus optional role-specific overlays from `<project_root>/scanners/`:

| Role | Overlay | Rationale |
|------|---------|-----------|
| `CODER` | Strict type-check, test-coverage gate | Code must be production-ready |
| `REVIEWER` | Diff-only scanner, no build | Reviewers emit comments, not artifacts |
| `PLANNER` | Documentation linter, no execution | Planners produce design docs |
| `ARCHITECT` | Cross-module dependency scanner | Structural changes need broader validation |

### 2.3 Orphan Sandbox Reaping

Since the sandbox is a temp directory with a subprocess (not a VM), reaping is handled by the pipeline process itself:

- **BUILD timeout:** 30s. Kills process tree if exceeded.
- **TEST timeout:** 60s. Kills process tree if exceeded.
- **Total pipeline timeout:** `2 × max(30s, 60s)` = 120s. If the pipeline process itself is killed, the temp directory is orphaned.
- **Orphan cleanup:** A background asyncio task in the Pipeline process scans `<data_root>/sandbox/` for directories older than 30 minutes and deletes them. Emits `ORPHAN_SANDBOX_DESTROYED` alert if any are found.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Python 3.12+** | Semgrep, detect-secrets, and custom AST rules all have Python APIs. Single process for the entire pipeline. |
| **Static scanner** | **Semgrep** (Python) | Multi-language pattern matching. Rule files in `<project_root>/scanners/`. |
| **Secret detector** | **detect-secrets** (Python) | Baseline file for known secrets; scans for new ones. |
| **Build/test isolation** | **Temp directory** + `subprocess` with timeout | No OS-level virtualization. Copy project to temp dir, build, test, promote on success. |
| **File copy** | `shutil.copytree` (Python stdlib) | Full recursive copy for pilot. Symlinks/hardlinks as optimization upgrade. |
| **Promotion** | `shutil.copy2` per changed file | Copies modified files back to working directory. Preserves metadata. |

---

## 4. Deployment Topology

- **Process:** Python process triggered per task. Can run as a short-lived subprocess or a daemon listening on PostgreSQL `sandbox_execute` channel:
  ```
  sandbox: python -m rasa.sandbox  --data-dir data/sandbox
  ```
- **Sandbox root:** `<project_root>/data/sandbox/{task_id>/` — created per task, deleted after promotion or failure.
- **Scanner rules:** `<project_root>/scanners/` — YAML files loaded at pipeline start.
- **Dependencies:** Python 3.12+, Semgrep (`pip install semgrep`), detect-secrets (`pip install detect-secrets`).
- **Promotion target:** The project's working directory (same filesystem). Changed files are copied back on successful gate pass.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Sandbox copy time (CLONING) | Timed per task | > 5s — project too large for full copy; consider symlinks |
| SCANNING pass rate | Counted per task | < 95% — review rule strictness |
| BUILD timeout hits | Counted per task | > 0 — agent output is not buildable |
| TEST failure rate | Counted per task | > 10% — review test gate strictness |
| Orphan sandbox count | Scanned every 5 min | > 0 — pipeline process may have crashed |
| Sandbox disk usage | Tracked `<data_root>/sandbox/` | > 1 GB — orphans accumulating |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | What is the sandbox VM/container startup latency? | **Resolved (N/A):** Temp directory — ~10ms. No VM. |
| 2 | How are orphaned sandboxes reaped? | **Resolved:** Background asyncio task scans for stale temp dirs > 30 min. Process-level timeouts prevent most orphans. |
| 3 | Should scanner rule overlays be baked or fetched dynamically? | **Resolved:** Local YAML files in `<project_root>/scanners/` for pilot. Dynamic fetch is an upgrade. |
| 4 | Should the sandbox copy the full project or only changed files? | **Resolved:** Full recursive copy for pilot. Symlink/hardlink optimization is an upgrade. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: replaced gVisor/containerd sandbox with temp-directory subprocess jail, replaced K8s reaper with process timeout + cleanup goroutine, replaced container-image scanner rules with local YAML files, added copy-to-temp CLONING phase, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul-aware scanner rules, role-specific overlays, and orphan reaping tied to soul params | ? |

---

*This document implements the execution isolation contract defined in `architectural_schema_v2.1.md` §6.1. Scanner role overlays align with `agent_configuration.md` §2.2 behavior block.*
