# Rasa — Procfile
# Start all services: honcho start
# Start a single service: honcho start <service>
#
# Startup order (staged launcher handles this):
#   1. PostgreSQL + Redis + Ollama (external, must be running)
#   2. api (:8400) — REST API for dashboard + orchestrator
#   3. pool-controller — task dispatch via PG NOTIFY
#   4. gui-nice (:8401) + daemon agents
#   5. heartbeat — self-healing meta-service

# === Layer 1: API ===
api: .venv\Scripts\python -m rasa.gui.server

# === Layer 2: Pool Controller (Python) ===
pool-controller: .venv\Scripts\python -m rasa.pool.controller --pool-file config/pool.yaml

# === Layer 3: Dashboard + Agents ===
gui-nice: .venv\Scripts\python -m rasa.gui_nice

# Daemon agents — poll for tasks assigned to them by the pool controller
agent-coder: .venv\Scripts\python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
agent-coder-2: .venv\Scripts\python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
agent-reviewer: .venv\Scripts\python -m rasa.agent.runtime --soul souls/reviewer-v1.yaml
agent-planner: .venv\Scripts\python -m rasa.agent.runtime --soul souls/planner-v1.yaml
agent-architect: .venv\Scripts\python -m rasa.agent.runtime --soul souls/architect-v1.yaml

# === Layer 4: Self-Healing ===
heartbeat: .venv\Scripts\python scripts/heartbeat_monitor.py --loop --interval 30

# === Observability ===
logs: .venv\Scripts\python scripts/observe.py --interval 60

# === Legacy (Go binaries — compiled, not actively maintained) ===
# pool-controller: .\pool-controller --config config/pool.yaml --redis localhost:6379 --http 127.0.0.1:8301
# policy-engine: .\policy-engine --redis localhost:6379 --soul-dir souls/
# recovery: .\recovery-controller --redis localhost:6379 --http 127.0.0.1:8302
# eval-aggregator: .\eval-aggregator --http 127.0.0.1:8303
# memory: .\memory-controller --redis localhost:6379 --http 127.0.0.1:8300
# gui: .venv\Scripts\python -m rasa.gui
