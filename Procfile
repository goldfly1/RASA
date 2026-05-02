# Rasa Pilot — Procfile
# Start all services: honcho start
# Start a single service: honcho start <service>

# === Infrastructure ===
# redis: redis-server --port 6379  # Already running externally

# === Control Plane (Go) ===
pool-controller: .\pool-controller --config config/pool.yaml --redis localhost:6379 --http 127.0.0.1:8301
policy-engine: .\policy-engine --redis localhost:6379 --soul-dir souls/
recovery: .\recovery-controller --redis localhost:6379 --http 127.0.0.1:8302
eval-aggregator: .\eval-aggregator --http 127.0.0.1:8303
memory: .\memory-controller --redis localhost:6379 --http 127.0.0.1:8300

# === Agent Layer (Python) ===
sandbox: .venv\Scripts\python -m rasa.sandbox --data-dir data/sandbox

# === Agent Processes ===
agent-coder: .venv\Scripts\python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
agent-coder-2: .venv\Scripts\python -m rasa.agent.runtime --soul souls/coder-v2-dev.yaml
agent-reviewer: .venv\Scripts\python -m rasa.agent.runtime --soul souls/reviewer-v1.yaml
agent-planner: .venv\Scripts\python -m rasa.agent.runtime --soul souls/planner-v1.yaml
agent-architect: .venv\Scripts\python -m rasa.agent.runtime --soul souls/architect-v1.yaml

# === Observability ===
logs: .venv\Scripts\python scripts/observe.py --interval 60
