"""Background setup and a bounded first inbox fetch for Tuta."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from mailklient.services.mail_sync import MailSyncService
from mailklient.services.tuta_setup import TutaSetupError, TutaSetupService


class TutaSetupWorker(QObject):
    account_created = Signal(object)
    finished = Signal(int, object)
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        setup: TutaSetupService,
        sync: MailSyncService,
        display_name: str,
        email_address: str,
        password: str | None,
        certificate_path: Path | None,
    ) -> None:
        super().__init__()
        self._setup = setup
        self._sync = sync
        self._display_name = display_name
        self._email_address = email_address
        self._password = password
        self._certificate_path = certificate_path

    @Slot()
    def run(self) -> None:
        account = None
        try:
            account = self._setup.add_account(
                self._display_name,
                self._email_address,
                self._password,
                self._certificate_path,
            )
            self._password = None
            self.account_created.emit(account)
            result = self._sync.fetch_imap_headers(
                account.id,
                limit_per_folder=5,
                message_folder_names=("INBOX",),
            )
            self.finished.emit(account.id, result)
        except TutaSetupError as error:
            self.failed.emit(str(error))
        except Exception:  # noqa: BLE001 - Keep server replies out of the GUI.
            self.failed.emit(
                "The account is saved, but the initial sync failed. Check TutaBridge and try syncing again."
                if account is not None
                else "Could not create the Tuta account."
            )
        finally:
            self._password = None
            self.done.emit()
