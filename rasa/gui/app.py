import customtkinter as ctk
import threading
import queue
import os

from rasa.gui.tracker import Tracker, init_schema
from rasa.gui.launcher import launch_all
from rasa.gui.cli import OrchestratorCLI

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PHASES = ["planning", "in_progress", "review", "blocked", "done"]
PHASE_COLORS = {
    "planning": "#607d8b", "in_progress": "#2196f3",
    "review": "#ff9800", "blocked": "#f44336", "done": "#4caf50"
}
PRIORITY_LABELS = {1: "! Low", 2: "!! Med", 3: "!!! High", 4: "!!!! Critical", 5: "!!!!! Blocker"}


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

        self._build_ui()
        self._poll_queue()
        self._refresh_projects()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Left panel ---
        self.left = ctk.CTkFrame(self, width=320)
        self.left.grid(row=0, column=0, sticky="nsw", padx=4, pady=4)
        self.left.grid_propagate(False)

        ctk.CTkLabel(self.left, text="RASA Command Center", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(8, 4))

        # Launcher
        self.launch_btn = ctk.CTkButton(self.left, text="LAUNCH ALL SERVICES", command=self._launch,
                                        fg_color="#4caf50", hover_color="#388e3c", height=36)
        self.launch_btn.pack(pady=6, padx=8, fill="x")
        self.launch_status = ctk.CTkTextbox(self.left, height=80, font=ctk.CTkFont(size=10))
        self.launch_status.pack(pady=(0, 6), padx=8, fill="x")

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

        # --- Right panel (notebook) ---
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

        self.cli_output = ctk.CTkTextbox(cli_tab, font=ctk.CTkFont(family="Consolas", size=11))
        self.cli_output.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))
        self.cli_output.insert("1.0", "Type 'help' for commands.\n\n")

        cli_input_frame = ctk.CTkFrame(cli_tab, fg_color="transparent")
        cli_input_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        cli_input_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(cli_input_frame, text=">").pack(side="left", padx=(0, 4))
        self.cli_entry = ctk.CTkEntry(cli_input_frame, placeholder_text="submit coder-v2-dev Fix the login bug...")
        self.cli_entry.pack(side="left", fill="x", expand=True)
        self.cli_entry.bind("<Return>", self._cli_send)
        ctk.CTkButton(cli_input_frame, text="Send", width=50, command=lambda: self._cli_send(None)).pack(side="right", padx=(4, 0))

        # --- Tracker Tab ---
        track_tab = self.tabview.tab("Tracker")
        track_tab.grid_columnconfigure(0, weight=1)
        track_tab.grid_rowconfigure(0, weight=0)
        track_tab.grid_rowconfigure(1, weight=1)

        self.track_header = ctk.CTkLabel(track_tab, text="Select a project", font=ctk.CTkFont(size=14, weight="bold"))
        self.track_header.grid(row=0, column=0, sticky="w", padx=8, pady=4)

        self.track_frame = ctk.CTkScrollableFrame(track_tab)
        self.track_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        # --- Activity Tab ---
        act_tab = self.tabview.tab("Activity")
        act_tab.grid_columnconfigure(0, weight=1)
        act_tab.grid_rowconfigure(0, weight=1)
        self.activity_box = ctk.CTkTextbox(act_tab, font=ctk.CTkFont(family="Consolas", size=11))
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
        threading.Thread(target=self._cli_thread, args=(line,), daemon=True).start()

    def _cli_thread(self, line):
        try:
            result = self.cli.execute(line)
            self._msg_queue.put(result)
        except Exception as e:
            self._msg_queue.put(f"Error: {e}")

    def _launch(self):
        self.launch_btn.configure(text="Checking...", state="disabled")
        self.launch_status.delete("1.0", "end")
        threading.Thread(target=self._launch_thread, daemon=True).start()

    def _launch_thread(self):
        def progress(msg):
            self._msg_queue.put(msg)
        results = launch_all(progress_callback=progress)
        ok_count = sum(1 for _, ok, _ in results if ok)
        self._msg_queue.put(f"\n{ok_count}/{len(results)} services reachable.")
        self.after(0, lambda: self.launch_btn.configure(text="LAUNCH ALL SERVICES", state="normal"))

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
                              font=ctk.CTkFont(size=11))
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

        # Phase selector
        phase_frame = ctk.CTkFrame(self.track_frame, fg_color="transparent")
        phase_frame.pack(pady=4, fill="x")
        ctk.CTkLabel(phase_frame, text="Phase:").pack(side="left", padx=4)
        phase_var = ctk.StringVar(value=p["phase"])
        for ph in PHASES:
            ctk.CTkRadioButton(phase_frame, text=ph, variable=phase_var, value=ph,
                              command=lambda ph=ph: self._set_phase(project_id, ph)).pack(side="left", padx=2)

        # Priority
        prio_frame = ctk.CTkFrame(self.track_frame, fg_color="transparent")
        prio_frame.pack(pady=4, fill="x")
        ctk.CTkLabel(prio_frame, text="Priority:").pack(side="left", padx=4)
        prio_var = ctk.IntVar(value=p["priority"])
        for val, label in PRIORITY_LABELS.items():
            ctk.CTkRadioButton(prio_frame, text=label, variable=prio_var, value=val,
                              command=lambda v=val: self._set_priority(project_id, v)).pack(side="left", padx=2)

        # Notes
        ctk.CTkLabel(self.track_frame, text="Notes:").pack(anchor="w", padx=8)
        notes_box = ctk.CTkTextbox(self.track_frame, height=120)
        notes_box.pack(pady=4, padx=8, fill="x")
        notes_box.insert("1.0", p.get("notes", ""))
        ctk.CTkButton(self.track_frame, text="Save Notes",
                      command=lambda: self._set_notes(project_id, notes_box.get("1.0", "end-1c"))).pack(pady=2)

        # Tasks for this project
        ctk.CTkLabel(self.track_frame, text="Tasks:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(12, 2))
        tasks = self.tracker.list_tasks(project_id)
        for t in tasks:
            tframe = ctk.CTkFrame(self.track_frame)
            tframe.pack(pady=1, padx=8, fill="x")
            ctk.CTkLabel(tframe, text=f"[{t['status']}] {t['title'][:50]}", font=ctk.CTkFont(size=10)).pack(side="left", padx=4)
            if t.get("orch_task_id"):
                ctk.CTkLabel(tframe, text=f"orch:{t['orch_task_id'][:8]}",
                            font=ctk.CTkFont(size=9), text_color="gray").pack(side="right", padx=4)

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
