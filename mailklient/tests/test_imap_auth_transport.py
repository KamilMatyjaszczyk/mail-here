"""Exercise the real imaplib transport against a local protocol peer."""

import base64
import imaplib
import socket
import threading
import time
from contextlib import contextmanager

import pytest

from mailklient.mail import ImapClient, ImapSettings
from mailklient.mail.imap_client import ImapAuthenticationError


@contextmanager
def peer(exchange):
    errors = []
    release = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)

        def run():
            try:
                connection, _address = listener.accept()
                with connection, connection.makefile("rb") as stream:
                    connection.settimeout(3)
                    connection.sendall(b"* OK Local test IMAP\r\n")
                    tag, command = stream.readline().split(b" ", 1)
                    assert command.strip() == b"CAPABILITY"
                    connection.sendall(
                        b"* CAPABILITY IMAP4rev1 AUTH=XOAUTH2\r\n"
                        + tag
                        + b" OK Completed\r\n"
                    )
                    exchange(connection, stream, release)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            yield listener.getsockname()[1]
        finally:
            release.set()
            thread.join(4)
            assert not thread.is_alive()
            assert not errors, errors


def client_for(port):
    # Only this loopback test transport skips TLS; production still uses SSL.
    return ImapClient(
        ImapSettings(
            "127.0.0.1",
            port,
            "test@example.com",
            "test-token",
            auth_method="oauth2",
            timeout=0.3,
        ),
        connection_factory=lambda host, port, **kw: imaplib.IMAP4(
            host, port, timeout=kw["timeout"]
        ),
    )


def test_real_imaplib_finishes_rejected_oauth_exchange():
    replies = []

    def exchange(connection, stream, release):
        tag, command = stream.readline().split(b" ", 1)
        assert command.strip() == b"AUTHENTICATE XOAUTH2"
        connection.sendall(b"+ \r\n")
        replies.append(base64.b64decode(stream.readline().strip()))
        challenge = base64.b64encode(b'{"status":"401","schemes":"bearer"}')
        connection.sendall(b"+ " + challenge + b"\r\n")
        replies.append(stream.readline())
        assert replies[-1] == b"\r\n"
        connection.sendall(tag + b" NO [AUTHENTICATIONFAILED] Rejected\r\n")

    with peer(exchange) as port:
        with pytest.raises(ImapAuthenticationError):
            client_for(port).test_connection()
    assert len(replies) == 2


def test_real_imaplib_times_out_when_server_never_answers_auth():
    def exchange(connection, stream, release):
        assert b"AUTHENTICATE XOAUTH2" in stream.readline()
        release.wait(3)

    with peer(exchange) as port:
        started = time.monotonic()
        with pytest.raises((OSError, imaplib.IMAP4.abort)):
            client_for(port).test_connection()
        assert time.monotonic() - started < 2
