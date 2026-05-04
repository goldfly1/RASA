"""Terminal-style CLI panel for the RASA orchestrator."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from rasa.gui_native import api_client


class ChatPane(ttk.Frame):
    """Terminal-style CLI for communicating with the orchestrator."""

    def __init__(self, parent: ttk.Notebook, **kwargs):
        super().__init__(parent, **kwargs)
        self._project_id: str | None = None
        self._busy = False

        self._build_ui()

    def _build_ui(self):
        # ── Terminal output area ──
        term_frame = ttk.Frame(self)
        term_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))

        self._output = tk.Text(
            term_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#0d1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            font=("Consolas", 11),
            padx=10,
            pady=10,
            relief=tk.FLAT,
            borderwidth=0,
            cursor="arrow",
        )
        self._output.tag_config("prompt", foreground="#22c55e")
        self._output.tag_config("output", foreground="#e6edf3")
        self._output.tag_config("error", foreground="#ef4444")
        self._output.tag_config("dim", foreground="#8b949e")

        scroll = ttk.Scrollbar(term_frame, orient=tk.VERTICAL, command=self._output.yview)
        self._output.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._output.pack(fill=tk.BOTH, expand=True)

        # Bind click-to-focus to the frame instead (output area is read-only)
        self._output.bind("<Button-1>", self._focus_input)

        # ── Input area ──
        input_frame = ttk.Frame(self)
        input_frame.pack(fill=tk.X, padx=4, pady=(0, 4))

        self._prompt_label = ttk.Label(
            input_frame,
            text="$",
            font=("Consolas", 12, "bold"),
            foreground="#22c55e",
            background="#0d1117",
        )
        self._prompt_label.pack(side=tk.LEFT, padx=(6, 4))

        self._input = tk.Text(
            input_frame,
            height=2,
            wrap=tk.WORD,
            bg="#161b22",
            fg="#e6edf3",
            insertbackground="#22c55e",
            font=("Consolas", 11),
            padx=6,
            pady=6,
            relief=tk.FLAT,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#30363d",
        )
        self._input.bind("<Return>", self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)
        self._input.bind("<FocusIn>", self._on_input_focus)
        self._input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        self._send_btn = ttk.Button(
            input_frame, text="SEND", command=self._send, width=6
        )
        self._send_btn.pack(side=tk.RIGHT, padx=(0, 4))

        # ── Status bar (subtle) ──
        status_frame = ttk.Frame(self)
        status_frame.pack(fill=tk.X, padx=6, pady=(0, 4))

        self._status_bar = ttk.Label(
            status_frame,
            text="Connected",
            font=("Consolas", 9),
            foreground="#8b949e",
            background="#0d1117",
        )
        self._status_bar.pack(side=tk.LEFT)

        self._reset_link = ttk.Label(
            status_frame,
            text="[reset]",
            font=("Consolas", 9),
            foreground="#58a6ff",
            cursor="hand2",
        )
        self._reset_link.pack(side=tk.RIGHT)
        self._reset_link.bind("<Button-1>", lambda e: self._reset())

        self._input.focus_set()

    # ── Terminal output ──

    def _write(self, text: str, tag: str = "output") -> None:
        self._output.configure(state=tk.NORMAL)
        self._output.insert(tk.END, text + "\n", tag)
        self._output.see(tk.END)
        self._output.configure(state=tk.DISABLED)

    def _show_thinking(self):
        self._write("⏳ ...", "dim")

    def _hide_thinking(self):
        self._output.configure(state=tk.NORMAL)
        content = self._output.get("1.0", tk.END).strip()
        if content.endswith("..."):
            self._output.delete("end-2l", tk.END)
        self._output.configure(state=tk.DISABLED)

    # ── Events ──

    def _on_enter(self, event):
        if not (event.state & 0x1):  # Shift not held
            self._send()
            return "break"

    def _on_input_focus(self, event):
        self._input.configure(highlightbackground="#22c55e")

    def _focus_input(self, event):
        self._input.focus_set()

    # ── Send / receive ──

    def _send(self):
        if self._busy:
            return
        text = self._input.get("1.0", tk.END).strip()
        if not text:
            return
        self._input.delete("1.0", tk.END)

        # Echo command in terminal style
        self._write(f"$ {text}", "prompt")
        self._show_thinking()
        self._busy = True
        self._send_btn.configure(state=tk.DISABLED)
        self._status_bar.configure(text="Sending...")

        api_client.send_message(
            message=text,
            project_id=self._project_id,
            mode="step_by_step",
            on_done=lambda r: self.after(0, self._on_response, r),
        )

    def _on_response(self, result: dict) -> None:
        self._busy = False
        self._send_btn.configure(state=tk.NORMAL)
        self._hide_thinking()

        if "error" in result:
            self._write(f"error: {result['error']}", "error")
            self._status_bar.configure(text="Ready")
            return

        # Show tool steps in dim
        for step in result.get("steps", []):
            name = step.get("name", "tool")
            args = step.get("args", {})
            arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
            self._write(f"  ─ {name}({arg_str})", "dim")

        # Show reply
        reply = result.get("reply", "")
        if reply:
            self._write(reply, "output")

        # Show metadata in dim
        meta = result.get("model", "") or ""
        usage = result.get("usage", {})
        elapsed = result.get("elapsed_seconds", 0)
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        meta_parts = [m for m in [meta, f"{pt}+{ct} tok" if pt else "", f"{elapsed}s" if elapsed else ""] if m]
        if meta_parts:
            self._write("  ── " + " · ".join(meta_parts), "dim")

        self._status_bar.configure(text="Ready")
        self._input.focus_set()

    # ── External API ──

    def set_project(self, project_id: str, project_name: str) -> None:
        self._project_id = project_id
        self._status_bar.configure(text=f"Project: {project_name}")

    def _reset(self):
        self._output.configure(state=tk.NORMAL)
        self._output.delete("1.0", tk.END)
        self._output.configure(state=tk.DISABLED)
        api_client.reset_orchestrator()
        self._status_bar.configure(text="Reset")
        self._write("Session reset", "dim")
