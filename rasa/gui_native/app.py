"""Main Tkinter window for the RASA native GUI command center."""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from rasa.gui_native import api_client
from rasa.gui_native.chat_pane import ChatPane
from rasa.gui_native.project_pane import ProjectPane
from rasa.gui_native.services_pane import ServicesPane


class App(tk.Tk):
    """RASA native GUI command center."""

    def __init__(self):
        super().__init__()

        self.title("RASA Command Center")
        self.geometry("1200x800")
        self.minsize(900, 600)

        # Center on screen
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"+{x}+{y}")

        self._apply_theme()
        self._build_menu()
        self._build_ui()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Theme ──

    def _apply_theme(self):
        self.configure(bg="#0d1117")

        style = ttk.Style()
        style.theme_use("clam")

        # Dark palette
        bg = "#0d1117"
        fg = "#e6edf3"
        select_bg = "#1f6feb"
        select_fg = "#ffffff"
        border = "#30363d"
        input_bg = "#161b22"
        accent = "#58a6ff"

        style.configure(".", background=bg, foreground=fg, fieldbackground=input_bg,
                        selectbackground=select_bg, selectforeground=select_fg,
                        borderwidth=0, focuscolor="none")

        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
        style.configure("TButton", background=input_bg, foreground=fg,
                        borderwidth=1, focuscolor="none", font=("Segoe UI", 10))
        style.map("TButton",
                  background=[("active", border), ("pressed", select_bg)],
                  foreground=[("active", fg)])

        style.configure("TMenubutton", background=input_bg, foreground=fg)

        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=input_bg, foreground=fg,
                        padding=[12, 4], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", select_bg), ("active", border)])

        style.configure("Treeview", background=input_bg, foreground=fg,
                        fieldbackground=input_bg, borderwidth=0,
                        font=("Segoe UI", 10), rowheight=26)
        style.configure("Treeview.Heading", background=bg, foreground=fg,
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview",
                  background=[("selected", select_bg)],
                  foreground=[("selected", select_fg)])

        style.configure("TSpinbox", background=input_bg, foreground=fg,
                        fieldbackground=input_bg)
        style.configure("TEntry", fieldbackground=input_bg, foreground=fg)
        style.configure("TText", background=input_bg, foreground=fg)

        style.configure("Horizontal.TScrollbar", background=input_bg,
                        troughcolor=bg, borderwidth=0)
        style.configure("Vertical.TScrollbar", background=input_bg,
                        troughcolor=bg, borderwidth=0)

        style.configure("TPanedWindow", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)

        # Status bar
        style.configure("Status.TLabel", background="#161b22", foreground="#8b949e",
                        font=("Segoe UI", 9), padding=(8, 2))

    # ── Menu ──

    def _build_menu(self):
        menubar = tk.Menu(self, bg="#161b22", fg="#e6edf3",
                          activebackground="#1f6feb", activeforeground="#ffffff")

        # File
        file_menu = tk.Menu(menubar, tearoff=False, bg="#161b22", fg="#e6edf3",
                            activebackground="#1f6feb", activeforeground="#ffffff")
        file_menu.add_command(label="New Project", command=self._new_project)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # Mode
        self._mode_var = tk.StringVar(value="step_by_step")
        mode_menu = tk.Menu(menubar, tearoff=False, bg="#161b22", fg="#e6edf3",
                            activebackground="#1f6feb", activeforeground="#ffffff")
        mode_menu.add_radiobutton(label="Step-by-Step", variable=self._mode_var,
                                  value="step_by_step", command=self._on_mode_change)
        mode_menu.add_radiobutton(label="Autonomous", variable=self._mode_var,
                                  value="autonomous", command=self._on_mode_change)
        menubar.add_cascade(label="Mode", menu=mode_menu)

        # Help
        help_menu = tk.Menu(menubar, tearoff=False, bg="#161b22", fg="#e6edf3",
                            activebackground="#1f6feb", activeforeground="#ffffff")
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    # ── UI ──

    def _build_ui(self):
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Tab 1: Chat
        self._chat_pane = ChatPane(self._notebook)
        self._notebook.add(self._chat_pane, text="  Chat  ")

        # Tab 2: Project
        self._project_pane = ProjectPane(self._notebook)
        self._notebook.add(self._project_pane, text="  Project  ")

        # Tab 3: Services
        self._services_pane = ServicesPane(self._notebook)
        self._notebook.add(self._services_pane, text="  Services  ")

        # Sync project selection to chat pane on tab switch
        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_switch)

    # ── Status bar ──

    def _build_statusbar(self):
        self._status_bar = ttk.Frame(self, style="TFrame")
        self._status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._project_status = ttk.Label(
            self._status_bar, text="Project: (none)", style="Status.TLabel"
        )
        self._project_status.pack(side=tk.LEFT)

        self._mode_status = ttk.Label(
            self._status_bar, text="Mode: Step-by-Step", style="Status.TLabel"
        )
        self._mode_status.pack(side=tk.LEFT, padx=(16, 0))

        self._server_status = ttk.Label(
            self._status_bar, text="Server: checking...", style="Status.TLabel"
        )
        self._server_status.pack(side=tk.RIGHT)

        self._update_status_bar()

    def _update_status_bar(self):
        pid = self._project_pane.get_selected_project_id()
        pname = self._project_pane.get_selected_project_name()
        self._project_status.config(text=f"Project: {pname}")
        if pid:
            self._chat_pane.set_project(pid, pname)
            self._chat_pane.set_mode(self._mode_var.get())

    # ── Event handlers ──

    def _on_tab_switch(self, event=None):
        self._update_status_bar()

    def _on_mode_change(self):
        mode = self._mode_var.get()
        label = "Autonomous" if mode == "autonomous" else "Step-by-Step"
        self._mode_status.config(text=f"Mode: {label}")
        self._chat_pane.set_mode(mode)

    def _new_project(self):
        self._notebook.select(1)  # Switch to Project tab
        self._project_pane._new_project()
        self._update_status_bar()

    def _show_about(self):
        messagebox.showinfo(
            "RASA Command Center",
            "RASA — Reliable Autonomous System of Agents\n"
            "Version 0.1.0\n\n"
            "Multi-agent orchestration platform.\n"
            "Go control plane · Python agent runtime · PostgreSQL bus",
        )

    def _on_close(self):
        result = messagebox.askyesno(
            "Exit",
            "Are you sure you want to exit?\n\nThe server will continue running in the background.",
        )
        if result:
            self.destroy()


def launch():
    """Launch the RASA native GUI."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    launch()
