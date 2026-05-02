"""Tool definitions for the orchestrator agent (OpenAI function-calling format)."""

ORCHESTRATOR_TOOL_DEFS = {
    "task_create": {
        "type": "function",
        "function": {
            "name": "task_create",
            "description": "Create a new task in the work queue for a specialist agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "soul_id": {
                        "type": "string",
                        "enum": ["planner-v1", "architect-v1", "coder-v2-dev", "reviewer-v1"],
                        "description": "Which agent type should handle this task",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short task title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed task description with context for the agent",
                    },
                },
                "required": ["soul_id", "title", "description"],
            },
        },
    },
    "task_assign": {
        "type": "function",
        "function": {
            "name": "task_assign",
            "description": "Assign a PENDING task so an agent picks it up. Call this after task_create.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "UUID of the task to assign",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    "task_query": {
        "type": "function",
        "function": {
            "name": "task_query",
            "description": "Check the current status and result of a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "UUID of the task to query",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    "task_list": {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List all tasks for the current project with their status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    "project_status": {
        "type": "function",
        "function": {
            "name": "project_status",
            "description": "Get a summary of the current project goal, active tasks, and progress.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
}
