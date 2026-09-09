from __future__ import annotations

import smtplib
from dataclasses import replace

import pytest
from mailklient.mail.smtp_client import SmtpClient
from mailklient.services import MailSendService, MailStore, mail_settings
from mailklient.services.mail_send import MailSendError
from mailklient.ui.main_window import _send_status_text


@pytest.fixture
def sending(tmp_path, monkeypatch):
    cert = tmp_path / "cert.pem"
    cert.write_text("public certificate for fake TLS")
    store = MailStore(tmp_path / "cache.sqlite3")
    gmail = store.add_account_with_default_folders("Gmail", "user@gmail.com")
    tuta = store.add_account_with_default_folders(
        "Tuta",
        "user@tuta.io",
        provider="tuta",
        local_certificate=str(cert),
        imap_host="127.0.0.1",
        smtp_host="127.0.0.1",
    )
    monkeypatch.setattr(mail_settings, "get_password", lambda _email: "bridge-secret")
    folder = store.get_or_add_folder(tuta.id, "Innboks")
    message = store.add_message(
        tuta.id,
        folder.id,
        sender="sender@example.com",
        subject="Hello",
        message_id="<original@example.com>",
        body_text="Original",
    )
    return store, tuta, gmail, message


class WireConnection:
    def __init__(self, host, port, *, context, timeout):
        assert (host, port) == ("127.0.0.1", 1025)
        assert timeout == 60
        self.closed = False
        self.sent = []

    def login(self, username, password):
        assert (username, password) == ("user@tuta.io", "bridge-secret")

    def send_message(self, message, *, to_addrs):
        self.sent.append((message, to_addrs))
        return {}

    def quit(self):
        raise smtplib.SMTPServerDisconnected("QUIT failed after acceptance")

    def close(self):
        self.closed = True


def test_tuta_reply_sends_mime_via_receiving_account(sending, tmp_path, monkeypatch):
    store, tuta, gmail, original = sending
    from mailklient.mail import smtp_client

    monkeypatch.setattr(smtp_client, "create_mail_ssl_context", lambda *_args: object())
    connections = []

    def connection_factory(*args, **kwargs):
        connection = WireConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    def client_factory(settings):
        assert settings.local_certificate
        return SmtpClient(settings, ssl_connection_factory=connection_factory)

    def forbidden_append(_settings):
        pytest.fail("Tuta handles the sent copy; no IMAP APPEND should occur")

    service = MailSendService(store, client_factory, forbidden_append)
    draft = service.create_reply_draft(original.id)
    assert draft.account_id == tuta.id != gmail.id
    attachment = tmp_path / "note.txt"
    attachment.write_bytes(b"attachment content")
    draft = replace(
        draft,
        body_text="Reply",
        body_html="<b>Reply</b>",
        cc="copy@example.com",
        bcc="hidden@example.com",
        attachment_paths=(str(attachment),),
    )
    result = service.send_draft(draft)
    assert result.sent and result.local_copy_saved
    assert not result.server_copy_attempted
    connection = connections[0]
    assert connection.closed
    message, envelope = connection.sent[0]
    assert str(message["From"]) == "user@tuta.io"
    assert message["In-Reply-To"] == "<original@example.com>"
    assert message["References"] == "<original@example.com>"
    assert message["Date"] and message["Message-ID"]
    assert message["Bcc"] is None
    assert envelope == ["sender@example.com", "copy@example.com", "hidden@example.com"]
    assert (
        message.get_body(preferencelist=("html",)).get_content().strip()
        == "<b>Reply</b>"
    )
    assert (
        next(message.iter_attachments()).get_payload(decode=True)
        == b"attachment content"
    )
    sent_folder = store.get_or_add_folder(tuta.id, "Sendt")
    assert len(store.list_messages(tuta.id, sent_folder.id)) == 1
    assert len(connections) == 1


def test_cache_failure_does_not_report_send_failure(sending, monkeypatch):
    store, _tuta, _gmail, message = sending

    class Accepted:
        def __init__(self, _settings):
            pass

        def send_message(self, **_kwargs):
            return True

    service = MailSendService(store, Accepted)

    def fail_cache(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_save_sent_copy", fail_cache)
    result = service.send_draft(service.create_reply_draft(message.id))
    assert result.sent and not result.local_copy_saved
    assert "Do not send" in _send_status_text(result)


@pytest.mark.parametrize(
    "error, text",
    [
        (ConnectionRefusedError("secret"), "Start it"),
        (smtplib.SMTPAuthenticationError(535, b"secret"), "bridge password"),
        (TimeoutError("secret"), "before sending again"),
        (smtplib.SMTPDataError(451, b"secret"), "rejected the message"),
    ],
)
def test_tuta_send_errors_are_safe_and_no_local_sent_copy(sending, error, text):
    store, tuta, _gmail, message = sending

    class Rejected:
        def __init__(self, _settings):
            pass

        def send_message(self, **_kwargs):
            raise error

    service = MailSendService(store, Rejected)
    with pytest.raises(MailSendError) as caught:
        service.send_draft(service.create_reply_draft(message.id))
    assert text in str(caught.value)
    assert "secret" not in str(caught.value)
    sent_folder = store.get_or_add_folder(tuta.id, "Sendt")
    assert store.list_messages(tuta.id, sent_folder.id) == []
