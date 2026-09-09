from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from test_mail_clients import FakeImapConnection, FakeSmtpConnection
from test_mail_sync import _settings_for

from mailklient.mail import ImapClient, ImapFolder, ImapMessageHeader
from mailklient.mail.config import ImapSettings
from mailklient.services import (
    ComposeDraft,
    MailStore,
    MailSyncService,
    SendResult,
    mail_sync,
)
from mailklient.services.drafts import DraftService
from mailklient.ui.compose_dialog import ComposeDialog
from mailklient.ui.draft_dialog import DraftDialog
from mailklient.ui.main_window import MainWindow
from mailklient.workers.mail_send_worker import MailSendWorker


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class GenerationConnection(FakeImapConnection):
    def response(self, name):
        assert name == "UIDVALIDITY"
        return name, [b"12"]


def test_stale_uid_blocks_move_before_any_mutation():
    connections = []

    def factory(*args, **kwargs):
        connection = GenerationConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user", "secret"), factory
    )
    client.expect_uidvalidity("INBOX", 11)
    with pytest.raises(ValueError, match="changed"):
        client.move_message("INBOX", "42", "Archive")
    assert connections[0].uid_requests == []
    assert not connections[0].expunged


def test_missing_move_never_falls_back_to_expunge():
    connection = FakeImapConnection("imap.example.com", 993)
    connection.capabilities = ("IMAP4rev1", "UIDPLUS")
    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user", "secret"),
        lambda *a, **k: connection,
    )
    with pytest.raises(ValueError, match="UID MOVE"):
        client.move_message("INBOX", "42", "Archive")
    assert connection.uid_requests == []
    assert not connection.expunged


def test_rejected_move_never_copies_or_expunges():
    connection = FakeImapConnection("imap.example.com", 993)
    calls = []

    def reject(command, *args):
        calls.append(command)
        return "NO", [b"cannot move"]

    connection.uid = reject
    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user", "secret"),
        lambda *a, **k: connection,
    )
    assert not client.move_message("INBOX", "42", "Archive")
    assert calls == ["MOVE"] and not connection.expunged


def test_oauth_smtp_ehlo_precedes_auth():
    from mailklient.mail import SmtpClient
    from mailklient.mail.config import SmtpSettings

    events = []

    class StrictSmtp(FakeSmtpConnection):
        def starttls(self, **kwargs):
            events.append("TLS")

        def ehlo(self):
            events.append("EHLO")

        def auth(self, *args, **kwargs):
            assert events == ["TLS", "EHLO"]
            events.append("AUTH")

        def send_message(self, message, **kwargs):
            assert events == ["TLS", "EHLO", "AUTH"]
            events.append("SEND")
            return {}

    client = SmtpClient(
        SmtpSettings(
            "smtp-mail.outlook.com",
            587,
            "user@hotmail.com",
            "token",
            auth_method="oauth2",
        ),
        connection_factory=StrictSmtp,
    )
    assert client.send_message("user@hotmail.com", ["user@hotmail.com"], "Test", "Body")
    assert events[-1] == "SEND"


def test_outlook_does_not_duplicate_server_sent_copy(tmp_path, monkeypatch):
    from mailklient.services import MailSendService, mail_send
    from mailklient.ui.main_window import _send_status_text

    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Outlook", "user@hotmail.com", oauth_provider="outlook")
    monkeypatch.setattr(
        mail_send, "get_mail_account_settings", lambda *a: _settings_for(account.id)
    )

    class Accepted:
        def __init__(self, settings):
            pass

        def send_message(self, **kwargs):
            return True

    def no_append(settings):
        pytest.fail("Outlook.com already saves Sent")

    result = MailSendService(store, Accepted, no_append).send_draft(
        ComposeDraft(account.id, recipients="user@hotmail.com")
    )
    assert result.sent and result.local_copy_deferred
    assert not result.server_copy_attempted
    assert not store.list_folders(account.id)
    assert "during sync" in _send_status_text(result)


def test_backfill_uses_uid_range_and_peek():
    connection = GenerationConnection("imap.example.com", 993)
    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user", "secret"),
        lambda *a, **k: connection,
    )
    client.expect_uidvalidity("INBOX", 12)
    assert len(client.fetch_headers_before_uid("INBOX", 103, limit=2)) == 2
    assert connection.uid_requests == [
        ("SEARCH", (None, "UID 1:102")),
        ("FETCH", ("101,102", "(UID FLAGS BODY.PEEK[])")),
    ]
    assert client.fetch_headers_before_uid("INBOX", 1) == []


class GenerationClient:
    generation = 11

    def __init__(self, settings):
        self.expected = None

    def list_folders(self):
        return [ImapFolder("INBOX")]

    def folder_uidvalidity(self, name):
        return self.generation

    def expect_uidvalidity(self, name, value):
        self.expected = value

    def fetch_headers(self, name, limit):
        assert self.expected == self.generation
        return [
            ImapMessageHeader(
                uid="5", subject=f"Generation {self.generation}", body_text="text"
            )
        ]

    def fetch_headers_since_uid(self, name, since, limit):
        assert since == 5
        return []

    def fetch_headers_before_uid(self, name, before, limit):
        return [ImapMessageHeader(uid="2", subject="Older")] if before == 5 else []

    def list_uids(self, name):
        return ["2", "5"]


def test_backfill_and_uidvalidity_reset(tmp_path, monkeypatch):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Account", "user@example.com")
    monkeypatch.setattr(
        mail_sync, "get_mail_account_settings", lambda *a: _settings_for(account.id)
    )
    monkeypatch.setattr(GenerationClient, "generation", 11)
    sync = MailSyncService(store, GenerationClient)
    sync.fetch_imap_headers(account.id)
    folder = store.list_folders(account.id)[0]
    store.add_message(account.id, folder.id, subject="Local copy")
    sync.fetch_imap_headers(account.id, fetch_older=True)
    assert store.oldest_folder_uid(account.id, folder.id) == 2
    assert store.get_folder_last_seen_uid(account.id, folder.id) == 5
    sync.fetch_imap_headers(account.id, fetch_older=True)
    assert store.count_messages(account.id, folder.id) == 3
    monkeypatch.setattr(GenerationClient, "generation", 12)
    sync.fetch_imap_headers(account.id)
    messages = store.list_messages(account.id, folder.id)
    assert {m.subject for m in messages} == {"Local copy", "Generation 12"}
    assert store.folder_uidvalidity(account.id, folder.id) == 12


def test_cross_account_move_rejected_before_network(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    one, two = (
        store.add_account("One", "one@example.com"),
        store.add_account("Two", "two@example.com"),
    )
    source, target = (
        store.add_folder(one.id, "INBOX"),
        store.add_folder(two.id, "INBOX"),
    )
    message = store.add_message(one.id, source.id, imap_uid="1")
    with pytest.raises(ValueError, match="between accounts"):
        MailSyncService(store).move_message_to_folder(message.id, target.id)
    assert store.get_message(message.id).folder_id == source.id


def test_old_sync_table_is_migrated_without_resetting_existing_cursor(tmp_path):
    import sqlite3

    from mailklient.database import connect

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE folder_sync_state (account_id INTEGER, folder_id INTEGER, last_seen_uid INTEGER, updated_at TEXT, PRIMARY KEY(account_id, folder_id))"
        )
        connection.execute("INSERT INTO folder_sync_state VALUES (1, 1, 42, '')")
    MailStore(path)
    MailStore(path)
    with connect(path) as connection:
        row = connection.execute(
            "SELECT last_seen_uid, uidvalidity FROM folder_sync_state"
        ).fetchone()
        assert tuple(row) == (42, None)
        assert connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_failed_draft_save_keeps_composer_open(tmp_path, app, monkeypatch):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("User", "user@example.com")
    service = DraftService(store)
    dialog = ComposeDialog([account], ComposeDraft(account.id), draft_service=service)
    dialog.show()
    dialog.body_edit.setPlainText("Do not lose this")
    original = service.save

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(service, "save", fail)
    dialog.reject()
    assert dialog.isVisible()
    assert "Could not save" in dialog.save_status.text()
    monkeypatch.setattr(service, "save", original)
    dialog.reject()
    assert not dialog.isVisible()
    assert service.list_drafts()[0].draft.body_text == "Do not lose this"


def test_drafts_survive_restart_and_send_failure(tmp_path, app):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("User", "user@example.com")
    service = DraftService(store)
    source = tmp_path / "note.txt"
    source.write_bytes(b"kept attachment")
    draft = ComposeDraft(
        account.id,
        recipients="other@example.com",
        cc="copy@example.com",
        bcc="hidden@example.com",
        body_text="Keep me",
        attachment_paths=(str(source),),
    )
    id = service.save(draft)
    draft = service.get(id).draft
    source.unlink()
    service.set_state(id, "sending")
    restored = DraftService(MailStore(store.database_path))
    assert restored.list_drafts()[0].draft == draft
    assert restored.list_drafts()[0].state == "sending"

    class Fails:
        def send_draft(self, draft):
            raise TimeoutError("Unknown acceptance")

    worker = MailSendWorker(Fails(), draft, restored, id)
    worker.run()
    assert restored.list_drafts()[0].state == "uncertain"

    class Sends:
        def send_draft(self, draft):
            return SendResult(sent=True)

    MailSendWorker(Sends(), draft, restored, id).run()
    assert restored.list_drafts() == []


def test_composer_saves_on_close_and_reopens(tmp_path, app):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("User", "user@example.com")
    service = DraftService(store)
    dialog = ComposeDialog([account], ComposeDraft(account.id), draft_service=service)
    dialog.subject_edit.setText("Keep this subject")
    dialog.body_edit.setPlainText("Keep this body")
    dialog.reject()
    saved = service.list_drafts()[0]
    assert saved.draft.subject == "Keep this subject"
    assert saved.draft.body_text == "Keep this body"
    picker = DraftDialog(service)
    assert picker.selected().id == saved.id
    reopened = ComposeDialog(
        [account], saved.draft, draft_service=service, draft_id=saved.id
    )
    assert reopened.body_edit.toPlainText() == "Keep this body"
    reopened.reject()
    assert len(service.list_drafts()) == 1
    picker.close()


def test_auto_sync_serializes_accounts_without_changing_selection(tmp_path, app):
    store = MailStore(tmp_path / "cache.sqlite3")
    one = store.add_account_with_default_folders(
        "One", "one@example.com", imap_host="imap.example.com"
    )
    two = store.add_account_with_default_folders(
        "Two", "two@example.com", imap_host="imap.example.com"
    )
    store.add_account("Parked", "user@tuta.io", provider="tuta", imap_host="127.0.0.1")
    calls = []

    class Sync:
        def fetch_imap_headers(self, account_id, **kwargs):
            calls.append(account_id)
            return mail_sync.HeaderSyncResult(1, 0)

    prefs = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = MainWindow(store, mail_sync_service=Sync(), preferences=prefs)
    selection = window.account_list.currentRow()
    try:
        window._queue_auto_sync()
        window._queue_auto_sync()
        deadline = time.monotonic() + 5
        while (
            window._sync_thread is not None or window._sync_queue
        ) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window._sync_thread is None
        assert calls == [one.id, two.id]
        assert window.account_list.currentRow() == selection
        window.auto_sync_action.setChecked(False)
        assert not window._sync_timer.isActive()
    finally:
        if window._sync_thread:
            window._sync_thread.quit()
            window._sync_thread.wait(5000)
            app.processEvents()
        window.close()
