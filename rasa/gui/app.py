import customtkinter as ctk
import threading
import queue
import os
import subprocess
import sys
from pathlib import Path

from rasa.gui.tracker import Tracker, init_schema
from rasa.gui.launcher import launch_all
from rasa.gui.cli import OrchestratorCLI
from rasa.orchestrator.reviews import ReviewManager

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PHASES = ["planning", "in_progress", "review", "blocked", "done"]
PHASE_COLORS = {
    "planning": "#607d8b", "in_progress": "#2196f3",
    "review": "#ff9800", "blocked": "#f44336", "done": "#4caf50"
}
PRIORITY_LABELS = {1: "! Low", 2: "!! Med", 3: "!!! High", 4: "!!!! Critical", 5: "!!!!! Blocker"}
SERVICE_NAMES = ["PostgreSQL", "Redis", "Ollama", "Pool Ctrl", "Orch Daemon"]
PROJECT_ROOT = Path(__file__).parent.parent.parent


class RasaGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RASA Command Center")
        self.geometry("1100x700")
        self.minsize(900, 500)

        init_schema()
        self.tracker = Tracker()
        self.cli = OrchestratorCLI(output_callback=self._cli_output)
        self._msg_queue = queue.Queue()
        self._selected_project = None
        self._service_labels = {}
        self._pool_proc = None

        self._build_ui()
        self._poll_queue()
        self._refresh_projects()
        self._refresh_activity()

        self._msg_queue.put("\n" + "=" * 50 + "\n")
        self._msg_queue.put("  Welcome to RASA Command Center\n")
        self._msg_queue.put("  Type a message to talk to the Orchestrator.\n")
        self._msg_queue.put("  Type 'help' for additional commands.\n")
        self._msg_queue.put("=" * 50 + "\n\n")
        self.after(500, self._auto_check_services)
        self.after(2000, self._auto_start_all)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Left panel ---
        self.left = ctk.CTkFrame(self, width=320)
        self.left.grid(row=0, column=0, sticky="nsw", padx=4, pady=4)
        self.left.grid_propagate(False)

        ctk.CTkLabel(self.left, text="RASA Command Center", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(8, 2))

        # Service status area
        svc_header = ctk.CTkFrame(self.left, fg_color="transparent")
        svc_header.pack(pady=(6, 2), padx=8, fill="x")
        ctk.CTkLabel(svc_header, text="Services", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        self.svc_spinner = ctk.CTkLabel(svc_header, text="", font=ctk.CTkFont(size=10))
        self.svc_spinner.pack(side="right")

        svc_frame = ctk.CTkFrame(self.left, fg_color="#1a1a2e", corner_radius=6)
        svc_frame.pack(pady=(0, 2), padx=8, fill="x")
        for name in SERVICE_NAMES:
            row = ctk.CTkFrame(svc_frame, fg_color="transparent")
            row.pack(pady=2, padx=6, fill="x")
            dot = ctk.CTkLabel(row, text="  ", width=14, height=14, corner_radius=7,
                              fg_color="#607d8b", text_color="#607d8b")
            dot.pack(side="left", padx=(0, 6))
            lbl = ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=16))
            lbl.pack(side="left")
            status_lbl = ctk.CTkLabel(row, text="...", font=ctk.CTkFont(size=10), text_color="#607d8b")
            status_lbl.pack(side="right")
            self._service_labels[name] = (dot, status_lbl)

        # Control buttons row
        btn_row = ctk.CTkFrame(self.left, fg_color="transparent")
        btn_row.pack(pady=(2, 6), padx=8, fill="x")
        self.launch_btn = ctk.CTkButton(btn_row, text="REFRESH", command=self._launch,
                                        fg_color="#4caf50", hover_color="#388e3c", height=28)
        self.launch_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.pool_btn = ctk.CTkButton(btn_row, text='START ALL', command=self._toggle_pool,
                                      fg_color="#2196f3", hover_color="#1976d2", height=28)
        self.pool_btn.pack(side="right", fill="x", expand=True, padx=(2, 0))

        # Projects
        ctk.CTkLabel(self.left, text="Projects", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=8)
        self.project_frame = ctk.CTkScrollableFrame(self.left, height=200)
        self.project_frame.pack(pady=4, padx=8, fill="both", expand=True)

        add_frame = ctk.CTkFrame(self.left, fg_color="transparent")
        add_frame.pack(pady=4, padx=8, fill="x")
        self.new_proj_entry = ctk.CTkEntry(add_frame, placeholder_text="New project name...")
        self.new_proj_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.new_proj_entry.bind("<Return>", lambda e: self._add_project())
        ctk.CTkButton(add_frame, text="+", width=30, command=self._add_project).pack(side="right")

        # --- Right panel (tabs) ---
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        self.tabview.add("CLI")
        self.tabview.add("Tracker")
        self.tabview.add("Activity")

        # --- CLI Tab ---
        cli_tab = self.tabview.tab("CLI")
        cli_tab.grid_columnconfigure(0, weight=1)
        cli_tab.grid_rowconfigure(0, weight=1)
        cli_tab.grid_rowconfigure(1, weight=0)

        self.cli_output = ctk.CTkTextbox(cli_tab, font=ctk.CTkFont(family="Consolas", size=16))
        self.cli_output.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))

        cli_input_frame = ctk.CTkFrame(cli_tab, fg_color="transparent")
        cli_input_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        cli_input_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(cli_input_frame, text=">").pack(side="left", padx=(0, 4))
        self.cli_entry = ctk.CTkEntry(cli_input_frame, placeholder_text="Talk to the Orchestrator...")
        self.cli_entry.pack(side="left", fill="x", expand=True)
        self.cli_entry.bind("<Return>", self._cli_send)
        ctk.CTkButton(cli_input_frame, text="Send", width=50, command=lambda: self._cli_send(None)).pack(side="right", padx=(4, 0))

        # --- Tracker Tab ---
        track_tab = self.tabview.tab("Tracker")
        track_tab.grid_columnconfigure(0, weight=1)
        track_tab.grid_rowconfigure(0, weight=0)
        track_tab.grid_rowconfigure(1, weight=1)

        self.track_header = ctk.CTkLabel(track_tab, text="Select a project from the left panel",
                                         font=ctk.CTkFont(size=16, weight="bold"))
        self.track_header.grid(row=0, column=0, sticky="w", padx=8, pady=4)

        self.track_frame = ctk.CTkScrollableFrame(track_tab)
        self.track_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        # --- Activity Tab ---
        act_tab = self.tabview.tab("Activity")
        act_tab.grid_columnconfigure(0, weight=1)
        act_tab.grid_rowconfigure(0, weight=1)
        self.activity_box = ctk.CTkTextbox(act_tab, font=ctk.CTkFont(family="Consolas", size=16))
        self.activity_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

    def _poll_queue(self):
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                self.cli_output.insert("end", msg + "\n")
                self.cli_output.see("end")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _cli_output(self, msg):
        self._msg_queue.put(msg)

    def _cli_send(self, event):
        line = self.cli_entry.get().strip()
        if not line:
            return
        self.cli_entry.delete(0, "end")
        self.cli_output.insert("end", "> " + line + "\n")
        self.cli_output.see("end")
        self.cli_entry.configure(state="disabled")
        threading.Thread(target=self._cli_thread, args=(line,), daemon=True).start()

    def _cli_thread(self, line):
        try:
            result = self.cli.execute(line)
            self._msg_queue.put(chr(10) + result)
            self._msg_queue.put("=" * 58)
        except Exception as e:
            self._msg_queue.put(f"Error: {e}")
        finally:
            self.after(0, lambda: self.cli_entry.configure(state="normal"))

    def _toggle_pool(self):
        if self._pool_proc and self._pool_proc.poll() is None:
            self._pool_proc.terminate()
            self._pool_proc = None
            self.cli.stop()
            self.pool_btn.configure(text='START ALL', fg_color="#2196f3")
            self._update_service("Pool Ctrl", False, "stopped")
            self._update_service("Orch Daemon", False, "stopped")
            self._msg_queue.put("[system] Pool controller and orchestrator daemon stopped.")
        else:
            self.pool_btn.configure(text="STARTING...", state="disabled")
            self._msg_queue.put("[system] Starting all services...")
            threading.Thread(target=self._start_pool_thread, daemon=True).start()
            if not self.cli.is_running:
                threading.Thread(target=lambda: (self.cli.start(), self._update_orch_daemon_status()), daemon=True).start()

    def _start_pool_thread(self):
        try:
            env = os.environ.copy()
            env["RASA_DB_PASSWORD"] = os.environ.get("RASA_DB_PASSWORD", "8764")
            env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._pool_proc = subprocess.Popen(
                [sys.executable, "-m", "rasa.pool.controller"],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self.after(0, lambda: self.pool_btn.configure(text="STOP ALL", fg_color="#f44336", state="normal"))
            self.after(0, lambda: self._update_service("Pool Ctrl", True, "running"))
            self._msg_queue.put("[system] Pool controller started (pool pid=" + str(self._pool_proc.pid) + ").")
        except Exception as e:
            self.after(0, lambda: self.pool_btn.configure(text='START ALL', fg_color="#2196f3", state="normal"))
            self._msg_queue.put(f"[system] Failed to start pool controller: {e}")


    def _auto_check_services(self):
        self._launch()
        self.after(30000, self._auto_check_services)

    def _auto_start_all(self):
        if not self._pool_proc or self._pool_proc.poll() is not None:
            self._msg_queue.put("[system] Auto-starting pool controller...")
            threading.Thread(target=self._start_pool_thread, daemon=True).start()
        if not self.cli.is_running:
            self._msg_queue.put("[system] Auto-starting orchestrator daemon...")
            threading.Thread(target=lambda: (self.cli.start(), self._update_orch_daemon_status()), daemon=True).start()

    def _update_orch_daemon_status(self):
        import time
        time.sleep(1)
        ok = self.cli.is_running
        self.after(0, lambda: self._update_service("Orch Daemon", ok, "running" if ok else "offline"))

    def _launch(self):
        self.launch_btn.configure(text="Checking...", state="disabled")
        self.svc_spinner.configure(text="checking...")
        threading.Thread(target=self._launch_thread, daemon=True).start()

    def _launch_thread(self):
        results = launch_all()
        for name, ok, msg in results:
            self.after(0, lambda n=name, o=ok, m=msg: self._update_service(n, o, m))
        ok_count = sum(1 for _, ok, _ in results if ok)
        # Also check pool controller
        pool_ok = self._pool_proc is not None and self._pool_proc.poll() is None
        if pool_ok:
            self.after(0, lambda: self._update_service("Pool Ctrl", True, "running"))
            ok_count += 1
        self.after(0, lambda: self.launch_btn.configure(text="REFRESH", state="normal"))
        self.after(0, lambda: self.svc_spinner.configure(text=f"{ok_count}/{len(results)+1} up"))

    def _update_service(self, name, ok, detail):
        if name in self._service_labels:
            dot, lbl = self._service_labels[name]
            if ok:
                dot.configure(fg_color="#4caf50", text_color="#4caf50")
                lbl.configure(text="running", text_color="#4caf50")
            else:
                dot.configure(fg_color="#f44336", text_color="#f44336")
                lbl.configure(text="offline", text_color="#f44336")

    def _add_project(self):
        name = self.new_proj_entry.get().strip()
        if not name:
            return
        self.new_proj_entry.delete(0, "end")
        self.tracker.add_project(name)
        self._refresh_projects()
        self._refresh_activity()

    def _refresh_projects(self):
        for w in self.project_frame.winfo_children():
            w.destroy()
        projects = self.tracker.list_projects()
        for p in projects:
            color = PHASE_COLORS.get(p["phase"], "#607d8b")
            frame = ctk.CTkFrame(self.project_frame, fg_color=color, corner_radius=6)
            frame.pack(pady=2, padx=2, fill="x")
            frame.bind("<Button-1>", lambda e, pid=p["id"]: self._select_project(pid))
            lbl = ctk.CTkLabel(frame, text=f"{p['name']}  [{p['phase']}]  {PRIORITY_LABELS.get(p['priority'], '')}",
                              font=ctk.CTkFont(size=16))
            lbl.pack(pady=4, padx=6)
            lbl.bind("<Button-1>", lambda e, pid=p["id"]: self._select_project(pid))

    def _select_project(self, project_id):
        self._selected_project = project_id
        p = self.tracker.get_project(project_id)
        if not p:
            return
        self.track_header.configure(text=f"{p['name']} [{p['phase']}]")
        for w in self.track_frame.winfo_children():
            w.destroy()

        phase_frame = ctk.CTkFrame(self.track_frame, fg_color="transparent")
        phase_frame.pack(pady=4, fill="x")
        ctk.CTkLabel(phase_frame, text="Phase:").pack(side="left", padx=4)
        phase_var = ctk.StringVar(value=p["phase"])
        for ph in PHASES:
            ctk.CTkRadioButton(phase_frame, text=ph, variable=phase_var, value=ph,
                              command=lambda ph=ph: self._set_phase(project_id, ph)).pack(side="left", padx=2)

        prio_frame = ctk.CTkFrame(self.track_frame, fg_color="transparent")
        prio_frame.pack(pady=4, fill="x")
        ctk.CTkLabel(prio_frame, text="Priority:").pack(side="left", padx=4)
        prio_var = ctk.IntVar(value=p["priority"])
        for val, label in PRIORITY_LABELS.items():
            ctk.CTkRadioButton(prio_frame, text=label, variable=prio_var, value=val,
                              command=lambda v=val: self._set_priority(project_id, v)).pack(side="left", padx=2)

        ctk.CTkLabel(self.track_frame, text="Notes:").pack(anchor="w", padx=8)
        notes_box = ctk.CTkTextbox(self.track_frame, height=120)
        notes_box.pack(pady=4, padx=8, fill="x")
        notes_box.insert("1.0", p.get("notes", ""))
        ctk.CTkButton(self.track_frame, text="Save Notes",
                      command=lambda: self._set_notes(project_id, notes_box.get("1.0", "end-1c"))).pack(pady=2)

        ctk.CTkLabel(self.track_frame, text="Tasks:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(12, 2))
        tasks = self.tracker.list_tasks(project_id)
        for t in tasks:
            tframe = ctk.CTkFrame(self.track_frame)
            tframe.pack(pady=1, padx=8, fill="x")
            ctk.CTkLabel(tframe, text=f"[{t['status']}] {t['title'][:50]}", font=ctk.CTkFont(size=10)).pack(side="left", padx=4)
            if t.get("orch_task_id"):
                ctk.CTkLabel(tframe, text=f"orch:{t['orch_task_id'][:8]}",
                            font=ctk.CTkFont(size=9), text_color="gray").pack(side="right", padx=4)

        # Human reviews from PostgreSQL
        reviews = self._load_reviews()
        if reviews:
            ctk.CTkLabel(self.track_frame, text="Pending Reviews:",
                        font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(12, 2))
            for r in reviews:
                rframe = ctk.CTkFrame(self.track_frame, fg_color="#3a2f1a", corner_radius=4)
                rframe.pack(pady=2, padx=8, fill="x")
                reason = (r.get("reason") or "")[:80]
                agent = (r.get("agent_id") or "?")[:20]
                created = (r.get("created_at") or "")[:16]
                ctk.CTkLabel(rframe, text=reason,
                            font=ctk.CTkFont(size=10), wraplength=280).pack(anchor="w", padx=4, pady=(2, 0))
                ctk.CTkLabel(rframe, text=f"agent: {agent}  |  {created}",
                            font=ctk.CTkFont(size=8), text_color="#ff9800").pack(anchor="w", padx=4, pady=(0, 2))

    def _load_reviews(self):
        try:
            rm = ReviewManager()
            return rm.get_pending_reviews(limit=20)
        except Exception:
            return []

    def _set_phase(self, pid, phase):
        self.tracker.set_phase(pid, phase)
        self._refresh_projects()
        self._select_project(pid)

    def _set_priority(self, pid, priority):
        self.tracker.set_priority(pid, priority)
        self._refresh_projects()

    def _set_notes(self, pid, notes):
        self.tracker.set_notes(pid, notes)
        self._refresh_activity()

    def _refresh_activity(self):
        self.activity_box.delete("1.0", "end")
        entries = self.tracker.get_activity(limit=50)
        for e in entries:
            level = e.get("level", "info")
            prefix = {"info": "  ", "warn": "!! ", "error": "XX ", "success": "OK "}.get(level, "  ")
            self.activity_box.insert("end", f"{prefix}[{e['created_at']}] {e['message']}\n")

    def run(self):
        self.mainloop()
