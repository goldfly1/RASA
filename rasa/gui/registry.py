from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ServiceGroup(str, Enum):
    INFRASTRUCTURE = "infrastructure"
    CONTROL_PLANE = "control-plane"
    AGENTS = "agents"
    OBSERVABILITY = "observability"


class HealthType(str, Enum):
    HTTP_GET = "http_get"
    TCP_PORT = "tcp_port"
    PROCESS = "process"


@dataclass
class HealthCheck:
    type: HealthType
    host: str = "127.0.0.1"
    port: Optional[int] = None
    path: str = "/health"
    process_name: Optional[str] = None
    cmdline_match: Optional[str] = None


@dataclass
class ServiceDef:
    id: str
    display_name: str
    group: ServiceGroup
    health: HealthCheck
    start_command: list[str]
    min_version: str
    port: Optional[int] = None
    depends_on: list[str] = field(default_factory=list)
    can_start: bool = True
    is_external: bool = False


# Version constants
GO_VERSION = "1.24"
PYTHON_VERSION = "3.12"
PG_VERSION = "16"
REDIS_VERSION = "7.0"
OLLAMA_VERSION = "0.1"
RASA_VERSION = "0.1.0"


def build_registry() -> list[ServiceDef]:
    python_agent_cmd = [
        ".venv\\Scripts\\python", "-m", "rasa.agent.runtime", "--soul"
    ]
    return [
        # ── Infrastructure (external) ──
        ServiceDef(
            id="postgresql",
            display_name="PostgreSQL",
            group=ServiceGroup.INFRASTRUCTURE,
            health=HealthCheck(type=HealthType.TCP_PORT, port=5432),
            start_command=[],
            min_version=PG_VERSION,
            port=5432,
            can_start=False,
            is_external=True,
        ),
        ServiceDef(
            id="redis",
            display_name="Redis",
            group=ServiceGroup.INFRASTRUCTURE,
            health=HealthCheck(type=HealthType.TCP_PORT, port=6379),
            start_command=[],
            min_version=REDIS_VERSION,
            port=6379,
            can_start=False,
            is_external=True,
        ),
        ServiceDef(
            id="ollama",
            display_name="Ollama",
            group=ServiceGroup.INFRASTRUCTURE,
            health=HealthCheck(type=HealthType.TCP_PORT, port=11434),
            start_command=[],
            min_version=OLLAMA_VERSION,
            port=11434,
            can_start=False,
            is_external=True,
        ),
        # ── Control Plane (Go services) ──
        ServiceDef(
            id="memory",
            display_name="Memory Controller",
            group=ServiceGroup.CONTROL_PLANE,
            health=HealthCheck(type=HealthType.HTTP_GET, port=8300),
            start_command=[".\\memory-controller", "--redis", "localhost:6379", "--http", "127.0.0.1:8300"],
            min_version=GO_VERSION,
            port=8300,
            depends_on=["redis", "postgresql"],
        ),
        ServiceDef(
            id="pool-controller",
            display_name="Pool Controller",
            group=ServiceGroup.CONTROL_PLANE,
            health=HealthCheck(type=HealthType.HTTP_GET, port=8301),
            start_command=[".\\pool-controller", "--config", "config/pool.yaml", "--redis", "localhost:6379", "--http", "127.0.0.1:8301"],
            min_version=GO_VERSION,
            port=8301,
            depends_on=["redis", "postgresql"],
        ),
        ServiceDef(
            id="recovery",
            display_name="Recovery Controller",
            group=ServiceGroup.CONTROL_PLANE,
            health=HealthCheck(type=HealthType.HTTP_GET, port=8302),
            start_command=[".\\recovery-controller", "--redis", "localhost:6379", "--http", "127.0.0.1:8302"],
            min_version=GO_VERSION,
            port=8302,
            depends_on=["redis", "postgresql"],
        ),
        ServiceDef(
            id="eval-aggregator",
            display_name="Eval Aggregator",
            group=ServiceGroup.CONTROL_PLANE,
            health=HealthCheck(type=HealthType.HTTP_GET, port=8303),
            start_command=[".\\eval-aggregator", "--http", "127.0.0.1:8303"],
            min_version=GO_VERSION,
            port=8303,
            depends_on=["postgresql"],
        ),
        ServiceDef(
            id="policy-engine",
            display_name="Policy Engine",
            group=ServiceGroup.CONTROL_PLANE,
            health=HealthCheck(type=HealthType.PROCESS, process_name="policy-engine.exe"),
            start_command=[".\\policy-engine", "--redis", "localhost:6379", "--soul-dir", "souls/"],
            min_version=GO_VERSION,
            depends_on=["redis", "postgresql"],
        ),
        # ── Agents (Python) ──
        ServiceDef(
            id="sandbox",
            display_name="Sandbox",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="rasa.sandbox"),
            start_command=[".venv\\Scripts\\python", "-m", "rasa.sandbox", "--data-dir", "data/sandbox"],
            min_version=PYTHON_VERSION,
        ),
        ServiceDef(
            id="agent-coder",
            display_name="Agent Coder",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="coder-v2-dev"),
            start_command=[*python_agent_cmd, "souls/coder-v2-dev.yaml"],
            min_version=PYTHON_VERSION,
        ),
        ServiceDef(
            id="agent-coder-2",
            display_name="Agent Coder (2)",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="coder-v2-dev"),
            start_command=[*python_agent_cmd, "souls/coder-v2-dev.yaml"],
            min_version=PYTHON_VERSION,
        ),
        ServiceDef(
            id="agent-reviewer",
            display_name="Agent Reviewer",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="reviewer-v1"),
            start_command=[*python_agent_cmd, "souls/reviewer-v1.yaml"],
            min_version=PYTHON_VERSION,
        ),
        ServiceDef(
            id="agent-planner",
            display_name="Agent Planner",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="planner-v1"),
            start_command=[*python_agent_cmd, "souls/planner-v1.yaml"],
            min_version=PYTHON_VERSION,
        ),
        ServiceDef(
            id="agent-architect",
            display_name="Agent Architect",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="architect-v1"),
            start_command=[*python_agent_cmd, "souls/architect-v1.yaml"],
            min_version=PYTHON_VERSION,
        ),
        ServiceDef(
            id="agent-orchestrator",
            display_name="Agent Orchestrator",
            group=ServiceGroup.AGENTS,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="orchestrator-v1"),
            start_command=[*python_agent_cmd, "souls/orchestrator-v1.yaml"],
            min_version=PYTHON_VERSION,
        ),
        # ── Observability ──
        ServiceDef(
            id="logs",
            display_name="Log Observer",
            group=ServiceGroup.OBSERVABILITY,
            health=HealthCheck(type=HealthType.PROCESS, process_name="python.exe", cmdline_match="observe.py"),
            start_command=[".venv\\Scripts\\python", "scripts/observe.py", "--interval", "60"],
            min_version=PYTHON_VERSION,
        ),
    ]


def get_service_map() -> dict[str, ServiceDef]:
    return {svc.id: svc for svc in build_registry()}
