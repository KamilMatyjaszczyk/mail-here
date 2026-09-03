"""Mail synchronization service."""

from __future__ import annotations

from dataclasses import dataclass

from mailklient.mail import ImapClient, ImapMessageHeader, SmtpClient
from mailklient.services.mail_settings import get_mail_account_settings
from mailklient.services.mail_store import MailStore


@dataclass(frozen=True, slots=True)
class HeaderSyncResult:
    """Summary of an IMAP header sync."""

    folders_seen: int
    messages_seen: int


class MailSyncService:
    """Coordinate mail protocol clients and the local store."""

    def __init__(
        self,
        store: MailStore,
        imap_client_class=ImapClient,
        smtp_client_class=SmtpClient,
    ) -> None:
        self._store = store
        self._imap_client_class = imap_client_class
        self._smtp_client_class = smtp_client_class

    def test_imap_connection(self, account_id: int) -> bool:
        """Test IMAP SSL login for one account."""
        settings = get_mail_account_settings(self._store, account_id)
        if settings is None:
            return False

        return self._imap_client_class(settings.imap).test_connection()

    def test_smtp_connection(self, account_id: int) -> bool:
        """Test SMTP SSL login for one account."""
        settings = get_mail_account_settings(self._store, account_id)
        if settings is None:
            return False

        return self._smtp_client_class(settings.smtp).test_connection()

    def fetch_imap_headers(
        self,
        account_id: int,
        limit_per_folder: int = 25,
    ) -> HeaderSyncResult:
        """Fetch folder names and recent message headers into the local cache."""
        settings = get_mail_account_settings(self._store, account_id)
        if settings is None:
            return HeaderSyncResult(folders_seen=0, messages_seen=0)

        imap_client = self._imap_client_class(settings.imap)
        folders = imap_client.list_folders()
        messages_seen = 0

        for imap_folder in folders:
            folder = self._store.get_or_add_folder(
                account_id,
                imap_folder.name,
                remote_id=imap_folder.name,
            )
            headers = imap_client.fetch_headers(imap_folder.name, limit_per_folder)
            messages_seen += len(headers)

            for header in headers:
                self._store.save_message_metadata(
                    account_id,
                    folder.id,
                    imap_uid=header.uid,
                    flags=" ".join(header.flags),
                    message_id=header.message_id,
                    subject=header.subject,
                    sender=header.sender,
                    recipients=header.recipients,
                    received_at=header.date,
                    is_read=_is_seen(header.flags),
                    body_preview="",
                )

        return HeaderSyncResult(
            folders_seen=len(folders),
            messages_seen=messages_seen,
        )


def _is_seen(flags: tuple[str, ...]) -> bool:
    return any(flag.casefold() == "\\seen" for flag in flags)
