"""Orchestrator daemon - persistent process with stdin/stdout JSON-line IPC."""
from __future__ import annotations

import asyncio
import json
import sys
import threading


def _write_event(data: dict) -> None:
    line = json.dumps(data, default=str)
    sys.stdout.write(line + chr(10))
    sys.stdout.flush()


def main_sync() -> None:
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    runtime = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _write_event({'type': 'error', 'message': 'Invalid JSON input'})
            continue
        action = request.get('action', 'send')
        if action == 'reset':
            if runtime:
                asyncio.run_coroutine_threadsafe(_reset(runtime), loop).result(timeout=10)
            _write_event({'type': 'reset_ok'})
            continue
        text = request.get('text', '').strip()
        if not text:
            _write_event({'type': 'error', 'message': 'Empty message'})
            continue
        if runtime is None:
            from rasa.orchestrator.runtime import OrchestratorRuntime
            runtime = OrchestratorRuntime()
        async def _on_event(event):
            _write_event(event)
        try:
            fut = asyncio.run_coroutine_threadsafe(
                runtime.send_message(text, on_event=_on_event), loop
            )
            result = fut.result(timeout=3600)
            if not result.get('_sent_done'):
                _write_event({'type': 'done', 'result': result})
        except Exception as exc:
            _write_event({'type': 'error', 'message': str(exc)})


async def _reset(runtime):
    runtime.reset()


if __name__ == '__main__':
    main_sync()
