# Agent Configuration & Prompt Governance

> **Architectural Reference:** `architectural_schema_v2.1.md` §2.1 (Agent Taxonomy), §3.2 (Agent Session Lifecycle)
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report
> **Owner:** TBD
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Defines the schema, lifecycle, and governance model for **soul sheets** — the declarative configuration files that bind an agent's identity, prompt templates, tool bindings, and behavioral parameters to its runtime process. This document also specifies how CLI invocation parameters override or augment soul-sheet defaults, and how prompt versioning is tracked across deployments.

A soul sheet is the single artifact that answers: *Who is this agent? What is it allowed to do? What does it believe about itself?*

---

## 2. Soul Sheet Schema

### 2.1 File Format

Soul sheets are **YAML 1.2** documents with a mandatory header block and optional extension blocks. They are stored in the `<project_root>/souls/` directory and loaded directly by the Agent Runtime at session start.

> **Upgrade path:** Migrate soul sheet storage to PostgreSQL (runtime cache) + flat files (blobs > 1MB) when the system runs more than a handful of agents. Git remains the source of truth at every stage.

### 2.2 Core Schema

```yaml
soul_version: "1.0.0"           # Schema version of this soul sheet
soul_id: "coder-v2-dev"        # Unique identifier; used in logs, metrics, and pool allocation
agent_role: CODER               # One of: PLANNER, CODER, REVIEWER, ARCHITECT, CUSTOM
inherits: "base-coder"          # Optional: parent soul sheet to merge with

metadata:
  name: "Senior Coder"
  description: "Agent responsible for feature implementation and refactoring"
  owner: "platform-team"
  created_at: "2026-04-25T00:00:00Z"
  review_date: "2026-07-25"
  tags: ["backend", "python", "go"]

model:
  default_tier: "standard"      # Maps to LLM Gateway tier (standard → deepseek-v4-flash, premium → deepseek-v4-pro)
  preferred_model: ""            # Reserved for on-the-fly model selection (upgrade). Ignored in pilot — Gateway uses hard-coded tier mapping.
  temperature: 0.2
  max_tokens: 8192
  top_p: 1.0
  frequency_penalty: 0.0
  presence_penalty: 0.0

prompt:
  system_template: |
    You are {{metadata.name}}, an {{agent_role}} agent in the Rasa system.
    You specialize in: {{#each metadata.tags}}{{.}}, {{/each}}.

    Operating principles:
    {{#each behavior.principles}}
    - {{.}}
    {{/each}}

  context_injection: |
    Current task: {{task.title}}
    Task type: {{task.type}}
    Codebase context: {{memory.short_term_summary}}
    Canonical model excerpt: {{memory.graph_excerpt}}

  tool_use_preamble: |
    You have access to the following tools. Use them only when explicitly required:
    {{#each tools.enabled}}
    - {{name}}: {{description}}
    {{/each}}

behavior:
  principles:
    - "Write tests before implementation when feasible"
    - "Prefer explicit over implicit"
    - "Never commit secrets to version control"

  tool_policy:
    auto_invoke: false            # If true, agent may call tools without Orchestrator gating
    allowed_tools:
      - "file_read"
      - "file_write"
      - "shell_exec"
      - "git_diff"
    denied_tools:
      - "shell_exec:sudo"
      - "file_write:/etc/*"
    require_human_confirm:
      - "shell_exec:rm -rf"

  session:
    mode: daemon                  # Default session mode; overridden by --mode or --one-shot
    max_idle_minutes: 10
    checkpoint_interval_seconds: 30
    heartbeat_interval_seconds: 5
    graceful_shutdown_seconds: 30

memory:
  short_term_window: 10           # Number of recent turns to keep in working context
  long_term_retrieval_k: 5        # Top-k semantic matches from vector store
  graph_traversal_depth: 2        # How many hops in the canonical model (JSONB + recursive CTE)

cli:
  enabled: true                   # Whether this soul sheet supports CLI invocation
  argument_binding:
    --task-id: "task.id"
    --file: "task.context_files[]"
    --dry-run: "behavior.dry_run"
    --model-override: "model.preferred_model"
    --verbose: "behavior.verbose_logging"
    --one-shot: "behavior.session.mode"     # Shorthand: sets mode to "one-shot"
  environment_injection:
    RASA_AGENT_ROLE: "agent_role"
    RASA_SOUL_ID: "soul_id"
    RASA_BUDGET_TIER: "model.default_tier"
  exit_codes:
    success: 0
    validation_failure: 1
    sandbox_rejection: 2
    budget_exhausted: 3
    checkpoint_failure: 4
    unknown_error: 255

extensions:
  # Plugin-specific blocks; validated against extension schemas
  custom_policy_rules: []
```

### 2.3 Schema Validation

- **Required fields:** `soul_version`, `soul_id`, `agent_role`, `model.default_tier`, `prompt.system_template`
- **Inheritance resolution:** Merges parent → child; child values override. Arrays are replaced, not appended.
- **Validation engine:** JSON Schema (draft 2020-12) enforced at load time by the Agent Runtime.
- **Failure mode:** Invalid soul sheet → agent fails to start; error emitted to Observability Stack with `SOUL_VALIDATION_FAILED` event.

---

## 3. Prompt Templating System

### 3.1 Engine

**Mustache/Handlebars** (Python: `chevron`). Logic-light, deterministic — same inputs always produce the same SHA-256 hash, enabling LLM Gateway cache hits.

### 3.2 The 5-Layer Variable Resolution Stack

When the Agent Runtime assembles a prompt, it resolves variables from five sources (later layers override earlier ones):

| Layer | Source | Example Variable |
|-------|--------|------------------|
| 1. Soul defaults | YAML soul sheet | `metadata.name` |
| 2. Memory | Redis / pgvector / JSONB | `memory.short_term_summary` |
| 3. Task envelope | Orchestrator assignment | `task.title` |
| 4. CLI arguments | Command-line flags | `--model-override` |
| 5. Environment variables | `.env` file / OS | `RASA_BUDGET_TIER` |

### 3.3 Assembly Pipeline

1. Load soul sheet YAML from `<project_root>/souls/{soul_id}.yaml`.
2. Resolve inheritance (merge parent → child).
3. Query Memory Subsystem for context variables.
4. Overlay task envelope fields.
5. Overlay CLI arguments.
6. Overlay environment variables.
7. Render final string via chevron (Python Mustache/Handlebars).
8. Compute SHA-256 hash for LLM Gateway cache lookup.

### 3.4 Caching Strategy

After assembly, the Agent Runtime computes:

```
hash = SHA-256(final_prompt_string + model_id + temperature + max_tokens)
```

This hash is sent to the LLM Gateway in the `ModelRequest` envelope for cache lookup.

---

## 4. CLI Invocation Model

### 4.1 Session Modes

| Mode | Use Case |
|------|----------|
| **One-shot** (`--one-shot`) | CI/CD pipelines, batch jobs, pre-commit hooks. Process one task, exit. |
| **Interactive** (`--mode interactive`) | Local debugging, prompt engineering, REPL-like exploration. |
| **Daemon** (default) | Production warm pool. Register with Pool Controller, emit heartbeats, handle multiple tasks. |

### 4.2 Mode Selection Priority

1. `--one-shot` (shorthand)
2. `--mode one-shot|interactive|daemon` (explicit flag)
3. `RASA_AGENT_MODE` (environment variable)
4. Soul-sheet default: `behavior.session.mode: daemon`

### 4.3 One-Shot Behavior

When `--one-shot` is specified, the agent:
- Loads the soul configuration.
- Processes exactly one task.
- Exits immediately after completion.
- Does **not** register with the Pool Controller.
- Does **not** emit heartbeats.
- Does **not** checkpoint to Redis/PostgreSQL (unless `--checkpoint` is also passed).
- Policy Engine is still enforced.
- Budget tracking is still enforced (exits with code 3 if exhausted).

### 4.4 Flag Binding Example

```bash
rasa-agent --soul souls/coder-v2-dev.yaml \
           --one-shot \
           --task-id "0195f..." \
           --file src/auth.py \
           --dry-run \
           --verbose
```

---

## 5. Versioning & Governance

### 5.1 Soul Sheet Versioning

- **Semantic versioning** (`major.minor.patch`) for the soul sheet itself.
- **Schema version** (`soul_version`) tracks YAML schema compatibility; runtime rejects incompatible schemas.
- **Git tracking:** Soul sheets live in version control. Changes trigger the Evaluation Engine benchmark suite before promotion.

### 5.2 Prompt Versioning

- Prompt templates are content-addressed (SHA-256) after variable substitution.
- The `ModelRequest` envelope includes `prompt_version_hash` for traceability.
- The Evaluation Engine correlates regression scores with prompt hashes.

### 5.3 Promotion Flow

```
Soul sheet change in Git
        |
        v
  Evaluation Engine benchmark
        |
    +---------+
    | Pass?   |
    +---------+
        |
   Yes ----> No
   |          |
   v          v
 Promote    Block & alert
```

### 5.4 Audit Requirements

- Every agent session logs the resolved `soul_id`, `prompt_version_hash`, and CLI overrides.
- Policy Engine (§6.2) enforces that `denied_tools` and `require_human_confirm` rules are not relaxed without dual approval.

---

## 6. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Template engine** | **Mustache/Handlebars** (Python: `chevron`) | Portable, deterministic, logic-light. Prompt assembly happens in the Python Agent Runtime. |
| **Schema validation** | **JSON Schema** (draft 2020-12) + **go-playground/validator** | Standard, well-understood, generates clear error messages. |
| **Soul sheet storage** | **Git** (source of truth) + **local `souls/` directory** (runtime) + **flat files** (blobs > 1MB) | Git provides history and review; local directory provides fast load; flat files replace S3 for pilot. |
| **Hot reload** | **Filesystem watcher** (`fsnotify` in Go, `watchdog` in Python) | Agent Runtime watches `<project_root>/souls/` for changes; drains current task, reloads, resumes. `souls.update` topic is the documented upgrade path. |

---

## 7. Deployment Topology

- **Soul sheet location:** `<project_root>/souls/{soul_id}.yaml` — flat files on disk, loaded at session start.
- **Runtime load path:** Agent Runtime reads YAML directly from the local filesystem. No container image build needed for soul changes.
- **Orchestrator integration:** Orchestrator specifies `soul_id` in the `Task` envelope. Pool Controller matches it to agents with that soul pre-loaded.
- **Update flow:** Edit YAML → filesystem watcher triggers reload → Agent Runtime drains current task, re-reads soul, resumes.
- **Git sync:** Soul sheets are version-controlled in the project repo. Changes are pulled manually (`git pull`) or via a lightweight CI trigger. No container rebuild required.

---

## 8. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Soul sheet validation failures | Logged at session start | > 0 in any window — check YAML syntax |
| Prompt assembly latency (p99) | Timed per assembly | > 50ms — review template complexity |
| Soul version mismatch (Orchestrator vs. Runtime) | Checked at task assignment | > 0 — resync souls/ directory |
| LLM Gateway cache hit rate | Logged per request | < 30% — review TTL or prompt diversity |
| Embedded blob size > 1MB | Logged at load time | Flag for offload to flat file |

---

## 9. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should soul sheets support conditional logic (e.g., different prompts for different repo languages)? | **Open:** Out of scope for pilot. Template complexity increases Evaluation Engine burden. |
| 2 | How do we handle prompt injection from untrusted CLI arguments? | **Open:** Policy Engine rules and input sanitization. Needs dedicated design pass. |
| 3 | Should there be a visual / IDE soul-sheet editor, or is YAML sufficient? | **Open:** YAML is sufficient for pilot. Editor could improve DX at scale. |
| 4 | What is the maximum size of an embedded prompt example (before offload to flat files)? | **Resolved:** 1 MB inline in YAML. Larger examples → pointer file in `<project_root>/souls/blobs/`. |

---

## 10. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: replaced container-image deployment with local `souls/` directory, replaced hot-reload with filesystem watcher, replaced S3 blob storage with flat files, updated model tier comments for hard-coded Gateway mapping. | Codex |
| 2026-04-25 | Initial draft: soul sheet schema, CLI model, prompt pipeline, governance | ? |
| 2026-04-25 | Added `--one-shot` shorthand to `cli.argument_binding`; clarified one-shot behavior and mode selection priority | ? |

---

*This document implements the agent identity and prompting contract defined in `architectural_schema_v2.1.md` §2.1 and §3.2. For prompt governance explicitly marked Out of Scope in architecture, this document provides an implementation boundary until architecture is revised.*

---

## 11. Further Reading

- [`docs/prompt-and-cli-guide.md`](../docs/prompt-and-cli-guide.md) — Detailed guide to prompt templating and CLI invocation for operators and integrators.
