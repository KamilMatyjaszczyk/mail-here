from __future__ import annotations

import imaplib
import os
import sqlite3
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from mailklient.database import connect, initialize_database
from mailklient.mail.config import ImapFolder
from mailklient.mail.imap_client import _parse_message_header
from mailklient.services import MailStore, MailSyncService, mail_settings, tuta_setup
from mailklient.services.tuta_setup import TutaSetupError, TutaSetupService
from mailklient.ui import main_window
from mailklient.ui.account_dialog import AccountDialog
from mailklient.ui.main_window import MainWindow
from mailklient.workers.tuta_setup_worker import TutaSetupWorker


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    cert = tmp_path / "cert.pem"
    cert.write_text("Public certificate placeholder for fake connections")
    saved = {"user@tuta.io": "local-secret"}
    monkeypatch.setattr(tuta_setup, "get_password", saved.get)
    monkeypatch.setattr(tuta_setup, "save_password", saved.__setitem__)
    monkeypatch.setattr(mail_settings, "get_password", saved.get)
    store = MailStore(tmp_path / "cache.sqlite3")
    return store, cert, saved


class BridgeImap:
    def __init__(self, settings):
        assert settings.host == "127.0.0.1"
        assert settings.port == 1143
        assert settings.security == "ssl"
        assert settings.password_mechanism == "plain"
        assert settings.password == "local-secret"
        assert settings.local_certificate

    def test_connection(self):
        return True

    def list_folders(self):
        return [ImapFolder("INBOX"), ImapFolder("Sent", ("\\Sent",))]

    def fetch_headers(self, name, limit):
        assert name == "INBOX"
        assert limit == 5
        raw = (
            b"From: Sender <sender@example.com>\r\n"
            b"To: user@tuta.io\r\n"
            b"Subject: Tuta fixture\r\n"
            b"Date: Mon, 07 Sep 2026 12:00:00 +0000\r\n"
            b"Message-ID: <tuta-fixture@example.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
            b"--outer\r\nContent-Type: multipart/alternative; boundary=inner\r\n\r\n"
            b"--inner\r\nContent-Type: text/plain\r\n\r\nHello Tuta\r\n"
            b"--inner\r\nContent-Type: text/html\r\n\r\n<p>Hello Tuta</p>\r\n"
            b"--inner--\r\n"
            b"--outer\r\nContent-Type: text/plain\r\n"
            b"Content-Disposition: attachment; filename=note.txt\r\n\r\nAttachment\r\n"
            b"--outer--\r\n"
        )
        return [_parse_message_header("1", raw, ())]

    def fetch_headers_since_uid(self, name, last_uid, limit):
        assert last_uid == 1
        return []

    def list_uids(self, name):
        return ["1"]


class BridgeSmtp:
    def __init__(self, settings):
        assert settings.host == "127.0.0.1"
        assert settings.port == 1025
        assert settings.security == "ssl"
        assert settings.password == "local-secret"

    def test_connection(self):
        return True


def test_setup_restores_keyring_config_after_restart(setup_env):
    store, cert, saved = setup_env
    account = TutaSetupService(store, BridgeImap, BridgeSmtp).add_account(
        "Tuta", " user@tuta.io ", certificate_path=cert
    )
    reopened = MailStore(store.database_path)
    assert reopened.get_account(account.id) == account
    settings = mail_settings.get_mail_account_settings(reopened, account.id)
    assert settings.imap.local_certificate == str(cert)
    assert settings.imap.password_mechanism == "plain"
    assert settings.imap.password == saved["user@tuta.io"]
    with connect(store.database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }
    assert not {"password", "bridge_password", "access_token"} & columns
    assert b"local-secret" not in store.database_path.read_bytes()


@pytest.mark.parametrize("failing_protocol", ["IMAP", "SMTP"])
@pytest.mark.parametrize("error", [ConnectionRefusedError, imaplib.IMAP4.error])
def test_failed_setup_preserves_other_accounts_and_keyring(
    setup_env, failing_protocol, error
):
    store, cert, saved = setup_env
    gmail = store.add_account_with_default_folders("Gmail", "other@gmail.com")
    inbox = store.list_folders(gmail.id)[0]
    store.add_message(gmail.id, inbox.id, subject="Existing mail")

    class Failing:
        def __init__(self, _settings):
            pass

        def test_connection(self):
            raise error("local-secret must not appear in UI")

    service = TutaSetupService(
        store,
        Failing if failing_protocol == "IMAP" else BridgeImap,
        Failing if failing_protocol == "SMTP" else BridgeSmtp,
    )
    with pytest.raises(TutaSetupError) as caught:
        service.add_account("Tuta", "user@tuta.io", certificate_path=cert)
    assert failing_protocol in str(caught.value)
    assert "local-secret" not in str(caught.value)
    assert store.list_accounts() == [gmail]
    assert store.list_messages(gmail.id, inbox.id)[0].subject == "Existing mail"
    assert saved == {"user@tuta.io": "local-secret"}


def test_failed_keyring_write_removes_new_account(setup_env, monkeypatch):
    store, cert, _saved = setup_env

    def fail_save(*_args):
        raise RuntimeError("local-secret")

    monkeypatch.setattr(tuta_setup, "save_password", fail_save)
    with pytest.raises(TutaSetupError, match="keyring"):
        TutaSetupService(store, BridgeImap, BridgeSmtp).add_account(
            "Tuta", "user@tuta.io", "local-secret", cert
        )
    assert store.list_accounts() == []


def test_missing_password_does_not_create_account(setup_env):
    store, cert, saved = setup_env
    saved.clear()
    with pytest.raises(TutaSetupError, match="bridge password"):
        TutaSetupService(store, BridgeImap, BridgeSmtp).add_account(
            "Tuta", "user@tuta.io", certificate_path=cert
        )
    assert store.list_accounts() == []


def test_first_sync_failure_keeps_account_for_retry(setup_env):
    store, cert, _saved = setup_env

    class FailingSync:
        def fetch_imap_headers(self, *_args, **_kwargs):
            raise ConnectionRefusedError("sensitive server details")

    worker = TutaSetupWorker(
        TutaSetupService(store, BridgeImap, BridgeSmtp),
        FailingSync(),
        "Tuta",
        "user@tuta.io",
        None,
        cert,
    )
    created, failures, done = [], [], []
    worker.account_created.connect(created.append)
    worker.failed.connect(failures.append)
    worker.done.connect(lambda: done.append(True))
    worker.run()
    assert len(created) == 1
    assert store.get_account(created[0].id) == created[0]
    assert "The account is saved" in failures[0]
    assert "sensitive" not in failures[0]
    assert done == [True]


def test_worker_normalizes_messages_and_unifies_inbox(setup_env):
    store, cert, _saved = setup_env
    gmail = store.add_account_with_default_folders("Gmail", "other@gmail.com")
    inbox = next(f for f in store.list_folders(gmail.id) if f.name == "Innboks")
    store.add_message(gmail.id, inbox.id, subject="Gmail fixture")
    sync = MailSyncService(store, BridgeImap)
    worker = TutaSetupWorker(
        TutaSetupService(store, BridgeImap, BridgeSmtp),
        sync,
        "Tuta",
        "user@tuta.io",
        None,
        cert,
    )
    accounts, results, failures = [], [], []
    worker.account_created.connect(accounts.append)
    worker.finished.connect(lambda _id, result: results.append(result))
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures
    assert results[0].messages_seen == 1
    messages = store.list_unified_inbox_messages()
    assert {m.subject for m in messages} == {"Tuta fixture", "Gmail fixture"}
    message = next(m for m in messages if m.account_id == accounts[0].id)
    assert message.sender == "Sender <sender@example.com>"
    assert message.recipients == "user@tuta.io"
    assert message.body_text == "Hello Tuta"
    assert "<p>Hello Tuta</p>" in message.body_html
    assert not message.is_read
    attachment = store.list_attachments(message.id)[0]
    assert attachment.filename == "note.txt"
    assert store.get_attachment_content(attachment.id) == b"Attachment"
    sync.fetch_imap_headers(
        accounts[0].id, limit_per_folder=5, message_folder_names=("INBOX",)
    )
    assert len(store.list_unified_inbox_messages()) == 2
    assert sync.archive_message(message.id) is False


def test_migration_preserves_existing_account(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY, display_name TEXT, email_address TEXT UNIQUE)"
        )
        connection.execute(
            "INSERT INTO accounts VALUES (1, 'Existing', 'user@example.com')"
        )
    initialize_database(path)
    initialize_database(path)
    account = MailStore(path).get_account(1)
    assert account.provider == "imap"
    assert account.local_certificate is None
    assert account.email_address == "user@example.com"


def test_tuta_dialog_and_background_setup(setup_env, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store, cert, _saved = setup_env
    dialog = AccountDialog()
    dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData("tuta"))
    dialog.display_name_edit.setText("Tuta")
    dialog.email_address_edit.setText("user@tuta.io")
    dialog.certificate_edit.setText(str(cert))
    data = dialog.account_data()
    assert data.provider == "tuta"
    assert data.password is None
    assert data.username == "user@tuta.io"
    assert data.oauth_provider is None
    assert not dialog.server_group.isEnabled()
    assert not dialog.auth_method_combo.isEnabled()
    assert data.imap_port == 1143 and data.smtp_port == 1025
    dialog.close()

    calls = []
    main_thread = threading.get_ident()

    class RecordingImap(BridgeImap):
        def test_connection(self):
            calls.append(threading.get_ident())
            return True

    service = TutaSetupService(store, RecordingImap, BridgeSmtp)
    monkeypatch.setattr(main_window, "TutaSetupService", lambda _store: service)
    failures = []
    monkeypatch.setattr(
        main_window.QMessageBox, "warning", lambda *_args: failures.append(True)
    )
    window = MainWindow(store, MailSyncService(store, BridgeImap))
    try:
        assert window._add_account(
            "Tuta", "user@tuta.io", provider="tuta", local_certificate=str(cert)
        )
        deadline = time.monotonic() + 5
        while window._tuta_setup_thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window._tuta_setup_thread is None
        assert not failures
        assert calls and all(thread != main_thread for thread in calls)
        assert window.message_list.count() == 1
        window.account_list.setCurrentRow(0)
        assert window.message_list.count() == 1
        assert not window.archive_button.isEnabled()
    finally:
        if window._tuta_setup_thread is not None:
            window._tuta_setup_thread.quit()
            window._tuta_setup_thread.wait(5000)
            app.processEvents()
        window.close()
