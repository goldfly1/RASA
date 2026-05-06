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
            "description": "Assign a PENDING task — the agent process is launched immediately. Call this after task_create.",
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
            "description": "Check task status. Tasks run asynchronously — do NOT poll this repeatedly. Query once per turn at most.",
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
    "capability_query": {
        "type": "function",
        "function": {
            "name": "capability_query",
            "description": "Query the capability registry to find which specialist agents can handle a specific type of work. Returns agents matching the requested category or role.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Capability category to filter by (e.g., planning, design, implementation, review, testing, analysis, documentation)",
                    },
                    "role": {
                        "type": "string",
                        "description": "Agent role to filter by (e.g., PLANNER, ARCHITECT, CODER, REVIEWER)",
                    },
                },
            },
        },
    },
    "request_human_input": {
        "type": "function",
        "function": {
            "name": "request_human_input",
            "description": (
                "Request guidance, approval, or answers from the human operator. "
                "Use this when you need clarification, are blocked by an ambiguous decision, "
                "or need permission to proceed. The human will see this request on the dashboard "
                "and respond. After calling this, use check_human_response on a future turn "
                "to see if the human has answered."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Clear explanation of why human input is needed. What question or decision is blocked?",
                    },
                    "payload": {
                        "type": "object",
                        "description": "Optional structured context: options, data, or any JSON info the human might need.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    "check_human_response": {
        "type": "function",
        "function": {
            "name": "check_human_response",
            "description": (
                "Check whether the human has responded to a previous request_human_input call. "
                "Call this on a subsequent tool turn AFTER calling request_human_input. "
                "If the human has responded, the result will include their guidance text. "
                "If the human has not responded yet, you will be told to wait and try again later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "review_id": {
                        "type": "string",
                        "description": "The review UUID returned by the most recent request_human_input call for this question.",
                    },
                },
                "required": ["review_id"],
            },
        },
    },
    "service_list": {
        "type": "function",
        "function": {
            "name": "service_list",
            "description": "List all RASA services and their current status (running/stopped/error). No arguments needed.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    "service_start": {
        "type": "function",
        "function": {
            "name": "service_start",
            "description": "Start a RASA service by its service ID. Dependencies are checked automatically. Service IDs include: pool-controller, memory, recovery, eval-aggregator, policy-engine, agent-coder, agent-coder-2, agent-planner, agent-architect, agent-reviewer, sandbox",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "description": "Service ID to start",
                    },
                },
                "required": ["service_id"],
            },
        },
    },
    "service_stop": {
        "type": "function",
        "function": {
            "name": "service_stop",
            "description": "Stop a running RASA service by its service ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "description": "Service ID to stop",
                    },
                },
                "required": ["service_id"],
            },
        },
    },
}
