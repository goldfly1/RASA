"""Dark theme constants and CSS for the RASA dashboard."""

DARK_BG = "#0d1117"
SURFACE_BG = "#161b22"
FG = "#e6edf3"
DIM = "#8b949e"
BORDER = "#30363d"
ACCENT = "#58a6ff"
SUCCESS = "#22c55e"
WARNING = "#eab308"
ERROR = "#ef4444"
SELECTION = "#1f6feb"

STATUS_COLORS = {
    "running": SUCCESS,
    "starting": WARNING,
    "stopped": ERROR,
    "unknown": DIM,
    "error": ERROR,
}

GROUP_ORDER = ["infrastructure", "control-plane", "agents", "observability", "other"]
GROUP_LABELS = {
    "infrastructure": "Infrastructure",
    "control-plane": "Control Plane",
    "agents": "Agents",
    "observability": "Observability",
    "other": "Other",
}

TASK_STATUS_COLORS = {
    "ACTIVE": SUCCESS,
    "PENDING": DIM,
    "ASSIGNED": ACCENT,
    "RUNNING": WARNING,
    "COMPLETED": SUCCESS,
    "FAILED": ERROR,
    "CHECKPOINTED": "#d2a8ff",
    "CANCELLED": DIM,
}


def apply():
    from nicegui import ui
    ui.dark_mode().enable()
    ui.add_css(f"""
        body {{ background-color: {DARK_BG}; }}
        .nicegui-content {{ background-color: {DARK_BG}; max-width: none !important; }}
        .q-card {{ background-color: {SURFACE_BG} !important; border: 1px solid {BORDER} !important; }}
        .q-card__section {{ color: {FG}; }}
        .q-header {{ background-color: {SURFACE_BG} !important; border-bottom: 1px solid {BORDER}; }}
        .q-tab {{ color: {DIM}; text-transform: none; }}
        .q-tab--active {{ color: {ACCENT}; }}
        .q-tab-panel {{ padding: 12px; }}
        .q-table {{ background-color: {SURFACE_BG} !important; color: {FG}; }}
        .q-table th {{ color: {DIM}; }}
        .q-table td {{ color: {FG}; }}
        .q-field__control {{ background-color: {SURFACE_BG} !important; }}
        .q-field__native {{ color: {FG}; }}
        .q-btn {{ text-transform: none; }}
        .q-badge {{ font-weight: 500; }}
        .q-timeline__title {{ color: {FG}; }}
        .q-timeline__subtitle {{ color: {DIM}; }}
        .text-dim {{ color: {DIM}; }}
        .text-success {{ color: {SUCCESS}; }}
        .text-error {{ color: {ERROR}; }}
        .text-accent {{ color: {ACCENT}; }}
        .border-custom {{ border: 1px solid {BORDER}; }}
        .bg-surface {{ background-color: {SURFACE_BG}; }}
        .mono {{ font-family: 'Consolas', 'Courier New', monospace; }}
    """)
