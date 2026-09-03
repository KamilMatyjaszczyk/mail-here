from __future__ import annotations

from mailklient.mail import ImapClient, ImapFolder, ImapMessageHeader, SmtpClient
from mailklient.mail.config import ImapSettings, SmtpSettings


class FakeImapConnection:
    def __init__(self, host, port, ssl_context=None) -> None:
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.starttls_context = None
        self.logged_in_as: tuple[str, str] | None = None
        self.authenticated_with: tuple[str, bytes] | None = None
        self.selected_mailbox: tuple[str, bool] | None = None
        self.logged_out = False

    def starttls(self, ssl_context=None) -> None:
        self.starttls_context = ssl_context

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)

    def authenticate(self, mechanism: str, authobject) -> None:
        self.authenticated_with = (mechanism, authobject(b""))

    def logout(self) -> None:
        self.logged_out = True

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Sent Items"',
        ]

    def select(self, mailbox: str, readonly: bool = False):
        self.selected_mailbox = (mailbox, readonly)
        return "OK", [b"2"]

    def search(self, _charset, _criteria: str):
        return "OK", [b"1 2"]

    def uid(self, command: str, *args):
        if command == "SEARCH":
            return "OK", [b"101 102"]
        if command == "FETCH":
            return self.fetch(args[0], args[1])
        raise AssertionError(f"Unexpected IMAP UID command: {command}")

    def fetch(self, message_number: bytes, _query: str):
        headers = {
            b"101": (
                b"Message-ID: <old@example.com>\r\n"
                b"Subject: Old\r\n"
                b"From: old@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 10:00:00 +0000\r\n"
                b"\r\n"
            ),
            b"102": (
                b"Message-ID: <new@example.com>\r\n"
                b"Subject: New\r\n"
                b"From: new@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 11:00:00 +0000\r\n"
                b"\r\n"
            ),
        }
        flags = {
            b"101": b"101 (UID 101 FLAGS () BODY[])",
            b"102": b"102 (UID 102 FLAGS (\\Seen) BODY[])",
        }
        return "OK", [(flags[message_number], headers[message_number])]


class FakeSmtpConnection:
    def __init__(self, host, port, context=None) -> None:
        self.host = host
        self.port = port
        self.context = context
        self.starttls_context = None
        self.logged_in_as: tuple[str, str] | None = None
        self.authenticated_with: tuple[str, str] | None = None
        self.quit_called = False

    def starttls(self, context=None) -> None:
        self.starttls_context = context

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)

    def auth(
        self,
        mechanism: str,
        authobject,
        *,
        initial_response_ok: bool = True,
    ) -> None:
        self.authenticated_with = (mechanism, authobject())

    def quit(self) -> None:
        self.quit_called = True


def test_imap_client_tests_ssl_login() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="hemmelig",
            security="ssl",
        ),
        connection_factory=factory,
    )

    assert client.test_connection()
    assert connections[0].host == "imap.example.com"
    assert connections[0].port == 993
    assert connections[0].ssl_context is not None
    assert connections[0].logged_in_as == ("user@example.com", "hemmelig")
    assert connections[0].logged_out


def test_imap_client_tests_starttls_login() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings(
            host="imap.example.com",
            port=143,
            username="user@example.com",
            password="hemmelig",
            security="starttls",
        ),
        starttls_connection_factory=factory,
    )

    assert client.test_connection()
    assert connections[0].host == "imap.example.com"
    assert connections[0].port == 143
    assert connections[0].ssl_context is None
    assert connections[0].starttls_context is not None
    assert connections[0].logged_in_as == ("user@example.com", "hemmelig")
    assert connections[0].logged_out


def test_imap_client_tests_oauth2_login() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="access-token",
            auth_method="oauth2",
        ),
        connection_factory=factory,
    )

    assert client.test_connection()
    assert connections[0].logged_in_as is None
    assert connections[0].authenticated_with == (
        "XOAUTH2",
        b"user=user@example.com\x01auth=Bearer access-token\x01\x01",
    )
    assert connections[0].logged_out


def test_imap_client_lists_folders() -> None:
    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=FakeImapConnection,
    )

    assert client.list_folders() == [
        ImapFolder(name="INBOX"),
        ImapFolder(name="Sent Items"),
    ]


def test_imap_client_fetches_recent_headers_newest_first() -> None:
    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=FakeImapConnection,
    )

    assert client.fetch_headers("INBOX", limit=2) == [
        ImapMessageHeader(
            uid="102",
            flags=("\\Seen",),
            message_id="<new@example.com>",
            subject="New",
            sender="new@example.com",
            recipients="user@example.com",
            date="2027-01-01T11:00:00+00:00",
        ),
        ImapMessageHeader(
            uid="101",
            flags=(),
            message_id="<old@example.com>",
            subject="Old",
            sender="old@example.com",
            recipients="user@example.com",
            date="2027-01-01T10:00:00+00:00",
        ),
    ]


def test_smtp_client_tests_ssl_login() -> None:
    connections: list[FakeSmtpConnection] = []

    def factory(*args, **kwargs):
        connection = FakeSmtpConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = SmtpClient(
        SmtpSettings(
            host="smtp.example.com",
            port=465,
            username="user@example.com",
            password="hemmelig",
            security="ssl",
        ),
        ssl_connection_factory=factory,
    )

    assert client.test_connection()
    assert connections[0].host == "smtp.example.com"
    assert connections[0].port == 465
    assert connections[0].context is not None
    assert connections[0].starttls_context is None
    assert connections[0].logged_in_as == ("user@example.com", "hemmelig")
    assert connections[0].quit_called


def test_smtp_client_tests_starttls_login() -> None:
    connections: list[FakeSmtpConnection] = []

    def factory(*args, **kwargs):
        connection = FakeSmtpConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = SmtpClient(
        SmtpSettings(
            host="smtp.example.com",
            port=587,
            username="user@example.com",
            password="hemmelig",
            security="starttls",
        ),
        connection_factory=factory,
    )

    assert client.test_connection()
    assert connections[0].host == "smtp.example.com"
    assert connections[0].port == 587
    assert connections[0].context is None
    assert connections[0].starttls_context is not None
    assert connections[0].logged_in_as == ("user@example.com", "hemmelig")
    assert connections[0].quit_called


def test_smtp_client_tests_oauth2_login() -> None:
    connections: list[FakeSmtpConnection] = []

    def factory(*args, **kwargs):
        connection = FakeSmtpConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = SmtpClient(
        SmtpSettings(
            host="smtp.example.com",
            port=587,
            username="user@example.com",
            password="access-token",
            security="starttls",
            auth_method="oauth2",
        ),
        connection_factory=factory,
    )

    assert client.test_connection()
    assert connections[0].logged_in_as is None
    assert connections[0].authenticated_with == (
        "XOAUTH2",
        "user=user@example.com\x01auth=Bearer access-token\x01\x01",
    )
    assert connections[0].quit_called
