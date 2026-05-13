# RASA Procfile
# Start all services: honcho start
# Start a single service: honcho start <service>
#
# Startup order:
#   1. PostgreSQL + Redis + Ollama (external, must be running)
#   2. Go control plane: memory-controller, pool-controller, recovery
#   3. API + Dashboard + Agents
#   4. Observability: heartbeat, eval, logs

# === Layer 1: Go Control Plane ===
memory-controller: .\memory-controller --redis localhost:6379 --http 127.0.0.1:8300
pool-controller: .\pool-controller --config config/pool.yaml --redis localhost:6379 --http 127.0.0.1:8301
recovery-controller: .\recovery-controller --redis localhost:6379 --http 127.0.0.1:8302
policy-engine: .\policy-engine --redis localhost:6379 --soul-dir souls/
eval-aggregator: .\eval-aggregator --http 127.0.0.1:8303

# === Layer 2: API ===
api: .venv\Scripts\python -m rasa.gui.server

# === Layer 3: Dashboard + Agents ===
gui-nice: .venv\Scripts\python -m rasa.gui_nice

# Daemon agents poll for tasks assigned by the pool controller
agent-coder: .venv\Scripts\python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
agent-coder-2: .venv\Scripts\python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
agent-reviewer: .venv\Scripts\python -m rasa.agent.runtime --soul souls/reviewer-v1.yaml
agent-planner: .venv\Scripts\python -m rasa.agent.runtime --soul souls/planner-v1.yaml
agent-architect: .venv\Scripts\python -m rasa.agent.runtime --soul souls/architect-v1.yaml

# === Layer 4: Observability ===
heartbeat: .venv\Scripts\python scripts/heartbeat_monitor.py --loop --interval 30
logs: .venv\Scripts\python scripts/observe.py --interval 60
