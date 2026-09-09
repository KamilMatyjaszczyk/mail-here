"""Qt workers for background mail synchronization."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, Signal, Slot

from mailklient.services import (
    CORE_SYNC_FOLDER_NAMES,
    HeaderSyncResult,
    MailSyncService,
)


class MailSyncWorker(QThread):
    """Run one account sync outside the GUI thread."""

    synced = Signal(int, object)
    failed = Signal(int, str)
    cancelled = Signal(int)
    progress = Signal(int, str)
    done = Signal()

    def __init__(
        self,
        mail_sync_service: MailSyncService,
        account_id: int,
        *,
        limit_per_folder: int = 25,
        folder_names: tuple[str, ...] | None = None,
        message_folder_names: tuple[str, ...] | None = CORE_SYNC_FOLDER_NAMES,
        fetch_older: bool = False,
        timeout_seconds: float = 300,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._mail_sync_service = mail_sync_service
        self._account_id = account_id
        self._limit_per_folder = limit_per_folder
        self._folder_names = folder_names
        self._message_folder_names = message_folder_names
        self._fetch_older = fetch_older
        self._timeout_seconds = timeout_seconds
        self._started_at = 0.0

    def _report_progress(self, message: str) -> None:
        if self.isInterruptionRequested():
            raise InterruptedError("Sync cancelled.")
        if time.monotonic() - self._started_at >= self._timeout_seconds:
            raise TimeoutError(
                "Sync timed out and was stopped. "
                "Previously saved messages are kept. Try syncing again."
            )
        self.progress.emit(self._account_id, message)

    @Slot()
    def run(self) -> None:
        """Run the sync and notify the GUI about the result."""
        self._started_at = time.monotonic()
        try:
            self._report_progress("Starting sync...")
            extra = {"fetch_older": True} if self._fetch_older else {}
            result: HeaderSyncResult = self._mail_sync_service.fetch_imap_headers(
                self._account_id,
                limit_per_folder=self._limit_per_folder,
                folder_names=self._folder_names,
                message_folder_names=self._message_folder_names,
                progress=self._report_progress,
                **extra,
            )
            self._report_progress("Sync complete.")
        except InterruptedError:
            self.cancelled.emit(self._account_id)
        except Exception as error:
            self.failed.emit(self._account_id, str(error) or type(error).__name__)
        else:
            self.synced.emit(self._account_id, result)
        finally:
            self.done.emit()
