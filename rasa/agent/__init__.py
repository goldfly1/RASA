"""Agent runtime — Windows-side worker with state machine, heartbeats, LLM integration."""

# Lazy imports to avoid double-import when running as __main__

def __getattr__(name):
    if name == "run_task" or name == "daemon_loop" or name == "_load_soul":
        from rasa.agent.dispatcher import run_task, daemon_loop, _load_soul
        return globals()[name]
    if name == "AgentRuntime" or name == "AgentState" or name == "main":
        from rasa.agent.runtime import AgentRuntime, AgentState, main
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["run_task", "daemon_loop", "_load_soul", "AgentRuntime", "AgentState", "main"]
