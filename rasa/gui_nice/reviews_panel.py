"""Reviews panel — human-in-the-loop review queue with respond UI."""

from __future__ import annotations

from nicegui import ui

from rasa.gui_nice.api_client import ApiClient
from rasa.gui_nice.theme import DIM, SUCCESS, WARNING


class ReviewsPanel:
    """Human-in-the-loop review queue — pending + answered history."""

    def __init__(self, api: ApiClient):
        self.api = api
        self._pending_container: ui.column | None = None
        self._history_container: ui.column | None = None
        self._pending_count: ui.label | None = None
        self._error_label: ui.label | None = None
        # Respond dialog refs (set in build, read in handlers)
        self._respond_dialog: ui.dialog | None = None
        self._dialog_reason: ui.label | None = None
        self._dialog_context: ui.label | None = None
        self._dialog_input: ui.textarea | None = None
        # Active review being responded to
        self._active_review: dict = {}

    def build(self):
        with ui.column().classes('w-full gap-2'):
            self._error_label = ui.label("").classes('text-error text-sm')

            with ui.row().classes('w-full items-center gap-2'):
                ui.label("Pending Reviews").classes('text-sm font-bold')
                self._pending_count = ui.label("0").classes(
                    'text-xs mono px-2 py-1 rounded-full'
                ).style(f'background-color: {WARNING}; color: #000;')

            self._pending_container = ui.column().classes('w-full gap-3')

            ui.separator().classes('my-2')

            with ui.row().classes('w-full items-center gap-2'):
                ui.label("Review History").classes('text-sm font-bold')
            self._history_container = ui.column().classes('w-full gap-1')

        # ── Respond dialog — built once at panel level, never recreated ──
        with ui.dialog() as self._respond_dialog:
            with ui.card().classes('w-[500px]'):
                with ui.column().classes('w-full gap-2'):
                    ui.label("Respond to Review").classes('text-lg font-bold')
                    self._dialog_reason = ui.label("").classes('text-sm text-dim')
                    self._dialog_context = ui.label("").classes('text-xs text-dim')
                    self._dialog_input = ui.textarea(
                        placeholder="Type your guidance, approval, or instructions...",
                    ).classes('w-full').props("outlined dense autofocus")

                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button("Cancel", on_click=self._dialog_close).props("size=sm")
                        ui.button(
                            "Approve", color="positive", icon="check",
                            on_click=self._dialog_approve,
                        ).props("size=sm")
                        ui.button(
                            "Deny / Request Changes", color="negative", icon="block",
                            on_click=self._dialog_deny,
                        ).props("size=sm")

        # DEBUG: test button at panel level (outside refresh cycle)
        ui.button("DEBUG: Test Dialog", icon="bug_report",
                  on_click=self._debug_test).props("size=sm")

        ui.timer(10.0, self._refresh)

    # ── Dialog handlers (simple method refs, not lambdas) ──

    def _debug_test(self):
        """Debug: test if dialog opens and handlers work."""
        print("DEBUG: test button clicked", flush=True)
        ui.notify("DEBUG: test clicked", color="positive")
        if self._respond_dialog:
            self._active_review = {"id": "debug", "reason": "Debug test review"}
            if self._dialog_reason:
                self._dialog_reason.text = "Debug: test review reason"
            if self._dialog_input:
                self._dialog_input.value = ""
            if self._dialog_context:
                self._dialog_context.visible = False
            self._respond_dialog.open()
        else:
            ui.notify("DEBUG: dialog is None!", color="negative")

    def _dialog_close(self):
        if self._respond_dialog:
            self._respond_dialog.close()
        self._active_review = {}

    def _dialog_approve(self):
        self._do_respond("Approved.")

    def _dialog_deny(self):
        self._do_respond("Changes requested.")

    def _do_respond(self, fallback: str):
        rid = self._active_review.get("id")
        if not rid:
            return
        text = (self._dialog_input.value or "").strip() if self._dialog_input else ""
        if not text:
            text = fallback
        if self._respond_dialog:
            self._respond_dialog.close()
        self._active_review = {}
        # Fire and forget the async API call — timer will refresh the list
        ui.timer(0.1, lambda: self._submit(rid, text), once=True)

    async def _submit(self, review_id: str, text: str):
        result = await self.api.respond_to_review(review_id, text, "dashboard")
        if result.ok:
            ui.notify("Response submitted.", color="positive")
            await self._refresh()
        else:
            ui.notify(f"Failed: {result.error}", color="negative")

    def _open_respond(self, review: dict):
        """Populate the panel-level dialog and open it."""
        self._active_review = review
        if self._dialog_reason:
            self._dialog_reason.text = review.get("reason", "")
        if self._dialog_input:
            self._dialog_input.value = ""
        payload = review.get("payload", {})
        has_payload = bool(payload and isinstance(payload, dict) and len(payload) > 0)
        if self._dialog_context:
            if has_payload:
                self._dialog_context.set_text(
                    "\n".join(f"{k}: {v}" for k, v in payload.items())
                )
                self._dialog_context.visible = True
            else:
                self._dialog_context.visible = False
        if self._respond_dialog:
            self._respond_dialog.open()

    def _make_respond_handler(self, review):
        """Factory to avoid lambda-in-loop issues with NiceGUI event system."""
        def handler():
            self._open_respond(review)
        return handler

    # ── Timer refresh ──

    async def _refresh(self):
        pending_result = await self.api.get_reviews(limit=20, status="pending")
        if not pending_result.ok:
            if self._error_label:
                self._error_label.text = f"Failed: {pending_result.error}"
            return
        if self._error_label:
            self._error_label.text = ""

        data = pending_result.data
        pending_reviews = data.get("reviews", []) if isinstance(data, dict) else []
        pending_count = data.get("pending_count", len(pending_reviews))

        if self._pending_count:
            self._pending_count.text = str(pending_count)

        self._render_pending(pending_reviews)

        history_result = await self.api.get_reviews(limit=10, status="answered")
        if history_result.ok:
            hist_data = history_result.data
            history = hist_data.get("reviews", []) if isinstance(hist_data, dict) else []
            self._render_history(history)

    def _render_pending(self, reviews: list[dict]):
        if not self._pending_container:
            return
        self._pending_container.clear()
        if not reviews:
            with self._pending_container:
                ui.label("No pending reviews.").classes('text-dim text-sm')
            return

        for rev in reviews:
            rid = rev["id"]
            # Capture the review dict for the button handler
            with self._pending_container:
                with ui.card().classes('w-full'):
                    with ui.column().classes('w-full gap-2'):
                        with ui.row().classes('w-full items-center gap-2'):
                            ui.badge("Pending", color=WARNING).props("size=sm")
                            ui.label(f"Review {rid[:8]}...").classes('text-xs mono text-dim')

                        ui.label(rev.get("reason", "")).classes('text-sm')

                        payload = rev.get("payload", {})
                        if payload and isinstance(payload, dict) and len(payload) > 0:
                            with ui.expansion("Context", icon="info").classes('w-full'):
                                ui.code(
                                    "\n".join(f"{k}: {v}" for k, v in payload.items()),
                                ).classes('text-xs')

                        # Use factory function instead of lambda for NiceGUI compat
                        handler = self._make_respond_handler(rev)
                        ui.button(
                            "Respond", icon="rate_review",
                            on_click=handler,
                        ).props("size=sm outline")

    def _render_history(self, reviews: list[dict]):
        if not self._history_container:
            return
        self._history_container.clear()
        if not reviews:
            with self._history_container:
                ui.label("No completed reviews yet.").classes('text-dim text-sm')
            return
        for rev in reviews:
            with self._history_container:
                with ui.row().classes('w-full items-center gap-2 text-xs mono'):
                    ui.badge(rev.get("status", "?"),
                             color=SUCCESS if rev["status"] == "answered" else DIM).props("size=sm")
                    reason = rev.get("reason", "")[:80]
                    resp = rev.get("response", "")[:60]
                    ui.label(reason).classes('text-white')
                    ui.label(f"→ {resp}").classes('text-dim')
