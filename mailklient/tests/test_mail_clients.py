from __future__ import annotations

import pytest

from mailklient.mail import (
    ImapClient,
    ImapFolder,
    ImapMessageFlags,
    ImapMessageHeader,
    SmtpClient,
    imap_client,
)
from mailklient.mail.config import ImapSettings, SmtpSettings


class FakeImapConnection:
    def __init__(self, host, port, ssl_context=None, timeout=None) -> None:
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.timeout = timeout
        self.capabilities = ("IMAP4rev1", "MOVE", "UIDPLUS")
        self.shutdown_called = False
        self.starttls_context = None
        self.logged_in_as: tuple[str, str] | None = None
        self.authenticated_with: tuple[str, bytes] | None = None
        self.selected_mailbox: tuple[str, bool] | None = None
        self.fetch_requests: list[tuple[str, str]] = []
        self.uid_requests: list[tuple[str, tuple[object, ...]]] = []
        self.append_requests = []
        self.expunged = False
        self.logged_out = False

    def starttls(self, ssl_context=None) -> None:
        self.starttls_context = ssl_context

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)

    def authenticate(self, mechanism: str, authobject) -> None:
        self.authenticated_with = (mechanism, authobject(b""))

    def logout(self) -> None:
        self.logged_out = True
        self.shutdown()

    def shutdown(self) -> None:
        self.shutdown_called = True

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
        self.uid_requests.append((command, args))
        if command == "SEARCH":
            return "OK", [b"101 102"]
        if command == "FETCH":
            return self.fetch(args[0], args[1])
        if command in {"MOVE", "COPY"}:
            return "OK", [b""]
        if command == "STORE":
            return "OK", [b""]
        raise AssertionError(f"Unexpected IMAP UID command: {command}")

    def append(self, mailbox, flags, date_time, message):
        self.append_requests.append((mailbox, flags, date_time, message))
        return "OK", [b""]

    def expunge(self):
        self.expunged = True
        return "OK", [b""]

    def fetch(self, message_number, query: str):
        self.fetch_requests.append((message_number, query))
        headers = {
            b"101": (
                b"Message-ID: <old@example.com>\r\n"
                b"Subject: Old\r\n"
                b"From: old@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 10:00:00 +0000\r\n"
                b"\r\n"
                b"Gammel meldingstekst.\r\n"
            ),
            b"102": (
                b"Message-ID: <new@example.com>\r\n"
                b"Subject: New\r\n"
                b"From: new@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 11:00:00 +0000\r\n"
                b"\r\n"
                b"Ny meldingstekst.\r\n"
            ),
        }
        flags = {
            b"101": b"101 (UID 101 FLAGS () BODY[])",
            b"102": b"102 (UID 102 FLAGS (\\Seen) BODY[])",
        }
        if message_number == "1:*" or message_number == "101,102":
            return "OK", [
                (flags[b"101"], headers[b"101"]),
                (flags[b"102"], headers[b"102"]),
            ]

        return "OK", [(flags[message_number], headers[message_number])]


class FakeSmtpConnection:
    def __init__(self, host, port, context=None, timeout=None) -> None:
        self.host = host
        self.port = port
        self.context = context
        self.timeout = timeout
        self.close_called = False
        self.starttls_context = None
        self.logged_in_as: tuple[str, str] | None = None
        self.authenticated_with: tuple[str, str] | None = None
        self.sent_messages = []
        self.quit_called = False

    def starttls(self, context=None) -> None:
        self.starttls_context = context

    def ehlo(self):
        return 250, b"OK"

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

    def close(self) -> None:
        self.close_called = True

    def send_message(self, message, to_addrs=None) -> None:
        self.sent_messages.append((message, to_addrs))


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
        ImapFolder(name="INBOX", flags=("\\HasNoChildren",), delimiter="/"),
        ImapFolder(name="Sent Items", flags=("\\HasNoChildren",), delimiter="/"),
    ]


@pytest.mark.parametrize("response, expected", [
    (b'(\\Marked \\HasNoChildren) "/" Inbox',
     ImapFolder("Inbox", ("\\Marked", "\\HasNoChildren"), "/")),
    (b'(\\HasNoChildren \\Sent) "/" Sent',
     ImapFolder("Sent", ("\\HasNoChildren", "\\Sent"), "/")),
    (b'(\\HasChildren \\Trash) "/" Deleted',
     ImapFolder("Deleted", ("\\HasChildren", "\\Trash"), "/")),
    (b'(\\Junk) "/" Junk', ImapFolder("Junk", ("\\Junk",), "/")),
    (b'() NIL INBOX', ImapFolder("INBOX")),
    (b'() NIL "Sent Items"', ImapFolder("Sent Items")),
    (b'(\\Sent) "/" "[Gmail]/Sent Mail"',
     ImapFolder("[Gmail]/Sent Mail", ("\\Sent",), "/")),
    (b'() "/" "Sent Items" ("OLDNAME" ("Previous name"))',
     ImapFolder("Sent Items", (), "/")),
    (b'() "/" "Quotes \\" and slash \\\\"',
     ImapFolder('Quotes " and slash \\', (), "/")),
    ((b'() "/" {10}', b'Sent Items'), ImapFolder("Sent Items", (), "/")),
    (b'() "/" "S&APg-ppelpost"', ImapFolder("S&APg-ppelpost", (), "/")),
    (b'() "/"', None),
    (b'() "/" "unclosed', None),
    (b'() "/" {10}', None),
    ((b'() "/" {11}', b'Sent Items'), None),
    (b'', None),
])
def test_imap_list_parses_mailbox_field_not_delimiter(response, expected):
    assert imap_client._parse_folder(response) == expected


def test_outlook_list_to_examine_preserves_mailbox_name():
    class OutlookConnection(FakeImapConnection):
        def list(self):
            return "OK", [b'(\\Marked \\HasNoChildren) "/" Inbox']

        def select(self, mailbox, readonly=False):
            assert mailbox == '"Inbox"'
            assert readonly is True
            return super().select(mailbox, readonly)

    client = ImapClient(
        ImapSettings("outlook.office365.com", 993, "user@example.com", "token"),
        connection_factory=OutlookConnection,
    )
    folder = client.list_folders()[0]
    headers = client.fetch_headers(folder.name, limit=2)
    assert len(headers) == 2
    assert headers[0].body_text


@pytest.mark.parametrize("name", ["Inbox\r\nLOGOUT", "Inbox\x00", "Inbox\t", "Inbox\x7f"])
def test_mailbox_quoting_rejects_control_characters(name):
    with pytest.raises(ValueError, match="Control characters"):
        imap_client._quote_mailbox(name)


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
            body_text="Ny meldingstekst.",
            body_preview="Ny meldingstekst.",
        ),
        ImapMessageHeader(
            uid="101",
            flags=(),
            message_id="<old@example.com>",
            subject="Old",
            sender="old@example.com",
            recipients="user@example.com",
            date="2027-01-01T10:00:00+00:00",
            body_text="Gammel meldingstekst.",
            body_preview="Gammel meldingstekst.",
        ),
    ]


def test_imap_client_fetches_headers_since_uid() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    headers = client.fetch_headers_since_uid("INBOX", last_seen_uid=100, limit=25)

    assert [header.uid for header in headers] == ["102", "101"]
    assert connections[0].uid_requests == [
        ("SEARCH", (None, "UID 101:4294967295")),
        ("FETCH", ("101,102", "(UID FLAGS BODY.PEEK[])")),
    ]
    assert connections[0].logged_out


def test_imap_client_marks_message_seen() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.set_seen("INBOX", "102", True)
    assert connections[0].selected_mailbox == ('"INBOX"', False)
    assert connections[0].uid_requests == [
        ("STORE", ("102", "+FLAGS.SILENT", r"(\Seen)")),
    ]
    assert connections[0].logged_out


def test_imap_client_marks_message_unseen() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.set_seen("INBOX", "102", False)
    assert connections[0].uid_requests == [
        ("STORE", ("102", "-FLAGS.SILENT", r"(\Seen)")),
    ]


def test_imap_client_lists_uids() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.list_uids("INBOX") == ["101", "102"]
    assert connections[0].uid_requests == [("SEARCH", (None, "ALL"))]


def test_imap_client_fetches_recent_flags_without_body() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.fetch_recent_flags("INBOX", limit=2) == [
        ImapMessageFlags(uid="101", flags=()),
        ImapMessageFlags(uid="102", flags=("\\Seen",)),
    ]
    assert connections[0].fetch_requests == [("1:*", "(UID FLAGS)")]


def test_imap_client_appends_message() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.append_message(
        "[Gmail]/Sent Mail",
        b"Subject: Hei\r\n\r\nBody",
        internal_date="01-Jan-2027 12:00:00 +0000",
    )
    assert connections[0].append_requests == [
        (
            '"[Gmail]/Sent Mail"',
            r"(\Seen)",
            "01-Jan-2027 12:00:00 +0000",
            b"Subject: Hei\r\n\r\nBody",
        )
    ]


def test_imap_client_moves_message() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.move_message("INBOX", "102", "[Gmail]/Trash")
    assert connections[0].uid_requests[-1] == (
        "MOVE",
        ("102", '"[Gmail]/Trash"'),
    )


def test_imap_client_archives_message() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.archive_message("INBOX", "102")
    assert connections[0].uid_requests[-1] == (
        "MOVE",
        ("102", '"Archive"'),
    )
    assert not connections[0].expunged


def test_imap_client_archives_gmail_by_removing_inbox_label() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    assert client.archive_gmail_message("INBOX", "102")
    assert connections[0].uid_requests[-1] == (
        "STORE",
        ("102", "-X-GM-LABELS.SILENT", r"(\Inbox)"),
    )
    assert not connections[0].expunged


def test_imap_client_fetches_oldest_new_uids_first_when_limited() -> None:
    class ManyNewImapConnection(FakeImapConnection):
        def uid(self, command: str, *args):
            self.uid_requests.append((command, args))
            if command == "SEARCH":
                return "OK", [b"101 102 103"]
            if command == "FETCH":
                return self.fetch(args[0], args[1])
            raise AssertionError(f"Unexpected IMAP UID command: {command}")

    connections: list[ManyNewImapConnection] = []

    def factory(*args, **kwargs):
        connection = ManyNewImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    client.fetch_headers_since_uid("INBOX", last_seen_uid=100, limit=2)

    assert connections[0].uid_requests[-1] == (
        "FETCH",
        ("101,102", "(UID FLAGS BODY.PEEK[])"),
    )


def test_imap_client_fetches_recent_headers_without_search_all() -> None:
    connections: list[FakeImapConnection] = []

    def factory(*args, **kwargs):
        connection = FakeImapConnection(*args, **kwargs)
        connections.append(connection)
        return connection

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=factory,
    )

    client.fetch_headers("INBOX", limit=2)

    assert connections[0].fetch_requests == [
        (
            "1:*",
            "(UID FLAGS BODY.PEEK[])",
        )
    ]


def test_imap_client_extracts_html_body() -> None:
    class HtmlImapConnection(FakeImapConnection):
        def fetch(self, message_number, query: str):
            self.fetch_requests.append((message_number, query))
            message = (
                b"Message-ID: <html@example.com>\r\n"
                b"Subject: HTML\r\n"
                b"From: html@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 12:00:00 +0000\r\n"
                b"Content-Type: multipart/alternative; boundary=abc\r\n"
                b"\r\n"
                b"--abc\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"\r\n"
                b"Plain tekst.\r\n"
                b"--abc\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"\r\n"
                b"<p>HTML tekst.</p>\r\n"
                b"--abc--\r\n"
            )
            return "OK", [(b"1 (UID 201 FLAGS () BODY[])", message)]

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=HtmlImapConnection,
    )

    headers = client.fetch_headers("INBOX", limit=1)

    assert headers[0].body_text == "Plain tekst."
    assert headers[0].body_html == "<p>HTML tekst.</p>"
    assert headers[0].body_preview == "Plain tekst."


def test_imap_client_uses_html_as_text_fallback() -> None:
    class HtmlOnlyImapConnection(FakeImapConnection):
        def fetch(self, message_number, query: str):
            self.fetch_requests.append((message_number, query))
            message = (
                b"Message-ID: <html-only@example.com>\r\n"
                b"Subject: HTML only\r\n"
                b"From: html@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 12:00:00 +0000\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"\r\n"
                b"<p>HTML &amp; tekst.</p>\r\n"
            )
            return "OK", [(b"1 (UID 202 FLAGS () BODY[])", message)]

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=HtmlOnlyImapConnection,
    )

    headers = client.fetch_headers("INBOX", limit=1)

    assert headers[0].body_text == "HTML & tekst."
    assert headers[0].body_html == "<p>HTML &amp; tekst.</p>"
    assert headers[0].body_preview == "HTML & tekst."


def test_imap_client_html_text_fallback_ignores_style_and_script() -> None:
    class NoisyHtmlImapConnection(FakeImapConnection):
        def fetch(self, message_number, query: str):
            self.fetch_requests.append((message_number, query))
            message = (
                b"Message-ID: <html-noisy@example.com>\r\n"
                b"Subject: HTML noisy\r\n"
                b"From: html@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 12:00:00 +0000\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"\r\n"
                b"<html><head><style>body { margin: 0; }</style>"
                b"<script>alert('hei')</script></head>"
                b"<body><p>Synlig tekst.</p></body></html>\r\n"
            )
            return "OK", [(b"1 (UID 203 FLAGS () BODY[])", message)]

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=NoisyHtmlImapConnection,
    )

    headers = client.fetch_headers("INBOX", limit=1)

    assert headers[0].body_text == "Synlig tekst."
    assert "margin" not in headers[0].body_preview
    assert "alert" not in headers[0].body_preview


def test_imap_client_inlines_cid_images_in_html_body() -> None:
    class InlineImageImapConnection(FakeImapConnection):
        def fetch(self, message_number, query: str):
            self.fetch_requests.append((message_number, query))
            message = (
                b"Message-ID: <inline-image@example.com>\r\n"
                b"Subject: Inline image\r\n"
                b"From: html@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 12:00:00 +0000\r\n"
                b"Content-Type: multipart/related; boundary=related\r\n"
                b"\r\n"
                b"--related\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"\r\n"
                b'<p>Logo</p><img src="cid:logo@example.com">\r\n'
                b"--related\r\n"
                b"Content-Type: image/png\r\n"
                b"Content-ID: <logo@example.com>\r\n"
                b"Content-Transfer-Encoding: base64\r\n"
                b"\r\n"
                b"aW1hZ2UtYnl0ZXM=\r\n"
                b"--related--\r\n"
            )
            return "OK", [(b"1 (UID 204 FLAGS () BODY[])", message)]

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=InlineImageImapConnection,
    )

    headers = client.fetch_headers("INBOX", limit=1)

    assert 'src="cid:' not in headers[0].body_html
    assert 'src="data:image/png;base64,aW1hZ2UtYnl0ZXM="' in headers[0].body_html


def test_imap_client_extracts_attachment_metadata() -> None:
    class AttachmentImapConnection(FakeImapConnection):
        def fetch(self, message_number, query: str):
            self.fetch_requests.append((message_number, query))
            message = (
                b"Message-ID: <attachment@example.com>\r\n"
                b"Subject: Vedlegg\r\n"
                b"From: sender@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 12:00:00 +0000\r\n"
                b"Content-Type: multipart/mixed; boundary=mixed\r\n"
                b"\r\n"
                b"--mixed\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"\r\n"
                b"Se vedlegg.\r\n"
                b"--mixed\r\n"
                b"Content-Type: application/pdf\r\n"
                b"Content-Disposition: attachment; filename=rapport.pdf\r\n"
                b"Content-Transfer-Encoding: base64\r\n"
                b"\r\n"
                b"cGRmLWJ5dGVz\r\n"
                b"--mixed--\r\n"
            )
            return "OK", [(b"1 (UID 205 FLAGS () BODY[])", message)]

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=AttachmentImapConnection,
    )

    headers = client.fetch_headers("INBOX", limit=1)

    assert headers[0].body_text == "Se vedlegg."
    assert len(headers[0].attachments) == 1
    assert headers[0].attachments[0].filename == "rapport.pdf"
    assert headers[0].attachments[0].content_type == "application/pdf"
    assert headers[0].attachments[0].size == len(b"pdf-bytes")
    assert headers[0].attachments[0].content == b"pdf-bytes"


def test_imap_client_skips_auto_cache_for_large_attachments(monkeypatch) -> None:
    monkeypatch.setattr(imap_client, "MAX_AUTO_CACHED_ATTACHMENT_BYTES", 4)

    class LargeAttachmentImapConnection(FakeImapConnection):
        def fetch(self, message_number, query: str):
            self.fetch_requests.append((message_number, query))
            message = (
                b"Message-ID: <attachment@example.com>\r\n"
                b"Subject: Vedlegg\r\n"
                b"From: sender@example.com\r\n"
                b"To: user@example.com\r\n"
                b"Date: Fri, 01 Jan 2027 12:00:00 +0000\r\n"
                b"Content-Type: multipart/mixed; boundary=mixed\r\n"
                b"\r\n"
                b"--mixed\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"\r\n"
                b"Se vedlegg.\r\n"
                b"--mixed\r\n"
                b"Content-Type: application/pdf\r\n"
                b"Content-Disposition: attachment; filename=stor.pdf\r\n"
                b"Content-Transfer-Encoding: base64\r\n"
                b"\r\n"
                b"cGRmLWJ5dGVz\r\n"
                b"--mixed--\r\n"
            )
            return "OK", [(b"1 (UID 205 FLAGS () BODY[])", message)]

    client = ImapClient(
        ImapSettings("imap.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=LargeAttachmentImapConnection,
    )

    headers = client.fetch_headers("INBOX", limit=1)

    assert headers[0].attachments[0].filename == "stor.pdf"
    assert headers[0].attachments[0].size == len(b"pdf-bytes")
    assert headers[0].attachments[0].content is None


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


def test_smtp_client_sends_plain_text_message() -> None:
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

    assert client.send_message(
        sender="user@example.com",
        recipients=["friend@example.com"],
        subject="Hei",
        body_text="Dette er en test.",
    )

    sent_message, to_addrs = connections[0].sent_messages[0]
    assert sent_message["From"] == "user@example.com"
    assert sent_message["To"] == "friend@example.com"
    assert sent_message["Subject"] == "Hei"
    assert sent_message["Date"]
    assert sent_message["Message-ID"]
    assert sent_message["User-Agent"] == "mcpMail/0.1"
    assert sent_message.get_content().strip() == "Dette er en test."
    assert to_addrs == ["friend@example.com"]
    assert connections[0].quit_called


def test_smtp_client_sets_reply_headers() -> None:
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

    client.send_message(
        sender="user@example.com",
        recipients=["friend@example.com"],
        subject="Re: Hei",
        body_text="Svar.",
        in_reply_to="<original@example.com>",
    )

    sent_message, _to_addrs = connections[0].sent_messages[0]
    assert sent_message["In-Reply-To"] == "<original@example.com>"
    assert sent_message["References"] == "<original@example.com>"


def test_smtp_client_sends_cc_bcc_html_and_attachments(tmp_path) -> None:
    connections: list[FakeSmtpConnection] = []
    attachment_path = tmp_path / "rapport.txt"
    attachment_path.write_text("rapport", encoding="utf-8")

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

    assert client.send_message(
        sender="user@example.com",
        recipients=["friend@example.com"],
        cc=["copy@example.com"],
        bcc=["hidden@example.com"],
        subject="Hei",
        body_text="Plain",
        body_html="<p><strong>HTML</strong></p>",
        attachment_paths=[str(attachment_path)],
    )

    sent_message, to_addrs = connections[0].sent_messages[0]

    assert sent_message["To"] == "friend@example.com"
    assert sent_message["Cc"] == "copy@example.com"
    assert sent_message["Bcc"] is None
    assert to_addrs == [
        "friend@example.com",
        "copy@example.com",
        "hidden@example.com",
    ]
    assert sent_message.is_multipart()
    assert any(
        part.get_filename() == "rapport.txt"
        for part in sent_message.walk()
    )


def test_smtp_client_rejects_unsafe_header_values() -> None:
    client = SmtpClient(
        SmtpSettings("smtp.example.com", 993, "user@example.com", "hemmelig"),
        connection_factory=FakeSmtpConnection,
    )

    try:
        client.send_message(
            sender="user@example.com",
            recipients=["friend@example.com"],
            subject="Hei\nBcc: attacker@example.com",
            body_text="Dette skal ikke sendes.",
        )
    except ValueError as error:
        assert "Invalid header value" in str(error)
    else:
        raise AssertionError("Unsafe header value was accepted.")
