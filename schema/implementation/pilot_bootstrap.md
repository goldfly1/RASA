# Pilot Bootstrap — Artifacts & Configuration

> **Status:** Implemented — bootstrap pipeline functional via `rasa/bootstrap/ingest.py`  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Single source for all deployable artifacts needed to run the Rasa pilot: soul sheet definitions, the Procfile, configuration files, scanner rule overlays, and the project directory structure. Every file referenced across the 14 implementation docs is declared here.

---

## 2. Directory Structure

```
rasa/
├── souls/                          # Soul sheet YAML files
│   ├── coder-v2-dev.yaml
│   ├── reviewer-v1.yaml
│   ├── planner-v1.yaml
│   └── architect-v1.yaml
├── config/                         # Runtime configuration
│   ├── gateway.yaml
│   └── pool.yaml
├── scanners/                       # Sandbox scanner rule overlays
│   ├── base-rules.yaml
│   ├── coder-overlay.yaml
│   ├── reviewer-overlay.yaml
│   ├── planner-overlay.yaml
│   └── architect-overlay.yaml
├── benchmarks/                     # Prompt regression benchmark tasks
│   └── (populated per Evaluation Engine)
├── data/
│   ├── archive/                    # Session checkpoints, conversation logs
│   ├── replays/                    # Replay bundles per task
│   └── sandbox/                    # Temp directories for build/test isolation
├── logs/
│   └── reports/                    # KPI rollup reports
├── scripts/
│   ├── observe.py                  # Log aggregator / KPI rollup
│   └── smoke_test.ps1              # End-to-end smoke test
├── .env.example                    # Environment variable template
├── Procfile                        # Process definitions
└── README.md
```

---

## 3. Soul Sheets

### 3.1 coder-v2-dev.yaml

```yaml
soul_version: "1.0.0"
soul_id: "coder-v2-dev"
agent_role: CODER
inherits: ~

metadata:
  name: "Senior Coder"
  description: "Agent responsible for feature implementation and refactoring"
  owner: "pilot"
  created_at: "2026-04-25T00:00:00Z"
  review_date: "2026-07-25"
  tags: ["backend", "python", "go", "typescript"]

model:
  default_tier: "standard"
  preferred_model: ""
  temperature: 0.2
  max_tokens: 8192
  top_p: 1.0
  frequency_penalty: 0.0
  presence_penalty: 0.0

prompt:
  system_template: |
    You are {{metadata.name}}, an {{agent_role}} agent in the Rasa system.

    You specialize in backend development using: {{#each metadata.tags}}{{this}}, {{/each}}.

    Operating principles:
    {{#each behavior.principles}}
    - {{this}}
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
    - "Never commit secrets or credentials to version control"
    - "Follow the existing codebase style and conventions"

  tool_policy:
    auto_invoke: false
    allowed_tools:
      - "file_read"
      - "file_write"
      - "shell_exec"
      - "git_diff"
    denied_tools:
      - "shell_exec:sudo"
      - "file_write:/etc/*"
      - "file_write:/usr/*"
    require_human_confirm:
      - "shell_exec:rm -rf"
      - "shell_exec:git push"

  session:
    mode: daemon
    max_idle_minutes: 10
    checkpoint_interval_seconds: 30
    heartbeat_interval_seconds: 5
    graceful_shutdown_seconds: 30

memory:
  short_term_window: 10
  long_term_retrieval_k: 5
  graph_traversal_depth: 2

cli:
  enabled: true
  argument_binding:
    --task-id: "task.id"
    --file: "task.context_files[]"
    --dry-run: "behavior.dry_run"
    --model-override: "model.preferred_model"
    --verbose: "behavior.verbose_logging"
    --one-shot: "behavior.session.mode"
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

extensions: {}
```

### 3.2 reviewer-v1.yaml

```yaml
soul_version: "1.0.0"
soul_id: "reviewer-v1"
agent_role: REVIEWER
inherits: ~

metadata:
  name: "Code Reviewer"
  description: "Agent responsible for reviewing code changes for quality, correctness, and security"
  owner: "pilot"
  created_at: "2026-04-25T00:00:00Z"
  review_date: "2026-07-25"
  tags: ["code-review", "quality", "security", "best-practices"]

model:
  default_tier: "standard"
  preferred_model: ""
  temperature: 0.1
  max_tokens: 4096
  top_p: 1.0
  frequency_penalty: 0.0
  presence_penalty: 0.0

prompt:
  system_template: |
    You are {{metadata.name}}, an {{agent_role}} agent in the Rasa system.

    You review code changes for correctness, security, style, and test coverage.

    Operating principles:
    {{#each behavior.principles}}
    - {{this}}
    {{/each}}

  context_injection: |
    Changes to review: {{task.title}}
    Type: {{task.type}}
    Diff context: {{memory.diff_summary}}

  tool_use_preamble: |
    You have read-only access to the codebase:
    {{#each tools.enabled}}
    - {{name}}: {{description}}
    {{/each}}

behavior:
  principles:
    - "Be constructive and specific in feedback"
    - "Check for edge cases and error handling"
    - "Verify test coverage matches changes"
    - "Flag security concerns immediately"

  tool_policy:
    auto_invoke: false
    allowed_tools:
      - "file_read"
      - "git_diff"
    denied_tools:
      - "file_write"
      - "shell_exec"
      - "git_push"
    require_human_confirm: []

  session:
    mode: daemon
    max_idle_minutes: 15
    checkpoint_interval_seconds: 60
    heartbeat_interval_seconds: 10
    graceful_shutdown_seconds: 30

memory:
  short_term_window: 5
  long_term_retrieval_k: 3
  graph_traversal_depth: 1

cli:
  enabled: true
  argument_binding:
    --task-id: "task.id"
    --diff: "task.diff"
    --one-shot: "behavior.session.mode"
  environment_injection:
    RASA_AGENT_ROLE: "agent_role"
    RASA_SOUL_ID: "soul_id"
  exit_codes:
    success: 0
    validation_failure: 1
    unknown_error: 255

extensions: {}
```

### 3.3 planner-v1.yaml

```yaml
soul_version: "1.0.0"
soul_id: "planner-v1"
agent_role: PLANNER
inherits: ~

metadata:
  name: "Technical Planner"
  description: "Agent responsible for decomposing work into tasks and designing implementation plans"
  owner: "pilot"
  created_at: "2026-04-25T00:00:00Z"
  review_date: "2026-07-25"
  tags: ["architecture", "planning", "design", "decomposition"]

model:
  default_tier: "premium"
  preferred_model: ""
  temperature: 0.3
  max_tokens: 16384
  top_p: 1.0
  frequency_penalty: 0.0
  presence_penalty: 0.0

prompt:
  system_template: |
    You are {{metadata.name}}, an {{agent_role}} agent in the Rasa system.

    You decompose high-level goals into concrete, verifiable tasks. You design
    implementation plans that consider dependencies, risk, and codebase structure.

    Operating principles:
    {{#each behavior.principles}}
    - {{this}}
    {{/each}}

  context_injection: |
    Goal: {{task.title}}
    Context: {{task.description}}
    Canonical model: {{memory.graph_excerpt}}

  tool_use_preamble: |
    You have read-only access to explore the codebase:
    {{#each tools.enabled}}
    - {{name}}: {{description}}
    {{/each}}

behavior:
  principles:
    - "Think before acting — understand the full context first"
    - "Define clear acceptance criteria for each sub-task"
    - "Document tradeoffs and rejected alternatives"
    - "Consider cross-module impact before proposing changes"

  tool_policy:
    auto_invoke: false
    allowed_tools:
      - "file_read"
      - "git_diff"
    denied_tools:
      - "file_write"
      - "shell_exec"
    require_human_confirm: []

  session:
    mode: daemon
    max_idle_minutes: 20
    checkpoint_interval_seconds: 60
    heartbeat_interval_seconds: 10
    graceful_shutdown_seconds: 30

memory:
  short_term_window: 15
  long_term_retrieval_k: 8
  graph_traversal_depth: 3

cli:
  enabled: true
  argument_binding:
    --task-id: "task.id"
    --goal: "task.title"
    --one-shot: "behavior.session.mode"
  environment_injection:
    RASA_AGENT_ROLE: "agent_role"
    RASA_SOUL_ID: "soul_id"
    RASA_BUDGET_TIER: "model.default_tier"
  exit_codes:
    success: 0
    validation_failure: 1
    budget_exhausted: 3
    unknown_error: 255

extensions: {}
```

### 3.4 architect-v1.yaml

```yaml
soul_version: "1.0.0"
soul_id: "architect-v1"
agent_role: ARCHITECT
inherits: ~

metadata:
  name: "System Architect"
  description: "Agent responsible for cross-module design decisions and structural changes"
  owner: "pilot"
  created_at: "2026-04-25T00:00:00Z"
  review_date: "2026-07-25"
  tags: ["architecture", "design", "cross-module", "interfaces"]

model:
  default_tier: "premium"
  preferred_model: ""
  temperature: 0.2
  max_tokens: 16384
  top_p: 1.0
  frequency_penalty: 0.0
  presence_penalty: 0.0

prompt:
  system_template: |
    You are {{metadata.name}}, an {{agent_role}} agent in the Rasa system.

    You make cross-module design decisions and define interfaces between
    components. You consider the system holistically.

    Operating principles:
    {{#each behavior.principles}}
    - {{this}}
    {{/each}}

  context_injection: |
    Design decision: {{task.title}}
    Scope: {{task.description}}
    Canonical model: {{memory.graph_excerpt}}
    Semantic matches: {{memory.semantic_matches}}

  tool_use_preamble: |
    You have access to explore the full codebase:
    {{#each tools.enabled}}
    - {{name}}: {{description}}
    {{/each}}

behavior:
  principles:
    - "Consider system-wide impact before proposing changes"
    - "Define interfaces before implementation details"
    - "Document architectural rationale for future reference"
    - "Prefer simple, composable designs over complex optimizations"

  tool_policy:
    auto_invoke: false
    allowed_tools:
      - "file_read"
      - "file_write"
      - "git_diff"
    denied_tools:
      - "shell_exec:sudo"
      - "file_write:/etc/*"
    require_human_confirm:
      - "shell_exec:rm -rf"

  session:
    mode: daemon
    max_idle_minutes: 20
    checkpoint_interval_seconds: 60
    heartbeat_interval_seconds: 10
    graceful_shutdown_seconds: 30

memory:
  short_term_window: 15
  long_term_retrieval_k: 10
  graph_traversal_depth: 3

cli:
  enabled: true
  argument_binding:
    --task-id: "task.id"
    --scope: "task.description"
    --one-shot: "behavior.session.mode"
  environment_injection:
    RASA_AGENT_ROLE: "agent_role"
    RASA_SOUL_ID: "soul_id"
    RASA_BUDGET_TIER: "model.default_tier"
  exit_codes:
    success: 0
    validation_failure: 1
    budget_exhausted: 3
    unknown_error: 255

extensions: {}
```

---

## 4. Procfile

```
# Rasa Pilot — Procfile
# Start all services: honcho start
# Start a single service: honcho start <service>

# === Infrastructure ===
redis: redis-server --port 6379

# === Control Plane (Go) ===
orchestrator: orchestrator --db postgres://localhost/rasa_orch
pool-controller: pool-controller --config config/pool.yaml --db postgres://localhost/rasa_pool
policy-engine: policy-engine --db postgres://localhost/rasa_policy
recovery: recovery-controller --db postgres://localhost/rasa_recovery
eval-aggregator: evaluation-engine --mode aggregator --db postgres://localhost/rasa_eval
memory: memory-controller --db postgres://localhost/rasa_memory

# === Agent Layer (Python) ===
llm-gateway: python -m rasa.llm_gateway --config config/gateway.yaml
sandbox: python -m rasa.sandbox --data-dir data/sandbox
eval-scorer: evaluation-engine --mode scorer --benchmarks benchmarks/

# === Agent Processes ===
agent-coder: python -m rasa.agent --soul souls/coder-v2-dev.yaml --mode daemon
agent-coder-2: python -m rasa.agent --soul souls/coder-v2-dev.yaml --mode daemon
agent-reviewer: python -m rasa.agent --soul souls/reviewer-v1.yaml --mode daemon
agent-planner: python -m rasa.agent --soul souls/planner-v1.yaml --mode daemon
agent-architect: python -m rasa.agent --soul souls/architect-v1.yaml --mode daemon

# === Observability ===
logs: python scripts/observe.py --watch logs/ --interval 60
```

---

## 5. Configuration Files

### 5.1 config/gateway.yaml

```yaml
# Rasa Pilot — LLM Gateway Configuration
endpoint: "http://localhost:11434/v1"

models:
  standard: "deepseek-v4-flash:cloud"
  premium: "kimi-k2.6:cloud"

cache:
  backend: "redis"
  ttl_seconds: 3600

tiers:
  standard:
    max_output_tokens: 16384
    timeout_seconds: 30
  premium:
    max_output_tokens: 32768
    timeout_seconds: 60

fallback:
  enabled: false                         # Set to true to enable OpenAI fallback
  endpoint: "https://api.openai.com/v1"
  model: "gpt-4o-mini"
  # api_key loaded from FALLBACK_API_KEY env var
```

### 5.3 config/pool.yaml

```yaml
# Rasa Pilot — Pool Controller Configuration
pool:
  souls:
    coder-v2-dev:
      count: 2
      max_concurrent: 2
    reviewer-v1:
      count: 1
      max_concurrent: 1
    planner-v1:
      count: 1
      max_concurrent: 1
    architect-v1:
      count: 1
      max_concurrent: 1
```

---

## 6. Scanner Rule Overlays

### 6.1 scanners/base-rules.yaml

```yaml
# Base scanner rules — applied to all agent roles
rules:
  - id: "no-secrets"
    patterns:
      - "api_key\s*=\s*['\"][A-Za-z0-9_]{16,}"
      - "password\s*=\s*['\"][^'\"]+['\"]"
      - "-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----"
    severity: BLOCKER

  - id: "no-dangerous-functions"
    patterns:
      - "eval("
      - "exec("
      - "subprocess.call.*shell=True"
    severity: ERROR

  - id: "no-hardcoded-paths"
    patterns:
      - "\"/etc/"
      - "\"/usr/"
      - "C:\\Windows"
    severity: WARNING
```

### 6.2 scanners/coder-overlay.yaml

```yaml
# Coder-specific: enforce type annotations and test patterns
rules:
  - id: "missing-type-hints"
    patterns:
      - "def [a-z].*:"  # Function def without return type
    severity: WARNING
  - id: "no-test-coverage"
    check: "test_coverage"
    severity: ERROR
```

### 6.3 scanners/reviewer-overlay.yaml
```yaml
# Reviewer-specific: diff-only, no build/test
skip_build: true
skip_test: true
```

### 6.4 scanners/planner-overlay.yaml
```yaml
# Planner-specific: documentation quality
rules:
  - id: "missing-docstring"
    patterns:
      - "def [a-z_]+.*:\\n\\s+pass"
    severity: WARNING
  - id: "todo-left"
    patterns:
      - "TODO"
      - "FIXME"
      - "HACK"
    severity: COMMENT
```

### 6.5 scanners/architect-overlay.yaml

```yaml
# Architect-specific: cross-module dependency scan
rules:
  - id: "circular-import"
    check: "circular_dependency"
    severity: BLOCKER
  - id: "interface-violation"
    check: "module_boundary"
    severity: ERROR
```

---

## 7. Environment Template

### 7.1 .env.example

```bash
# Rasa Pilot — Environment Configuration
# Copy to .env and fill in your values.
# Never commit .env to version control.

# PostgreSQL
RASA_DB_HOST=localhost
RASA_DB_PORT=5432
RASA_DB_USER=rasa
RASA_DB_PASSWORD=changeme
RASA_DB_NAME=rasa

# Redis
RASA_REDIS_HOST=localhost
RASA_REDIS_PORT=6379


# Ollama Cloud (desktop app handles auth — no key needed)
RASA_LLM_ENDPOINT=http://localhost:11434/v1

# Optional OpenAI fallback (only used if Ollama Cloud is unreachable)
# FALLBACK_API_KEY=sk-...
# FALLBACK_ENDPOINT=https://api.openai.com/v1

# OpenAI embeddings (required for Memory Subsystem)
OPENAI_API_KEY=sk-...
```

---

## 8. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Initial scaffold: 4 soul sheets, Procfile, 3 config files, 6 scanner overlays, directory structure, env template. | Codex |
