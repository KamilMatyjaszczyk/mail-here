from __future__ import annotations

from mailklient.mail import ImapFolder, ImapMessageHeader
from mailklient.mail.config import (
    ImapSettings,
    MailAccountSettings,
    SmtpSettings,
)
from mailklient.services import MailStore
from mailklient.services import mail_sync
from mailklient.services.mail_sync import HeaderSyncResult, MailSyncService


class FakeImapClient:
    def __init__(self, _settings: ImapSettings) -> None:
        self.connection_tested = False

    def test_connection(self) -> bool:
        self.connection_tested = True
        return True

    def list_folders(self) -> list[ImapFolder]:
        return [ImapFolder(name="INBOX")]

    def fetch_headers(
        self,
        folder_name: str,
        limit: int = 25,
    ) -> list[ImapMessageHeader]:
        return [
            ImapMessageHeader(
                uid="42",
                flags=("\\Seen",),
                message_id="<first@example.com>",
                subject=f"Fra {folder_name}",
                sender="sender@example.com",
                recipients="privat@example.com",
                date="2027-01-01T12:00:00+00:00",
            )
        ][:limit]


class FakeSmtpClient:
    def __init__(self, _settings: SmtpSettings) -> None:
        self.connection_tested = False

    def test_connection(self) -> bool:
        self.connection_tested = True
        return True


def _settings_for(account_id: int) -> MailAccountSettings:
    return MailAccountSettings(
        account_id=account_id,
        email_address="privat@example.com",
        imap=ImapSettings("imap.example.com", 993, "privat@example.com", "hemmelig"),
        smtp=SmtpSettings("smtp.example.com", 465, "privat@example.com", "hemmelig"),
    )


def test_mail_sync_service_tests_imap_and_smtp_connections(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSyncService(
        store,
        imap_client_class=FakeImapClient,
        smtp_client_class=FakeSmtpClient,
    )

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    assert service.test_imap_connection(account.id)
    assert service.test_smtp_connection(account.id)


def test_mail_sync_service_returns_false_when_settings_are_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    service = MailSyncService(store)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: None,
    )

    assert not service.test_imap_connection(999)
    assert not service.test_smtp_connection(999)


def test_mail_sync_service_fetches_headers_into_store(tmp_path, monkeypatch) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSyncService(store, imap_client_class=FakeImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    result = service.fetch_imap_headers(account.id, limit_per_folder=10)
    folders = store.list_folders(account.id)
    messages = store.list_messages(account.id, folders[0].id)

    assert result == HeaderSyncResult(folders_seen=1, messages_seen=1)
    assert folders[0].name == "INBOX"
    assert messages[0].message_id == "<first@example.com>"
    assert messages[0].imap_uid == "42"
    assert messages[0].flags == "\\Seen"
    assert messages[0].is_read is True
    assert messages[0].subject == "Fra INBOX"
    assert messages[0].sender == "sender@example.com"


def test_mail_sync_service_does_not_duplicate_existing_headers(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSyncService(store, imap_client_class=FakeImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    service.fetch_imap_headers(account.id)
    service.fetch_imap_headers(account.id)

    folder = store.list_folders(account.id)[0]
    assert len(store.list_messages(account.id, folder.id)) == 1
