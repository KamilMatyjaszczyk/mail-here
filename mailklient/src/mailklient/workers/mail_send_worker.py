"""Qt workers for background SMTP sending."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from mailklient.services import ComposeDraft, MailSendService


class MailSendWorker(QObject):
    """Send one draft outside the GUI thread."""

    finished = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        mail_send_service: MailSendService,
        draft: ComposeDraft,
    ) -> None:
        super().__init__()
        self._mail_send_service = mail_send_service
        self._draft = draft

    @Slot()
    def run(self) -> None:
        """Send the draft and notify the GUI."""
        try:
            sent = self._mail_send_service.send_draft(self._draft)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.finished.emit(sent)
        finally:
            self.done.emit()
