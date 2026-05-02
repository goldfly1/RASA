"""Project dashboard for the RASA native GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from rasa.gui_native import api_client

STATUS_COLORS = {
    "ACTIVE": "#22c55e",
    "PENDING": "#8b949e",
    "ASSIGNED": "#58a6ff",
    "RUNNING": "#eab308",
    "COMPLETED": "#22c55e",
    "FAILED": "#ef4444",
    "CHECKPOINTED": "#d2a8ff",
}


class ProjectPane(ttk.Frame):
    """Project dashboard with project list and task DAG tree."""

    def __init__(self, parent: ttk.Notebook, **kwargs):
        super().__init__(parent, **kwargs)
        self._projects: list[dict] = []
        self._tasks: list[dict] = []
        self._current_project_id: str | None = None
        self._after_id: str | None = None

        self._build_ui()
        self._refresh_projects()

    def _build_ui(self):
        # ── Header ──
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(header, text="Projects", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)

        self._new_btn = ttk.Button(header, text="New Project", command=self._new_project)
        self._new_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self._refresh_btn = ttk.Button(header, text="Refresh", command=self._refresh_all)
        self._refresh_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # ── Main split: projects (left) + tasks (right) ──
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Left: project list
        left_frame = ttk.LabelFrame(paned, text="Projects")
        self._project_list = tk.Listbox(
            left_frame,
            bg="#0d1117",
            fg="#e6edf3",
            selectbackground="#1f6feb",
            selectforeground="#ffffff",
            font=("Segoe UI", 11),
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#30363d",
        )
        self._project_list.bind("<<ListboxSelect>>", self._on_project_select)
        self._project_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        paned.add(left_frame, weight=1)

        # Right: task tree + detail
        right_frame = ttk.Frame(paned)
        right_frame.pack(fill=tk.BOTH, expand=True)

        self._task_tree = ttk.Treeview(
            right_frame,
            columns=("status", "soul", "created"),
            show="tree headings",
            height=12,
        )
        self._task_tree.heading("#0", text="Task")
        self._task_tree.heading("status", text="Status")
        self._task_tree.heading("soul", text="Agent")
        self._task_tree.heading("created", text="Created")
        self._task_tree.column("#0", width=300, minwidth=200)
        self._task_tree.column("status", width=100, anchor="center")
        self._task_tree.column("soul", width=120, anchor="center")
        self._task_tree.column("created", width=160, anchor="center")
        self._task_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        task_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self._task_tree.yview)
        self._task_tree.configure(yscrollcommand=task_scroll.set)
        task_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._task_tree.pack(fill=tk.BOTH, expand=True)

        # Detail area
        self._detail_text = tk.Text(
            right_frame,
            height=6,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#0d1117",
            fg="#e6edf3",
            font=("Consolas", 10),
            padx=8,
            pady=8,
            relief=tk.FLAT,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#30363d",
        )
        self._detail_text.pack(fill=tk.X, pady=(4, 0))

        paned.add(right_frame, weight=3)

    # ── Project list ──

    def _refresh_projects(self):
        api_client.fetch_projects(self._on_projects)

    def _on_projects(self, projects: list[dict]):
        self._projects = projects
        self._project_list.delete(0, tk.END)
        for p in projects:
            name = p.get("name", p.get("id", "(unnamed)"))
            self._project_list.insert(tk.END, name)

        # Restore selection
        if self._current_project_id:
            for i, p in enumerate(projects):
                if p["id"] == self._current_project_id:
                    self._project_list.selection_set(i)
                    break

    def _on_project_select(self, event):
        sel = self._project_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._projects):
            return
        proj = self._projects[idx]
        self._current_project_id = proj["id"]
        self._refresh_tasks()

    def _refresh_tasks(self):
        if self._current_project_id:
            api_client.fetch_tasks(self._current_project_id, self._on_tasks)

    def _on_tasks(self, tasks: list[dict]):
        self._tasks = tasks
        self._render_task_tree()

    # ── Task tree ──

    def _render_task_tree(self):
        for item in self._task_tree.get_children():
            self._task_tree.delete(item)

        # Build parent-child map from task parent_id
        child_map: dict[str, list[dict]] = {}
        root_tasks: list[dict] = []
        for t in self._tasks:
            pid = t.get("parent_id")
            if pid:
                child_map.setdefault(pid, []).append(t)
            else:
                root_tasks.append(t)

        def insert_nodes(parent_id: str, nodes: list[dict]):
            for t in sorted(nodes, key=lambda x: x.get("created_at", "")):
                tid = t.get("id", "")
                title = t.get("title", tid[:12])
                status = t.get("status", "PENDING")
                soul = t.get("assigned_soul", t.get("soul_id", ""))
                created = (t.get("created_at") or "")[:19].replace("T", " ")
                if status in STATUS_COLORS:
                    color = STATUS_COLORS[status]
                    self._task_tree.tag_configure(f"status_{tid}", foreground=color)
                    tag = f"status_{tid}"
                else:
                    tag = ""
                item_id = self._task_tree.insert(
                    parent_id, tk.END,
                    text=title,
                    values=(status.capitalize(), soul, created),
                    tags=(tag,),
                    iid=tid,
                )
                # Recurse children
                children = child_map.get(tid, [])
                if children:
                    insert_nodes(item_id, children)

        insert_nodes("", root_tasks)

    def _on_task_select(self, event):
        sel = self._task_tree.selection()
        if not sel:
            return
        tid = sel[0]
        # Find task in our list
        task = None
        for t in self._tasks:
            if t.get("id") == tid:
                task = t
                break
        if not task:
            return

        self._detail_text.configure(state=tk.NORMAL)
        self._detail_text.delete("1.0", tk.END)

        desc = task.get("description", "") or ""
        result = task.get("result", "") or ""
        self._detail_text.insert(tk.END, f"Description:\n{desc}\n\n")
        if result:
            self._detail_text.insert(tk.END, f"Result:\n{result}")
        self._detail_text.configure(state=tk.DISABLED)

    # ── New project dialog ──

    def _new_project(self):
        dialog = tk.Toplevel(self)
        dialog.title("New Project")
        dialog.geometry("480x280")
        dialog.resizable(False, False)
        dialog.configure(bg="#0d1117")
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Project Name", font=("Segoe UI", 11)).pack(anchor=tk.W)
        name_entry = ttk.Entry(frame, font=("Segoe UI", 11))
        name_entry.pack(fill=tk.X, pady=(4, 12))

        ttk.Label(frame, text="Goal", font=("Segoe UI", 11)).pack(anchor=tk.W)
        goal_text = tk.Text(
            frame, height=4, wrap=tk.WORD,
            font=("Segoe UI", 11),
            bg="#161b22", fg="#e6edf3",
            insertbackground="#e6edf3",
            relief=tk.FLAT, borderwidth=1,
        )
        goal_text.pack(fill=tk.BOTH, expand=True, pady=(4, 12))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        def _do_create():
            name = name_entry.get().strip()
            goal = goal_text.get("1.0", tk.END).strip()
            if not name:
                messagebox.showwarning("Validation", "Project name is required.", parent=dialog)
                return
            api_client.create_project(name, goal, on_done=lambda r: self._on_created(r, dialog))

        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(btn_frame, text="Create", command=_do_create).pack(side=tk.RIGHT)

        name_entry.focus_set()
        dialog.wait_window()

    def _on_created(self, result: dict, dialog: tk.Toplevel):
        dialog.destroy()
        self._refresh_projects()

    # ── Refresh all ──

    def _refresh_all(self):
        self._refresh_projects()

    def get_selected_project_id(self) -> str | None:
        return self._current_project_id

    def get_selected_project_name(self) -> str:
        if not self._current_project_id:
            return "(none)"
        for p in self._projects:
            if p["id"] == self._current_project_id:
                return p.get("name", "(unnamed)")
        return "(none)"

    def destroy(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        super().destroy()
