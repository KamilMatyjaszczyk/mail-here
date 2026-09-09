from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from mailklient.database import connect, delete_messages_missing_from_folder
from mailklient.domain import Attachment
from mailklient.mail import ImapClient, ImapFolder, ImapSettings, imap_client
from mailklient.mail.config import ImapAttachment
from mailklient.security.attachment_files import AttachmentWorkspace, save_attachment
from mailklient.services import (
    ComposeDraft,
    MailSendService,
    MailStore,
    MailSyncService,
    mail_sync,
)
from mailklient.services import attachments as attachment_module
from mailklient.services.attachments import AttachmentService
from mailklient.services.drafts import DraftService
from mailklient.ui.compose_dialog import ComposeDialog
from mailklient.ui.main_window import MainWindow
from mailklient.workers.task_runner import TaskRunner


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        QApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Qt operation timed out")
        time.sleep(0.005)


def test_preview_files_are_private_unique_and_cleaned(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    trap = tmp_path / "mailklient-attachments"
    trap.mkdir()
    original = tmp_path / "original.txt"
    original.write_bytes(b"unchanged")
    (trap / "1-report.txt").symlink_to(original)
    workspace = AttachmentWorkspace()
    try:
        first = workspace.write("../report.txt", b"private")
        second = workspace.write("../report.txt", b"second")
        long_name = workspace.write("\u00e9" * 180, b"long filename")
        assert long_name.read_bytes() == b"long filename"
        assert first != second
        assert first.stat().st_mode & 0o777 == 0o600
        assert first.parent.stat().st_mode & 0o777 == 0o700
        assert first.parent.parent.stat().st_mode & 0o777 == 0o700
        assert original.read_bytes() == b"unchanged"
    finally:
        workspace.close()
    assert not first.exists()


def test_save_never_follows_symlink_or_overwrites_unapproved_file(tmp_path):
    original = tmp_path / "original.txt"
    original.write_bytes(b"unchanged")
    destination = tmp_path / "attachment.txt"
    destination.symlink_to(original)
    save_attachment(destination, b"new")
    assert original.read_bytes() == b"unchanged"
    assert destination.read_bytes() == b"new"
    assert not destination.is_symlink()
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_attachment(destination, b"must not overwrite", overwrite=False)
    assert destination.read_bytes() == b"new"
    assert not list(tmp_path.glob(".mailklient-*"))


def test_unknown_charset_and_malformed_mime_do_not_abort_message_batch(monkeypatch):
    parsed = imap_client._parse_message_header(
        "1", b"Content-Type: text/plain; charset=unknown-charset\r\n\r\nHello", ()
    )
    assert parsed.body_text == "Hello"
    assert not parsed.parse_error
    real_extract = imap_client._extract_message_parts

    def broken(message):
        if message["Subject"] == "broken":
            raise ValueError("Malformed MIME")
        return real_extract(message)

    monkeypatch.setattr(imap_client, "_extract_message_parts", broken)
    first = imap_client._parse_message_header("2", b"Subject: broken\r\n\r\n", ())
    second = imap_client._parse_message_header("3", b"Subject: normal\r\n\r\nHi", ())
    assert first.parse_error and first.uid == "2"
    assert second.subject == "normal" and second.body_text == "Hi"


def test_reply_to_survives_sqlite_and_is_used_for_reply(tmp_path):
    header = imap_client._parse_message_header(
        "1",
        b"From: noreply@example.com\r\nReply-To: support@example.com\r\n\r\nHello",
        (),
    )
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "Innboks")
    message = store.save_message_metadata(
        account.id,
        folder.id,
        imap_uid="1",
        sender=header.sender,
        reply_to=header.reply_to,
    )
    reopened = MailStore(store.database_path)
    assert (
        reopened.list_messages(account.id, folder.id)[0].reply_to
        == "support@example.com"
    )
    draft = MailSendService(reopened).create_reply_draft(message.id)
    assert draft.recipients == "support@example.com"
    assert draft.account_id == account.id


def test_large_mailbox_deletions_do_not_hit_sqlite_parameter_limit(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "Innboks", "INBOX")
    local = store.add_message(account.id, folder.id, subject="Local copy")
    with connect(store.database_path) as connection:
        connection.executemany(
            "INSERT INTO messages(account_id,folder_id,imap_uid) VALUES (?,?,?)",
            [(account.id, folder.id, str(uid)) for uid in range(1, 1502)],
        )
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 20)
        removed = delete_messages_missing_from_folder(
            connection, account.id, folder.id, [str(uid) for uid in range(1, 1501)]
        )
        assert removed == 1
    assert store.get_message(local.id) is not None


def test_sync_reconciles_folders_larger_than_old_limit(tmp_path, monkeypatch):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "Innboks", "INBOX")
    with connect(store.database_path) as connection:
        connection.executemany(
            "INSERT INTO messages(account_id,folder_id,imap_uid) VALUES (?,?,?)",
            [(account.id, folder.id, str(uid)) for uid in range(1, 1002)],
        )

    class Client:
        def __init__(self, _settings):
            pass

        def list_folders(self):
            return [ImapFolder("INBOX")]

        def fetch_headers_since_uid(self, *_args):
            return []

        def fetch_recent_flags(self, *_args):
            return []

        def list_uids(self, _name):
            return ["1001"]

    monkeypatch.setattr(
        mail_sync, "get_mail_account_settings", lambda *_: SimpleNamespace(imap=None)
    )
    MailSyncService(store, imap_client_class=Client).fetch_imap_headers(account.id)
    assert store.count_messages(account.id, folder.id) == 1


def test_imap_can_fetch_large_attachment_with_uid_guard_and_peek():
    message = EmailMessage()
    message.set_content("Dummy")
    content = b"x" * (5 * 1024 * 1024 + 1)
    message.add_attachment(
        content, maintype="application", subtype="pdf", filename="large.pdf"
    )
    raw = message.as_bytes()
    assert (
        imap_client._parse_message_header("42", raw, ()).attachments[0].content is None
    )
    calls = []

    class Connection:
        def login(self, *_):
            pass

        def select(self, name, readonly=False):
            assert readonly
            return "OK", [b"1"]

        def response(self, _name):
            return "UIDVALIDITY", [b"10"]

        def uid(self, *args):
            calls.append(args)
            part = message.get_payload()[1]
            payload = part.get_payload().encode("ascii")
            if args[-1] == "(UID BODYSTRUCTURE)":
                structure = (
                    b'(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 6 1)("APPLICATION" "PDF" NIL NIL NIL "BASE64" '
                    + str(len(payload)).encode()
                    + b' NIL ("ATTACHMENT" ("FILENAME" "large.pdf"))) "MIXED")'
                )
                return "OK", [b"1 (UID 42 BODYSTRUCTURE " + structure + b")"]
            assert args[-1] == "(UID BODY.PEEK[2.MIME] BODY.PEEK[2])"
            headers = b'Content-Type: application/pdf\r\nContent-Disposition: attachment; filename="large.pdf"\r\nContent-Transfer-Encoding: base64\r\n\r\n'
            return "OK", [
                (
                    b"1 (UID 42 BODY[2.MIME] {" + str(len(headers)).encode() + b"}",
                    headers,
                ),
                (b" BODY[2] {" + str(len(payload)).encode() + b"}", payload),
                b")",
            ]

        def logout(self):
            pass

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user", "test"),
        connection_factory=lambda *_args, **_kwargs: Connection(),
    )
    client.expect_uidvalidity("INBOX", 10)
    attachment = client.fetch_attachment("INBOX", "42", 0)
    assert attachment.content == content
    assert calls == [
        ("FETCH", "42", "(UID BODYSTRUCTURE)"),
        ("FETCH", "42", "(UID BODY.PEEK[2.MIME] BODY.PEEK[2])"),
    ]
    client.expect_uidvalidity("INBOX", 11)
    with pytest.raises(ValueError):
        client.fetch_attachment("INBOX", "42", 0)
    assert len(calls) == 2


def test_attachment_service_downloads_once_and_checks_metadata(tmp_path, monkeypatch):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "Innboks", "INBOX")
    store.reconcile_uidvalidity(account.id, folder.id, 99)
    message = store.add_message(account.id, folder.id, imap_uid="42")
    store.replace_message_attachments(
        message.id, [Attachment(0, message.id, "report.pdf", "application/pdf", 6)]
    )
    attachment = store.list_attachments(message.id)[0]
    calls = []

    class Client:
        def __init__(self, _settings):
            pass

        def expect_uidvalidity(self, name, validity):
            assert (name, validity) == ("INBOX", 99)

        def fetch_attachment(self, name, uid, index):
            calls.append((name, uid, index))
            return ImapAttachment("report.pdf", "application/pdf", 6, content=b"report")

    monkeypatch.setattr(
        attachment_module,
        "get_mail_account_settings",
        lambda *_: SimpleNamespace(imap=None),
    )
    service = AttachmentService(store, Client)
    with pytest.raises(ValueError):
        service.content(replace(attachment, filename="wrong.pdf"))
    assert store.get_attachment_content(attachment.id) is None
    assert service.content(attachment) == b"report"
    assert service.content(attachment) == b"report"
    assert len(calls) == 2


def test_draft_attachments_are_snapshots_and_cleanup_is_scoped(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    source = tmp_path / "original.txt"
    source.write_bytes(b"original")
    drafts = DraftService(store)
    id = drafts.save(ComposeDraft(account.id, attachment_paths=(str(source),)))
    snapshot = drafts.get(id).draft
    path = Path(snapshot.attachment_paths[0])
    assert path != source and path.stat().st_mode & 0o777 == 0o600
    source.write_bytes(b"changed")
    assert path.read_bytes() == b"original"
    source.unlink()
    reopened = DraftService(MailStore(store.database_path))
    assert Path(reopened.get(id).draft.attachment_paths[0]).read_bytes() == b"original"
    other_id = drafts.save(snapshot)
    other_path = Path(drafts.get(other_id).draft.attachment_paths[0])
    drafts.delete(id)
    assert not path.exists()
    assert other_path.read_bytes() == b"original"
    drafts.save(replace(drafts.get(other_id).draft, attachment_paths=()), other_id)
    assert not other_path.exists()


def test_missing_attachment_does_not_replace_saved_draft(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    service = DraftService(store)
    draft = ComposeDraft(account.id, body_text="Keep this")
    id = service.save(draft)
    with pytest.raises(ValueError):
        service.save(
            replace(draft, attachment_paths=(str(tmp_path / "missing.pdf"),)), id
        )
    assert service.get(id).draft == draft


def test_forward_attachments_survive_temporary_workspace_cleanup(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "Innboks")
    message = store.add_message(account.id, folder.id, subject="Forward")
    store.replace_message_attachments(
        message.id,
        [Attachment(0, message.id, "note.txt", "text/plain", 4, content=b"note")],
    )
    sender = MailSendService(store)
    draft = sender.create_forward_draft(message.id)
    assert Path(draft.attachment_paths[0]).name == "note.txt"
    service = DraftService(store)
    id = service.save(draft)
    sender.close()
    assert Path(service.get(id).draft.attachment_paths[0]).read_bytes() == b"note"


def test_composer_autosave_does_not_restart_itself(tmp_path, app):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    dialog = ComposeDialog(
        [account], ComposeDraft(account.id), draft_service=DraftService(store)
    )
    dialog._autosave.stop()
    assert dialog._save_draft()
    assert not dialog._autosave.isActive()
    dialog.reject()


def test_background_operation_keeps_event_loop_alive_and_callback_on_gui(app):
    runner = TaskRunner()
    main_thread = threading.get_ident()
    release = threading.Event()
    ticks, results = [], []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    def operation():
        assert threading.get_ident() != main_thread
        release.wait(2)
        return "done"

    try:
        assert runner.start(
            operation,
            lambda result: results.append((result, threading.get_ident())),
            pytest.fail,
        )
        assert not runner.start(lambda: None, lambda _: None, pytest.fail)
        wait_for(lambda: len(ticks) >= 3)
        assert runner.busy
    finally:
        release.set()
        wait_for(lambda: not runner.busy)
        timer.stop()
    assert results == [("done", main_thread)]


def test_window_blocks_overlap_and_close_until_worker_finishes(tmp_path, app):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account(
        "Test", "test@example.com", imap_host="imap.example.com"
    )
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    preferences.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=preferences)
    window._load_accounts(select_account_id=account.id)
    window.show()
    release = threading.Event()
    try:
        assert window._start_task("Test", lambda: release.wait(2), lambda _: None)
        assert not window.edit_account_action.isEnabled()
        assert not window.sync_account_action.isEnabled()
        window._queue_auto_sync()
        assert not window._sync_in_progress
        assert not window.close()
    finally:
        release.set()
        wait_for(lambda: not window._task_runner.busy)
        window.close()


def test_attachment_preview_waits_for_active_task(tmp_path, app):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(account.id, inbox.id, subject="Attachment")
    store.replace_message_attachments(
        message.id,
        [
            Attachment(
                id=0,
                message_id=message.id,
                filename="note.txt",
                content_type="text/plain",
                size=5,
            )
        ],
    )
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    preferences.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=preferences)
    window._load_accounts(select_account_id=account.id)
    window.message_list.setCurrentRow(0)
    calls = []

    def retrieve(attachment):
        calls.append(attachment.id)
        store.set_attachment_content(attachment.id, b"Hello")
        return b"Hello"

    window._attachment_service = SimpleNamespace(content=retrieve)
    release = threading.Event()
    try:
        assert window._start_task("Test", lambda: release.wait(2), lambda _: None)
        window.attachment_list.setCurrentRow(0)
        assert window._pending_attachment_preview is not None
        assert not calls
        assert "waiting" in window.attachment_preview.toPlainText()
        release.set()
        wait_for(lambda: "Hello" in window.attachment_preview.toPlainText())
        assert len(calls) == 1
    finally:
        release.set()
        wait_for(lambda: not window._task_runner.busy)
        window.close()


def test_failed_task_can_be_retried_and_clears_callback_references(app):
    runner = TaskRunner()
    errors = []
    results = []

    def fail():
        raise ValueError("test failure")

    assert runner.start(fail, results.append, errors.append)
    wait_for(lambda: not runner.busy)
    assert errors == ["test failure"] and not results
    assert runner._success is None and runner._failure is None
    assert runner.start(lambda: 42, results.append, errors.append)
    wait_for(lambda: not runner.busy)
    assert results == [42]
