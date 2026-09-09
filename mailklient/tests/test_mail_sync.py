from __future__ import annotations

from mailklient.mail import ImapAttachment, ImapFolder, ImapMessageHeader
from mailklient.mail.config import (
    ImapSettings,
    MailAccountSettings,
    SmtpSettings,
)
from mailklient.services import MailStore
from mailklient.services import mail_sync
from mailklient.services.mail_sync import HeaderSyncResult, MailSyncService
from mailklient.mail.imap_client import _parse_folder


class FakeImapClient:
    instances: list[FakeImapClient] = []

    def __init__(self, _settings: ImapSettings) -> None:
        self.connection_tested = False
        self.fetched_folders: list[str] = []
        self.fetched_since_uid: list[tuple[str, int, int]] = []
        self.fetched_flags: list[tuple[str, int]] = []
        self.moved_messages: list[tuple[str, str, str]] = []
        self.archived_messages: list[tuple[str, str]] = []
        self.gmail_archived_messages: list[tuple[str, str]] = []
        self.instances.append(self)

    def test_connection(self) -> bool:
        self.connection_tested = True
        return True

    def list_folders(self) -> list[ImapFolder]:
        return [
            ImapFolder(name="INBOX"),
            ImapFolder(name="[Gmail]", flags=("\\Noselect",)),
            ImapFolder(name="[Gmail]/All Mail"),
        ]

    def fetch_headers(
        self,
        folder_name: str,
        limit: int = 25,
    ) -> list[ImapMessageHeader]:
        self.fetched_folders.append(folder_name)
        return [
            ImapMessageHeader(
                uid="42",
                flags=("\\Seen",),
                message_id="<first@example.com>",
                subject=f"Fra {folder_name}",
                sender="sender@example.com",
                recipients="privat@example.com",
                date="2027-01-01T12:00:00+00:00",
                body_text=f"Full tekst fra {folder_name}",
                body_html=f"<p>Full HTML fra {folder_name}</p>",
                body_preview=f"Full tekst fra {folder_name}",
                attachments=(
                    ImapAttachment(
                        filename="rapport.pdf",
                        content_type="application/pdf",
                        size=1234,
                        content=b"pdf-bytes",
                    ),
                ),
            )
        ][:limit]

    def fetch_headers_since_uid(
        self,
        folder_name: str,
        last_seen_uid: int,
        limit: int = 25,
    ) -> list[ImapMessageHeader]:
        self.fetched_since_uid.append((folder_name, last_seen_uid, limit))
        return []

    def list_uids(self, _folder_name: str) -> list[str]:
        return ["42"]

    def fetch_recent_flags(self, folder_name: str, limit: int):
        self.fetched_flags.append((folder_name, limit))
        return []

    def move_message(
        self,
        folder_name: str,
        message_uid: str,
        destination_folder_name: str,
    ) -> bool:
        self.moved_messages.append(
            (folder_name, message_uid, destination_folder_name)
        )
        return True

    def archive_message(self, folder_name: str, message_uid: str) -> bool:
        self.archived_messages.append((folder_name, message_uid))
        return True

    def archive_gmail_message(self, folder_name: str, message_uid: str) -> bool:
        self.gmail_archived_messages.append((folder_name, message_uid))
        return True


class FakeSmtpClient:
    def __init__(self, _settings: SmtpSettings) -> None:
        self.connection_tested = False

    def test_connection(self) -> bool:
        self.connection_tested = True
        return True


class FlagRecordingImapClient(FakeImapClient):
    instances: list[FlagRecordingImapClient] = []

    def __init__(self, settings: ImapSettings) -> None:
        super().__init__(settings)
        self.seen_updates: list[tuple[str, str, bool]] = []
        self.instances.append(self)

    def set_seen(self, folder_name: str, message_uid: str, is_read: bool) -> bool:
        self.seen_updates.append((folder_name, message_uid, is_read))
        return True


class RecordingImapClient(FakeImapClient):
    instances: list[RecordingImapClient] = []

    def __init__(self, settings: ImapSettings) -> None:
        super().__init__(settings)
        self.instances.append(self)


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


def test_outlook_sync_repairs_bad_remote_ids_and_populates_unified_inbox(
    tmp_path, monkeypatch
):
    class OutlookClient(FakeImapClient):
        def list_folders(self):
            return [_parse_folder(line) for line in (
                b'(\\HasNoChildren) "/" Inbox',
                b'(\\HasNoChildren \\Sent) "/" Sent',
                b'(\\HasChildren \\Trash) "/" Deleted',
                b'(\\HasNoChildren \\Junk) "/" Junk',
                b'(\\Noselect) "/" Parent',
                b'(\\HasNoChildren) "/" Unrelated',
            )]

        def fetch_headers(self, folder_name, limit=25):
            assert folder_name in {"Inbox", "Sent", "Deleted", "Junk"}
            return super().fetch_headers(folder_name, limit)

    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders(
        "Outlook", "user@hotmail.com", auth_method="oauth2", oauth_provider="outlook"
    )
    inbox = store.get_or_add_folder(account.id, "Innboks", remote_id="/")
    trash = store.get_or_add_folder(account.id, "Papirkurv", remote_id="/")
    monkeypatch.setattr(
        mail_sync, "get_mail_account_settings", lambda *_args: _settings_for(account.id)
    )
    result = MailSyncService(store, OutlookClient).fetch_imap_headers(account.id)
    assert result == HeaderSyncResult(folders_seen=4, messages_seen=4)
    assert store.get_folder(inbox.id).remote_id == "Inbox"
    assert store.get_folder(trash.id).remote_id == "Deleted"
    messages = store.list_unified_inbox_messages()
    assert len(messages) == 1
    assert messages[0].subject == "Fra Inbox"
    assert messages[0].body_text == "Full tekst fra Inbox"


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
    FakeImapClient.instances = []
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
    inbox = next(folder for folder in folders if folder.name == "Innboks")
    messages = store.list_messages(account.id, inbox.id)

    assert result == HeaderSyncResult(folders_seen=1, messages_seen=1)
    assert {folder.name for folder in folders} == {"Innboks"}
    assert messages[0].message_id == "<first@example.com>"
    assert messages[0].imap_uid == "42"
    assert messages[0].flags == "\\Seen"
    assert messages[0].is_read is True
    assert messages[0].subject == "Fra INBOX"
    assert messages[0].sender == "sender@example.com"
    assert messages[0].body_text == "Full tekst fra INBOX"
    assert messages[0].body_html == "<p>Full HTML fra INBOX</p>"
    assert messages[0].body_preview == "Full tekst fra INBOX"
    assert store.list_attachments(messages[0].id)[0].filename == "rapport.pdf"
    attachment = store.list_attachments(messages[0].id)[0]
    assert attachment.has_content is True
    assert store.get_attachment_content(attachment.id) == b"pdf-bytes"
    assert FakeImapClient.instances[0].fetched_flags == [("INBOX", 250)]


def test_mail_sync_service_does_not_duplicate_existing_headers(
    tmp_path,
    monkeypatch,
) -> None:
    FakeImapClient.instances = []
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

    inbox = next(
        folder for folder in store.list_folders(account.id) if folder.name == "Innboks"
    )
    assert len(store.list_messages(account.id, inbox.id)) == 1
    assert FakeImapClient.instances[1].fetched_folders == []
    assert FakeImapClient.instances[1].fetched_since_uid == [("INBOX", 42, 25)]


def test_mail_sync_service_removes_messages_missing_from_remote_folder(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    stale_message = store.add_message(
        account.id,
        inbox.id,
        imap_uid="41",
        subject="Borte",
    )
    service = MailSyncService(store, imap_client_class=FakeImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    service.fetch_imap_headers(account.id)

    assert store.get_message(stale_message.id) is None


def test_mail_sync_service_updates_folder_last_seen_uid(
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

    inbox = next(
        folder for folder in store.list_folders(account.id) if folder.name == "Innboks"
    )
    assert store.get_folder_last_seen_uid(account.id, inbox.id) == 42


def test_mail_sync_service_syncs_only_inbox_by_default(tmp_path, monkeypatch) -> None:
    RecordingImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSyncService(store, imap_client_class=RecordingImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    result = service.fetch_imap_headers(account.id)

    assert result == HeaderSyncResult(folders_seen=1, messages_seen=1)
    assert RecordingImapClient.instances[0].fetched_folders == ["INBOX"]


def test_mail_sync_service_can_sync_all_folders_when_requested(
    tmp_path,
    monkeypatch,
) -> None:
    RecordingImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSyncService(store, imap_client_class=RecordingImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    result = service.fetch_imap_headers(account.id, message_folder_names=None)

    assert result == HeaderSyncResult(folders_seen=2, messages_seen=2)
    assert RecordingImapClient.instances[0].fetched_folders == [
        "INBOX",
        "[Gmail]/All Mail",
    ]


def test_mail_sync_service_marks_message_read_locally_without_remote_uid(
    tmp_path,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(account.id, inbox.id, subject="Hei", is_read=False)
    service = MailSyncService(store)

    assert service.mark_message_read(message.id)

    assert store.get_message(message.id).is_read is True


def test_mail_sync_service_marks_message_read_on_imap(
    tmp_path,
    monkeypatch,
) -> None:
    FlagRecordingImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX", remote_id="INBOX")
    message = store.add_message(
        account.id,
        inbox.id,
        imap_uid="42",
        subject="Hei",
        is_read=False,
    )
    service = MailSyncService(store, imap_client_class=FlagRecordingImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    assert service.mark_message_read(message.id)

    assert FlagRecordingImapClient.instances[0].seen_updates == [("INBOX", "42", True)]
    assert store.get_message(message.id).is_read is True


def test_mail_sync_service_moves_message_to_trash_on_imap(
    tmp_path,
    monkeypatch,
) -> None:
    FakeImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
    )
    inbox = store.add_folder(account.id, "INBOX", remote_id="INBOX")
    message = store.add_message(
        account.id,
        inbox.id,
        imap_uid="42",
        subject="Hei",
    )
    service = MailSyncService(store, imap_client_class=FakeImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    assert service.move_message_to_trash(message.id)

    moved_message = store.get_message(message.id)
    assert moved_message is not None
    trash = store.get_folder(moved_message.folder_id)
    assert trash is not None
    assert trash.name == "Papirkurv"
    assert trash.remote_id == "[Gmail]/Trash"
    assert FakeImapClient.instances[0].moved_messages == [
        ("INBOX", "42", "[Gmail]/Trash")
    ]


def test_mail_sync_service_archives_message_on_imap(
    tmp_path,
    monkeypatch,
) -> None:
    FakeImapClient.instances = []
    monkeypatch.setattr(FakeImapClient, "list_folders", lambda self: [ImapFolder("Archive", ("\\Archive",))])
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX", remote_id="INBOX")
    message = store.add_message(
        account.id,
        inbox.id,
        imap_uid="42",
        subject="Hei",
    )
    service = MailSyncService(store, imap_client_class=FakeImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    assert service.archive_message(message.id)

    moved_message = store.get_message(message.id)
    assert moved_message is not None
    archive = store.get_folder(moved_message.folder_id)
    assert archive is not None
    assert archive.name == "Arkiv"
    assert FakeImapClient.instances[-1].moved_messages == [("INBOX", "42", "Archive")]
    assert moved_message.imap_uid is None


def test_mail_sync_service_archives_gmail_by_removing_inbox_label(
    tmp_path,
    monkeypatch,
) -> None:
    FakeImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Gmail",
        "gmail@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
    )
    inbox = store.add_folder(account.id, "INBOX", remote_id="INBOX")
    all_mail = store.add_folder(
        account.id,
        "All e-post",
        remote_id="[Gmail]/All Mail",
    )
    message = store.add_message(
        account.id,
        inbox.id,
        imap_uid="42",
        subject="Hei",
    )
    service = MailSyncService(store, imap_client_class=FakeImapClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    assert service.archive_message(message.id)

    moved_message = store.get_message(message.id)
    assert moved_message is not None
    assert moved_message.folder_id == all_mail.id
    assert FakeImapClient.instances[0].gmail_archived_messages == [("INBOX", "42")]
    assert FakeImapClient.instances[0].archived_messages == []


def test_mail_sync_service_uses_gmail_special_use_folder_names(
    tmp_path,
    monkeypatch,
) -> None:
    class GmailFoldersClient(FakeImapClient):
        instances: list[GmailFoldersClient] = []

        def __init__(self, settings: ImapSettings) -> None:
            super().__init__(settings)
            self.instances.append(self)

        def list_folders(self) -> list[ImapFolder]:
            return [
                ImapFolder(name="INBOX", flags=("\\HasNoChildren",)),
                ImapFolder(name="[Gmail]/Sent Mail", flags=("\\Sent",)),
                ImapFolder(name="[Gmail]/Trash", flags=("\\Trash",)),
                ImapFolder(name="[Gmail]/All Mail", flags=("\\All",)),
            ]

        def fetch_headers(
            self,
            folder_name: str,
            limit: int = 25,
        ) -> list[ImapMessageHeader]:
            self.fetched_folders.append(folder_name)
            return []

    GmailFoldersClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Gmail",
        "gmail@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
    )
    service = MailSyncService(store, imap_client_class=GmailFoldersClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    service.fetch_imap_headers(account.id, message_folder_names=None)

    folders = store.list_folders(account.id)
    assert {
        (folder.name, folder.remote_id)
        for folder in folders
    } == {
        ("Innboks", "INBOX"),
        ("Sendt", "[Gmail]/Sent Mail"),
        ("Papirkurv", "[Gmail]/Trash"),
        ("All e-post", "[Gmail]/All Mail"),
    }


def test_mail_sync_service_refreshes_recent_flags_without_full_body(
    tmp_path,
    monkeypatch,
) -> None:
    class FlagRefreshClient(FakeImapClient):
        instances: list[FlagRefreshClient] = []

        def __init__(self, settings: ImapSettings) -> None:
            super().__init__(settings)
            self.instances.append(self)

        def fetch_headers_since_uid(
            self,
            folder_name: str,
            last_seen_uid: int,
            limit: int = 25,
        ) -> list[ImapMessageHeader]:
            self.fetched_since_uid.append((folder_name, last_seen_uid, limit))
            return []

        def fetch_recent_flags(self, folder_name: str, limit: int):
            self.fetched_flags.append((folder_name, limit))
            return [mail_sync.ImapMessageFlags(uid="42", flags=("\\Seen",))]

    FlagRefreshClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX", remote_id="INBOX")
    message = store.add_message(
        account.id,
        inbox.id,
        imap_uid="42",
        subject="Hei",
        is_read=False,
    )
    store.update_folder_last_seen_uid(account.id, inbox.id, 42)
    service = MailSyncService(store, imap_client_class=FlagRefreshClient)

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda _store, _account_id: _settings_for(account.id),
    )

    service.fetch_imap_headers(account.id)

    refreshed = store.get_message(message.id)
    assert refreshed is not None
    assert refreshed.is_read is True
    assert refreshed.flags == "\\Seen"
    assert FlagRefreshClient.instances[0].fetched_flags == [("INBOX", 250)]
