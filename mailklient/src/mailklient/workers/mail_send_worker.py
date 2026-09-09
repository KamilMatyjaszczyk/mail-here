"""Qt workers for background SMTP sending."""

from __future__ import annotations

from contextlib import suppress

from PySide6.QtCore import QObject, Signal, Slot

from mailklient.services import ComposeDraft, MailSendService
from mailklient.services.drafts import DraftService


class MailSendWorker(QObject):
    """Send one draft outside the GUI thread."""

    finished = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        mail_send_service: MailSendService,
        draft: ComposeDraft,
        draft_service: DraftService | None = None,
        draft_id: int | None = None,
    ) -> None:
        super().__init__()
        self._mail_send_service = mail_send_service
        self._draft = draft
        self._draft_service = draft_service
        self._draft_id = draft_id

    @Slot()
    def run(self) -> None:
        """Send the draft and notify the GUI."""
        try:
            sent = self._mail_send_service.send_draft(self._draft)
        except Exception as error:
            self._record_state("uncertain")
            self.failed.emit(str(error))
        else:
            self._record_state("sent" if sent else "draft")
            self.finished.emit(sent)
        finally:
            self.done.emit()

    def _record_state(self, state: str) -> None:
        if self._draft_service is not None and self._draft_id is not None:
            with suppress(Exception):
                self._draft_service.set_state(self._draft_id, state)
