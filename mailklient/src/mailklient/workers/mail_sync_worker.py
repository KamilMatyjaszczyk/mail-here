"""Qt workers for background mail synchronization."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from mailklient.services import (
    CORE_SYNC_FOLDER_NAMES,
    HeaderSyncResult,
    MailSyncService,
)


class MailSyncWorker(QObject):
    """Run one account sync outside the GUI thread."""

    finished = Signal(int, object)
    failed = Signal(int, str)
    done = Signal()

    def __init__(
        self,
        mail_sync_service: MailSyncService,
        account_id: int,
        *,
        limit_per_folder: int = 25,
        folder_names: tuple[str, ...] | None = None,
        message_folder_names: tuple[str, ...] | None = CORE_SYNC_FOLDER_NAMES,
    ) -> None:
        super().__init__()
        self._mail_sync_service = mail_sync_service
        self._account_id = account_id
        self._limit_per_folder = limit_per_folder
        self._folder_names = folder_names
        self._message_folder_names = message_folder_names

    @Slot()
    def run(self) -> None:
        """Run the sync and notify the GUI about the result."""
        try:
            result: HeaderSyncResult = self._mail_sync_service.fetch_imap_headers(
                self._account_id,
                limit_per_folder=self._limit_per_folder,
                folder_names=self._folder_names,
                message_folder_names=self._message_folder_names,
            )
        except Exception as error:
            self.failed.emit(self._account_id, str(error))
        else:
            self.finished.emit(self._account_id, result)
        finally:
            self.done.emit()
