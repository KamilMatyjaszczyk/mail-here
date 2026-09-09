from __future__ import annotations

import imaplib
import io
import smtplib
import ssl
import subprocess
from dataclasses import replace

import pytest

from mailklient import tuta_check
from mailklient.mail.imap_client import ImapAuthenticationError, ImapClient
from mailklient.mail.smtp_client import SmtpClient
from mailklient.mail.tuta_bridge import (
    build_tuta_bridge_settings,
    default_certificate_path,
)
from mailklient.security.tls import create_mail_ssl_context


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bridge-tls")
    pairs = []
    for name in ("trusted", "other"):
        certificate = directory / f"{name}.pem"
        key = directory / f"{name}.key"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        pairs.append((certificate, key))
    return pairs


def _handshake(client_context, certificate, key, hostname="127.0.0.1"):
    """Exercise real certificate validation in memory, without network sockets."""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, key)
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    for _ in range(20):
        try:
            client.do_handshake()
            return
        except ssl.SSLWantReadError:
            pass
        server_in.write(client_out.read())
        try:
            server.do_handshake()
        except ssl.SSLWantReadError:
            pass
        client_in.write(server_out.read())
    pytest.fail("TLS handshake did not complete")


def test_bridge_settings_and_secret_repr(certificates):
    certificate, _key = certificates[0]
    imap, smtp = build_tuta_bridge_settings(
        " user@tuta.io ", " bridge-password ", certificate
    )
    assert (imap.host, smtp.host) == ("127.0.0.1", "127.0.0.1")
    assert (imap.port, smtp.port) == (1143, 1025)
    assert imap.security == smtp.security == "ssl"
    assert imap.auth_method == smtp.auth_method == "password"
    assert imap.username == smtp.username == "user@tuta.io"
    assert imap.password == smtp.password == " bridge-password "
    assert imap.timeout == smtp.timeout == 10.0
    assert imap.local_certificate == smtp.local_certificate == str(certificate)
    assert "bridge-password" not in repr((imap, smtp))


def test_certificate_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_certificate_path() == tmp_path / "tutabridge" / "cert.pem"


def test_missing_certificate_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_tuta_bridge_settings("user@tuta.io", "secret", tmp_path / "missing")


@pytest.mark.parametrize(
    "address,password",
    [
        ("user", "secret"),
        ("user@", "secret"),
        ("@tuta.io", "secret"),
        ("a@b@c", "secret"),
        ("user@tuta.io\r\nBAD", "secret"),
        ("user@tuta.io", ""),
    ],
)
def test_invalid_credentials_are_rejected(address, password):
    with pytest.raises(ValueError):
        build_tuta_bridge_settings(address, password)


def test_local_certificate_is_trusted_with_hostname_checks(certificates):
    certificate, key = certificates[0]
    context = create_mail_ssl_context("127.0.0.1", str(certificate))
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    _handshake(context, certificate, key)
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(context, certificate, key, hostname="wrong.example")


def test_other_certificate_and_default_trust_reject_bridge(certificates):
    certificate, key = certificates[0]
    other_certificate, _other_key = certificates[1]
    context = create_mail_ssl_context("127.0.0.1", str(other_certificate))
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(context, certificate, key)
    gmail_context = create_mail_ssl_context("imap.gmail.com")
    assert gmail_context.check_hostname
    assert gmail_context.verify_mode == ssl.CERT_REQUIRED
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(gmail_context, certificate, key)


@pytest.mark.parametrize("protocol", ["imap", "smtp"])
def test_bridge_certificate_cannot_be_used_for_remote_host(protocol, certificates):
    settings = build_tuta_bridge_settings("user@tuta.io", "secret", certificates[0][0])[
        0 if protocol == "imap" else 1
    ]
    settings = replace(settings, host="mail.example.com")
    client = ImapClient(settings) if protocol == "imap" else SmtpClient(settings)
    with pytest.raises(ValueError, match="127.0.0.1"):
        client.test_connection()


@pytest.mark.parametrize(
    "protocol,imap_auth",
    [
        ("imap", "login"),
        ("imap", "plain"),
        ("smtp", "login"),
    ],
)
@pytest.mark.parametrize("reject_login", [False, True])
def test_bridge_login_timeout_tls_and_cleanup(
    protocol, imap_auth, reject_login, certificates
):
    closed = []
    logins = []
    settings = build_tuta_bridge_settings(
        "user@tuta.io", "secret", certificates[0][0], imap_auth=imap_auth
    )[0 if protocol == "imap" else 1]

    class Connection:
        capabilities = ("IMAP4REV1", "AUTH=PLAIN")

        def __init__(self, host, port, **kwargs):
            assert host == "127.0.0.1"
            assert port == (1143 if protocol == "imap" else 1025)
            assert kwargs["timeout"] == 10.0
            context = kwargs["ssl_context" if protocol == "imap" else "context"]
            assert context.check_hostname
            _handshake(context, *certificates[0])

        def login(self, username, password):
            assert imap_auth == "login"
            self.check_credentials(username, password)

        def authenticate(self, mechanism, authobject):
            assert imap_auth == "plain"
            assert mechanism == "PLAIN"
            assert authobject(b"") == b"\x00user@tuta.io\x00secret"
            self.check_credentials("user@tuta.io", "secret")

        def check_credentials(self, username, password):
            logins.append((username, password))
            if reject_login:
                if protocol == "imap":
                    raise imaplib.IMAP4.error(f"Rejected {password}")
                raise smtplib.SMTPAuthenticationError(535, b"Rejected")

        def logout(self):
            self.shutdown()

        def quit(self):
            return None

        def shutdown(self):
            if closed:
                raise OSError(9, "Already closed")
            closed.append(True)

        def close(self):
            closed.append(True)

    client = (
        ImapClient(settings, connection_factory=Connection)
        if protocol == "imap"
        else SmtpClient(settings, ssl_connection_factory=Connection)
    )
    if reject_login:
        with pytest.raises(
            (ImapAuthenticationError, smtplib.SMTPAuthenticationError)
        ) as caught:
            client.test_connection()
        assert "secret" not in str(caught.value)
    else:
        assert client.test_connection()
    assert logins == [("user@tuta.io", "secret")]
    assert closed == [True]


def test_check_distinguishes_authentication_and_protocol_errors():
    auth_error = tuta_check._error_message(ImapAuthenticationError("hidden"), "IMAP")
    protocol_error = tuta_check._error_message(imaplib.IMAP4.error("hidden"), "IMAP")
    assert "rejected sign-in" in auth_error
    assert "a password error has not been confirmed" in protocol_error
    assert "hidden" not in auth_error + protocol_error


@pytest.mark.parametrize("logout_fails", [False, True])
def test_connection_check_closes_once_with_stdlib_logout(
    logout_fails, certificates, monkeypatch
):
    class Socket:
        shutdown_count = 0
        closed = False

        def shutdown(self, _how):
            self.shutdown_count += 1
            if self.closed:
                raise OSError(9, "Bad file descriptor")

        def close(self):
            self.closed = True

    def command(name):
        assert name == "LOGOUT"
        if logout_fails:
            raise imaplib.IMAP4.error("Logout failed")
        return "BYE", [b"Goodbye"]

    # Use the real stdlib logout/shutdown lifecycle, bypassing only network I/O.
    connection = object.__new__(imaplib.IMAP4)
    connection.sock = Socket()
    stream = io.BytesIO()
    connection._file = stream
    if not isinstance(getattr(imaplib.IMAP4, "file", None), property):
        connection.file = stream  # Python versions before 3.14 use this name.
    connection._simple_command = command
    imap, _smtp = build_tuta_bridge_settings(
        "user@tuta.io", "secret", certificates[0][0]
    )
    client = ImapClient(imap)
    monkeypatch.setattr(client, "_login", lambda: connection)
    if logout_fails:
        with pytest.raises(imaplib.IMAP4.error, match="Logout failed"):
            client.test_connection()
    else:
        assert client.test_connection()
    assert connection.sock.shutdown_count == 1
    assert connection.sock.closed
    assert stream.closed


def test_local_socket_error_is_not_reported_as_certificate_failure():
    message = tuta_check._error_message(OSError(9, "secret server reply"), "IMAP")
    assert "EBADF" in message
    assert "not a certificate rejection" in message
    assert "secret server reply" not in message


@pytest.mark.parametrize("failure_stage", [None, "IMAP", "SMTP", "Keyring"])
def test_check_only_saves_after_both_logins(
    failure_stage, certificates, monkeypatch, capsys
):
    events = []
    secret = "never-print-this-password"
    monkeypatch.setattr(tuta_check.getpass, "getpass", lambda _prompt: secret)

    def check_imap(_self):
        events.append("IMAP")
        if failure_stage == "IMAP":
            raise ConnectionRefusedError(secret)
        return True

    def check_smtp(_self):
        events.append("SMTP")
        if failure_stage == "SMTP":
            raise smtplib.SMTPAuthenticationError(535, secret.encode())
        return True

    def save(address, password):
        assert address == "user@tuta.io"
        assert password == secret
        events.append("Keyring")
        if failure_stage == "Keyring":
            raise RuntimeError(secret)

    monkeypatch.setattr(tuta_check.ImapClient, "test_connection", check_imap)
    monkeypatch.setattr(tuta_check.SmtpClient, "test_connection", check_smtp)
    monkeypatch.setattr(tuta_check, "save_password", save)
    result = tuta_check.main(["user@tuta.io", "--certificate", str(certificates[0][0])])
    assert result == (0 if failure_stage is None else 1)
    stages = ["IMAP", "SMTP", "Keyring"]
    expected = (
        stages if failure_stage is None else stages[: stages.index(failure_stage) + 1]
    )
    assert events == expected
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    if failure_stage:
        assert failure_stage in output.err


def test_no_password_echo_fallback(monkeypatch, capsys):
    def unavailable_prompt(_prompt):
        import warnings

        warnings.warn("No terminal", tuta_check.getpass.GetPassWarning)
        pytest.fail("An insecure password prompt was allowed")

    monkeypatch.setattr(tuta_check.getpass, "getpass", unavailable_prompt)
    assert tuta_check.main(["user@tuta.io"]) == 1
    assert "terminal" in capsys.readouterr().err
