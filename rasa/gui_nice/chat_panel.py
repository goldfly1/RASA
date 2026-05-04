"""Chat panel — terminal-style CLI interface via relay."""

from __future__ import annotations

import time

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.theme import ACCENT, DIM, ERROR, FG, SUCCESS, SURFACE_BG


class ChatPanel:
    """Terminal-style chat interface for the orchestrator relay."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._log: ui.column | None = None
        self._input: ui.input | None = None
        self._send_btn: ui.button | None = None
        self._reset_btn: ui.button | None = None
        self._ready = True

    def build(self):
        with ui.column().classes('w-full gap-0 border-custom rounded').style(f'background:{SURFACE_BG}'):
            # Terminal header
            with ui.row().classes('w-full items-center px-3 py-1 border-custom').style(f'background:#0d1117'):
                ui.label("Orchestrator Relay").classes('text-xs text-dim mono')
                ui.space()
                self._status_dot = ui.icon("circle", color=DIM).props("size=8px")
                ui.label("ready").classes('text-xs text-dim mono')

            # Log area — fills available vertical space
            with ui.scroll_area().classes('w-full h-[65vh] px-3 py-2'):
                self._log = ui.column().classes('w-full gap-1')
                _log_line(self._log, "RASA Orchestrator Relay (file-based)", DIM)
                _log_line(self._log, "Type a message and press Enter to send.", DIM)
                _log_line(self._log, "Type /help for commands.", DIM)
                _log_line(self._log, "", DIM)

            # Input row — sticky at bottom within the card
            with ui.row().classes('w-full items-center px-3 py-2 border-custom sticky bottom-0').style(f'background:#0d1117'):
                ui.label("$").classes('text-green-400 font-bold mono')
                self._input = ui.input(
                    placeholder="type a message..."
                ).classes('flex-1 mono').props("input-style='font-family:Consolas,monospace'")
                self._input.on("keydown.enter", self._send)

                self._send_btn = ui.button("Send", color="primary",
                                           on_click=self._send).props("size=sm flat")
                self._reset_btn = ui.button("Reset", color="negative",
                                            on_click=self._reset).props("size=sm flat")

    async def _send(self):
        if not self._input or not self._log or not self._ready:
            return

        msg = self._input.value.strip()
        if not msg:
            return

        self._input.value = ""
        self._ready = False
        if self._send_btn:
            self._send_btn.disable()

        _log_line(self._log, f"$ {msg}", ACCENT)
        _log_line(self._log, "Sending...", DIM)

        if msg.startswith("/"):
            await self._handle_command(msg)
            self._ready = True
            if self._send_btn:
                self._send_btn.enable()
            return

        result = await self.api.send_message(msg)

        if result.ok:
            data = result.data
            resp = data.get("response", data.get("message", str(data)))
            _log_line(self._log, str(resp), FG)
        else:
            _log_line(self._log, f"Error: {result.error}", ERROR)

        self._ready = True
        if self._send_btn:
            self._send_btn.enable()

    async def _handle_command(self, cmd: str):
        if cmd == "/help":
            _log_line(self._log, "Commands: /help, /reset, /projects, /status", DIM)
        elif cmd == "/reset":
            await self._reset()
        elif cmd == "/projects":
            r = await self.api.get_projects()
            if r.ok:
                data = r.data
                projects = data if isinstance(data, list) else data.get("projects", [])
                for p in projects:
                    _log_line(self._log, f"  {p['name']} ({p.get('status', '?')})", FG)
            else:
                _log_line(self._log, f"Error: {r.error}", ERROR)
        elif cmd == "/status":
            r = await self.api.get_about()
            if r.ok:
                data = r.data
                for k, v in data.items():
                    _log_line(self._log, f"  {k}: {v}", FG)
            else:
                _log_line(self._log, f"Error: {r.error}", ERROR)
        else:
            _log_line(self._log, f"Unknown command: {cmd}", ERROR)

    async def _reset(self):
        r = await self.api.reset_orchestrator()
        if r.ok:
            _log_line(self._log, "Orchestrator reset.", SUCCESS)
        else:
            _log_line(self._log, f"Reset failed: {r.error}", ERROR)


def _log_line(parent: ui.column, text: str, color: str = FG):
    with parent:
        ui.label(text).classes(f'mono text-xs').style(f'color:{color}; line-height:1.5')
