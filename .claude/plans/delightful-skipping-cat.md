# Fix: Orchestrator Project Queue Context + Terminal Tab

## Context
The orchestrator LLM has no knowledge of the project queue because:
1. `TerminalPanel` never sends a `project_id` with its messages (`self._project_id` is always `None`)
2. `OrchestratorRuntime` renders the system prompt **once** on the very first message and never refreshes it — so even if a project gets set later, the LLM never sees the project context

Result: The orchestrator can't see projects, tasks, or queue state.

## Changes

### 1. `rasa/orchestrator/runtime.py` — Refresh system prompt every turn

Replace the one-shot system prompt initialization (lines 456-458) with per-call replacement:

```python
# BEFORE:
if not self._messages:
    system = self._render_system()
    self._messages.append({"role": "system", "content": system})

# AFTER:
system = self._render_system()
if self._messages and self._messages[0]["role"] == "system":
    self._messages[0]["content"] = system
else:
    self._messages.insert(0, {"role": "system", "content": system})
```

This ensures that every `send_message()` call injects current project/task/review context into the system prompt. Conversation history is preserved.

**Trade-off:** Slightly more tokens per turn (project summary + active tasks are re-fetched each call). Acceptable given the small project scale.

### 2. `rasa/gui_nice/terminal_panel.py` — Wire project selection

- Remove dead `self._project_id` field
- Import shared state from `rasa.gui_nice.state`
- In `_on_submit()`, read `state.selected_project_id` to include with the request

Optional: Add a project label/indicator in the panel UI so the user knows which project is active.

### 3. No changes needed to `app.py` or other files

The Terminal tab is already in `app.py` and renders in the HTML. User just needs to refresh the browser.

## Verification
1. Start API server: `python -m rasa.gui.server`
2. Start dashboard: `python -m rasa.gui_nice.app`
3. Open `http://127.0.0.1:8401` in browser
4. Click Terminal tab
5. Type a message like "What projects are available?" — the orchestrator should now see the project queue and respond with project/task information
6. Switch project in Projects tab, go back to Terminal tab and verify the orchestrator knows about the new project context
