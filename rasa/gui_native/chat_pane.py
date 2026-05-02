"""Chat panel for the RASA native GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from rasa.gui_native import api_client


class ChatPane(ttk.Frame):
    """Orchestrator chat panel with message history and input."""

    def __init__(self, parent: ttk.Notebook, **kwargs):
        super().__init__(parent, **kwargs)
        self._project_id: str | None = None
        self._busy = False

        self._build_ui()

    def _build_ui(self):
        # Top bar: project + mode + reset
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(top, text="Project:").pack(side=tk.LEFT, padx=(0, 4))
        self._project_var = tk.StringVar(value="(none)")
        self._project_label = ttk.Label(top, textvariable=self._project_var, font=("Segoe UI", 10, "bold"))
        self._project_label.pack(side=tk.LEFT, padx=(0, 12))

        self._mode_var = tk.StringVar(value="Step-by-Step")
        self._mode_btn = ttk.Button(top, textvariable=self._mode_var, command=self._toggle_mode, width=16)
        self._mode_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._reset_btn = ttk.Button(top, text="Reset", command=self._reset)
        self._reset_btn.pack(side=tk.RIGHT)

        # Message area
        msg_frame = ttk.Frame(self)
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._msg_text = tk.Text(
            msg_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#0d1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            font=("Consolas", 11),
            padx=12,
            pady=12,
            relief=tk.FLAT,
            borderwidth=0,
        )
        self._msg_text.tag_config("user", foreground="#58a6ff", font=("Consolas", 11, "bold"))
        self._msg_text.tag_config("assistant", foreground="#e6edf3")
        self._msg_text.tag_config("system", foreground="#8b949e", font=("Consolas", 10, "italic"))
        self._msg_text.tag_config("step", foreground="#22c55e", font=("Consolas", 10))
        self._msg_text.tag_config("thinking", foreground="#8b949e", font=("Consolas", 10, "italic"))
        self._msg_text.tag_config("meta", foreground="#8b949e", font=("Consolas", 9))

        scroll = ttk.Scrollbar(msg_frame, orient=tk.VERTICAL, command=self._msg_text.yview)
        self._msg_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._msg_text.pack(fill=tk.BOTH, expand=True)

        # Input area
        input_frame = ttk.Frame(self)
        input_frame.pack(fill=tk.X, padx=8, pady=(4, 8))

        self._input_text = tk.Text(
            input_frame,
            height=3,
            wrap=tk.WORD,
            bg="#161b22",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            font=("Consolas", 11),
            padx=8,
            pady=8,
            relief=tk.FLAT,
            borderwidth=1,
        )
        self._input_text.bind("<Return>", self._on_enter)
        self._input_text.bind("<Shift-Return>", lambda e: None)  # Let default handle newline
        self._input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        self._send_btn = ttk.Button(input_frame, text="Send", command=self._send, width=8)
        self._send_btn.pack(side=tk.RIGHT)

    def set_project(self, project_id: str, project_name: str) -> None:
        self._project_id = project_id
        self._project_var.set(project_name)

    def set_mode(self, mode: str) -> None:
        label = "Autonomous" if mode == "autonomous" else "Step-by-Step"
        self._mode_var.set(label)

    def _toggle_mode(self):
        current = self._mode_var.get()
        new_mode = "Autonomous" if current == "Step-by-Step" else "Step-by-Step"
        self._mode_var.set(new_mode)

    def get_mode(self) -> str:
        return "autonomous" if self._mode_var.get() == "Autonomous" else "step_by_step"

    def _reset(self):
        self._msg_text.configure(state=tk.NORMAL)
        self._msg_text.delete("1.0", tk.END)
        self._msg_text.configure(state=tk.DISABLED)
        api_client.reset_orchestrator()

    def _append(self, text: str, tag: str = "assistant") -> None:
        self._msg_text.configure(state=tk.NORMAL)
        self._msg_text.insert(tk.END, text + "\n", tag)
        self._msg_text.see(tk.END)
        self._msg_text.configure(state=tk.DISABLED)

    def _show_thinking(self):
        self._append("...", "thinking")

    def _hide_thinking(self):
        self._msg_text.configure(state=tk.NORMAL)
        # Remove last line if it's the thinking indicator
        content = self._msg_text.get("1.0", tk.END).strip()
        if content.endswith("..."):
            self._msg_text.delete("end-2l", tk.END)
        self._msg_text.configure(state=tk.DISABLED)

    def _on_enter(self, event):
        if not event.state & 0x1:  # Shift not held
            self._send()
            return "break"

    def _send(self):
        if self._busy:
            return
        text = self._input_text.get("1.0", tk.END).strip()
        if not text:
            return
        self._input_text.delete("1.0", tk.END)

        self._append(f">>> {text}", "user")
        self._show_thinking()
        self._busy = True
        self._send_btn.configure(state=tk.DISABLED)

        api_client.send_message(
            message=text,
            project_id=self._project_id,
            mode=self.get_mode(),
            on_done=lambda r: self.after(0, self._on_response, r),
        )

    def _on_response(self, result: dict) -> None:
        self._busy = False
        self._send_btn.configure(state=tk.NORMAL)
        self._hide_thinking()

        if "error" in result:
            self._append(f"[Error] {result['error']}", "system")
            return

        # Show tool steps
        for step in result.get("steps", []):
            name = step.get("name", "tool")
            args = step.get("args", {})
            result_text = step.get("result", "")
            arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
            self._append(f"  └─ {name}({arg_str})", "step")

        # Show reply
        reply = result.get("reply", "")
        self._append(reply, "assistant")

        # Show metadata
        meta = result.get("model", "") or ""
        usage = result.get("usage", {})
        elapsed = result.get("elapsed_seconds", 0)
        self._append(
            f"  ── {meta} · {usage.get('prompt_tokens', 0)}+{usage.get('completion_tokens', 0)} tok · {elapsed}s",
            "meta",
        )
