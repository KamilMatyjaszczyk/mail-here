"""Regression coverage for auth continuation, session ownership and deadlines."""

import imaplib
import time

import pytest
from PySide6.QtWidgets import QApplication
from test_imap_parts import PartsConnection
from test_mail_clients import FakeImapConnection

from mailklient.mail import ImapClient, ImapSettings
from mailklient.mail.imap_client import ImapAuthenticationError
from mailklient.services import HeaderSyncResult, MailStore, MailSyncService, mail_sync
from mailklient.workers import MailSyncWorker


def test_oauth_error_challenge_is_acknowledged_not_sent_another_token():
    replies = []

    class Rejected(FakeImapConnection):
        def authenticate(self, mechanism, respond):
            assert mechanism == "XOAUTH2"
            replies.append(respond(b""))
            replies.append(respond(b'{"status":"401","schemes":"bearer"}'))
            assert replies[-1] == b""
            replies.append(respond(b'{"status":"401"}'))
            assert replies[-1] is None
            raise imaplib.IMAP4.error("server response with test-token")

    connection = Rejected("test", 993)
    client = ImapClient(
        ImapSettings(
            "test", 993, "test@example.com", "test-token", auth_method="oauth2"
        ),
        connection_factory=lambda *a, **kw: connection,
    )
    with pytest.raises(ImapAuthenticationError) as error:
        client.test_connection()
    assert replies[0] == b"user=test@example.com\x01auth=Bearer test-token\x01\x01"
    assert connection.shutdown_called
    assert "test-token" not in str(error.value)


def test_full_sync_reuses_one_connection_and_reports_stages(tmp_path, monkeypatch):
    connections = []

    def factory(*args, **kwargs):
        connection = PartsConnection()
        connections.append(connection)
        return connection

    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    settings = ImapSettings("test", 993, "test@example.com", "test-token")
    from types import SimpleNamespace

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda *_: SimpleNamespace(imap=settings),
    )
    progress = []
    result = MailSyncService(
        store, imap_client_class=lambda s: ImapClient(s, connection_factory=factory)
    ).fetch_imap_headers(account.id, progress=progress.append, flag_refresh_limit=0)
    assert result.messages_seen == 1
    assert len(connections) == 1 and connections[0].logged_out
    assert progress[0] == "Loading credentials from the keyring..."
    assert any("Signing in" in stage for stage in progress)
    assert any("fetching message 1 of 1" in stage for stage in progress)
    assert any("saving message 1/1" in stage for stage in progress)
    assert progress[-1] == "Finishing local storage..."
    assert all("test-token" not in stage for stage in progress)


@pytest.mark.parametrize("failure", [TimeoutError, InterruptedError])
def test_failed_session_closes_socket_without_waiting_for_logout(failure):
    connection = FakeImapConnection("test", 993)
    client = ImapClient(
        ImapSettings("test", 993, "test", "test"),
        connection_factory=lambda *a, **kw: connection,
    )
    with pytest.raises(failure):
        with client.session():
            client.list_folders()
            assert not connection.logged_out
            raise failure("test")
    assert connection.shutdown_called
    assert not connection.logged_out
    assert client._shared_connection is None


def test_worker_deadline_emits_failure_and_finishes():
    app = QApplication.instance() or QApplication([])

    class SlowSync:
        def fetch_imap_headers(self, account_id, *, progress, **kwargs):
            time.sleep(0.03)
            progress("Next step")
            return HeaderSyncResult(0, 0)

    worker = MailSyncWorker(SlowSync(), 1, timeout_seconds=0.01)
    results, failures, finished = [], [], []
    worker.synced.connect(lambda *args: results.append(args))
    worker.failed.connect(lambda *args: failures.append(args))
    worker.finished.connect(lambda: finished.append(True))
    worker.start()
    assert worker.wait(2000)
    app.processEvents()
    assert not results and finished == [True]
    assert "timed out" in failures[0][1]
