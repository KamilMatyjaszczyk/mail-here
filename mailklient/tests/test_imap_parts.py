from __future__ import annotations

import base64
import sqlite3
from types import SimpleNamespace

import pytest
from imapclient.response_parser import parse_fetch_response

from mailklient.domain import Attachment
from mailklient.mail import ImapClient, ImapFolder, ImapMessageHeader, ImapSettings
from mailklient.mail.imap_parts import mime_parts
from mailklient.services import MailStore, MailSyncService, mail_sync
from mailklient.services.attachment_cache import AttachmentCache


def literal(prefix, content):
    return (prefix + b" {" + str(len(content)).encode() + b"}", content)


class PartsConnection:
    def __init__(self):
        self.calls = []
        self.validity = 9
        self.truncate = False
        self.wrong_uid = False
        self.logged_out = False
        self.content = b"some pdf bytes"
        self.payload = base64.b64encode(self.content)
        self.header = b"From: sender@example.com\r\nReply-To: reply@example.com\r\nSubject: Parts\r\n\r\n"
        self.structure = (
            b'(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 5 1)'
            b'("APPLICATION" "PDF" NIL NIL NIL "BASE64" '
            + str(len(self.payload)).encode()
            + b' NIL ("ATTACHMENT" ("FILENAME" "report.pdf"))) "MIXED")'
        )
        self.parts = {
            "1": (b"Content-Type: text/plain; charset=UTF-8\r\n\r\n", b"Hello"),
            "2": (
                b"Content-Type: application/pdf\r\nContent-Disposition: attachment; filename=report.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\n",
                self.payload,
            ),
        }

    def login(self, *_):
        pass

    def select(self, _name, readonly=False):
        assert readonly
        return "OK", [b"1"]

    def response(self, _name):
        return "UIDVALIDITY", [str(self.validity).encode()]

    def logout(self):
        self.logged_out = True

    def list(self):
        return "OK", [b'(\\Inbox) "/" "INBOX"']

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"42"]
        assert command == "FETCH"
        uid, query = args
        assert uid == "42"
        prefix = b"1 (UID " + (b"99" if self.wrong_uid else b"42")
        if query == "(UID BODYSTRUCTURE)":
            return "OK", [prefix + b" BODYSTRUCTURE " + self.structure + b")"]
        if query == "(UID FLAGS BODYSTRUCTURE BODY.PEEK[HEADER])":
            return "OK", [
                literal(
                    prefix
                    + b" FLAGS (\\Seen) BODYSTRUCTURE "
                    + self.structure
                    + b" BODY[HEADER]",
                    self.header,
                ),
                b")",
            ]
        for section, (header, payload) in self.parts.items():
            if query == f"(UID BODY.PEEK[{section}.MIME] BODY.PEEK[{section}])":
                if self.truncate:
                    payload = payload[:-1]
                if header is None:
                    return "OK", [
                        literal(
                            prefix
                            + f" BODY[{section}.MIME] NIL BODY[{section}]".encode(),
                            payload,
                        ),
                        b")",
                    ]
                return "OK", [
                    literal(prefix + f" BODY[{section}.MIME]".encode(), header),
                    literal(f" BODY[{section}]".encode(), payload),
                    b")",
                ]
        raise AssertionError(f"Unexpected or full-message fetch: {query}")


def client_for(connection):
    return ImapClient(
        ImapSettings("imap.example.com", 993, "test", "test"),
        connection_factory=lambda *args, **kwargs: connection,
    )


def test_sync_fetches_only_readable_parts_and_attachment_metadata():
    connection = PartsConnection()
    client = client_for(connection)
    client.expect_uidvalidity("INBOX", 9)
    messages = client.fetch_messages("INBOX")
    assert len(messages) == 1 and not messages[0].parse_error
    assert messages[0].body_text == "Hello"
    assert messages[0].reply_to == "reply@example.com"
    attachment = messages[0].attachments[0]
    assert attachment.imap_section == "2" and attachment.content is None
    assert attachment.filename == "report.pdf"
    assert connection.calls == [
        ("SEARCH", None, "ALL"),
        ("FETCH", "42", "(UID FLAGS BODYSTRUCTURE BODY.PEEK[HEADER])"),
        ("FETCH", "42", "(UID BODY.PEEK[1.MIME] BODY.PEEK[1])"),
    ]
    assert connection.logged_out


def test_attachment_fetch_downloads_only_requested_part():
    connection = PartsConnection()
    result = client_for(connection).fetch_attachment("INBOX", "42", 0)
    assert result.content == connection.content
    assert result.size == len(connection.content) and result.imap_section == "2"
    assert connection.calls == [
        ("FETCH", "42", "(UID BODYSTRUCTURE)"),
        ("FETCH", "42", "(UID BODY.PEEK[2.MIME] BODY.PEEK[2])"),
    ]


def test_outlook_extra_text_crlf_does_not_discard_the_message():
    connection = PartsConnection()
    header, payload = connection.parts["1"]
    connection.parts["1"] = (header, payload + b"\r\n")
    message = client_for(connection).fetch_messages("INBOX")[0]
    assert not message.parse_error and message.body_text == "Hello"
    assert message.attachments[0].filename == "report.pdf"


@pytest.mark.parametrize("mime_header", [None, b""])
def test_outlook_missing_mime_header_uses_bodystructure_charset_and_encoding(
    mime_header,
):
    connection = PartsConnection()
    payload = b"<p>Hei =C3=A6</p>"
    connection.structure = (
        b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "QUOTED-PRINTABLE" '
        + str(len(payload)).encode()
        + b" 1)"
    )
    connection.parts = {"1": (mime_header, payload)}
    message = client_for(connection).fetch_messages("INBOX")[0]
    assert not message.parse_error
    assert message.body_html == "<p>Hei \u00e6</p>"
    assert "Hei \u00e6" in message.body_text


@pytest.mark.parametrize("payload", [b"Hell", b"HelloXX", b"Hello\r\n\r\n"])
def test_other_text_size_mismatches_still_fail(payload):
    connection = PartsConnection()
    connection.parts["1"] = (connection.parts["1"][0], payload)
    assert client_for(connection).fetch_messages("INBOX")[0].parse_error


def test_extra_attachment_bytes_are_not_accepted_as_text_line_endings():
    connection = PartsConnection()
    header, payload = connection.parts["2"]
    connection.parts["2"] = (header, payload + b"\r\n")
    with pytest.raises(ValueError, match="incomplete"):
        client_for(connection).fetch_attachment("INBOX", "42", 0)


def test_explicit_uid_retry_does_not_search_or_skip_old_messages():
    connection = PartsConnection()
    result = client_for(connection).fetch_messages("INBOX", message_uids=("42", "42"))
    assert [message.uid for message in result] == ["42"]
    assert all(call[0] == "FETCH" for call in connection.calls)


@pytest.mark.parametrize("uids", [("0",), ("1:99",), ("1\r\nLOGOUT",)])
def test_explicit_retry_rejects_invalid_uid_before_connecting(uids):
    connection = PartsConnection()
    with pytest.raises(ValueError):
        client_for(connection).fetch_messages("INBOX", message_uids=uids)
    assert not connection.calls


def test_explicit_retry_with_no_uids_does_not_connect():
    connection = PartsConnection()
    assert client_for(connection).fetch_messages("INBOX", message_uids=()) == []
    assert not connection.calls and not connection.logged_out


def test_uidvalidity_change_prevents_retrying_a_reassigned_uid():
    connection = PartsConnection()
    client = client_for(connection)
    client.expect_uidvalidity("INBOX", 10)
    with pytest.raises(ValueError):
        client.fetch_messages("INBOX", message_uids=("42",))
    assert not connection.calls and connection.logged_out


def test_missing_message_during_retry_does_not_abort_later_messages():
    class RemovedConnection(PartsConnection):
        def uid(self, command, *args):
            if command == "FETCH" and args[0] == "43":
                return "OK", [None]
            return super().uid(command, *args)

    connection = RemovedConnection()
    result = client_for(connection).fetch_messages("INBOX", message_uids=("42", "43"))
    assert [message.uid for message in result] == ["42"]
    assert connection.logged_out


def test_sync_retries_failed_content_without_rewinding_uid_cursor(
    tmp_path, monkeypatch
):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    connection = PartsConnection()

    class Client(ImapClient):
        def __init__(self, settings):
            super().__init__(settings, connection_factory=lambda *a, **kw: connection)

        def fetch_recent_flags(self, *args, **kwargs):
            return []

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda *a: SimpleNamespace(
            imap=ImapSettings("imap.example.com", 993, "test", "test")
        ),
    )
    sync = MailSyncService(store, imap_client_class=Client)
    connection.truncate = True
    first = sync.fetch_imap_headers(account.id)
    assert first.messages_failed == 1
    message = store.list_unified_inbox_messages()[0]
    assert store.failed_message_uids(account.id, message.folder_id) == ("42",)
    assert store.get_folder_last_seen_uid(account.id, message.folder_id) == 42
    connection.truncate = False
    second = sync.fetch_imap_headers(account.id)
    assert second.messages_seen == 1 and not second.messages_failed
    assert store.get_message(message.id).body_text == "Hello"
    assert store.get_folder_last_seen_uid(account.id, message.folder_id) == 42
    assert store.failed_message_uids(account.id, message.folder_id) == ()
    assert sync.fetch_imap_headers(account.id).messages_seen == 0


def test_old_placeholder_cache_is_migrated_for_retry_once(tmp_path):
    path = tmp_path / "cache.sqlite3"
    store = MailStore(path)
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "INBOX")
    legacy = "Meldingsinnholdet kunne ikke hentes. Pr\u00f8v i webmail."
    failed = store.add_message(account.id, folder.id, imap_uid="42", body_text=legacy)
    normal = store.add_message(
        account.id, folder.id, imap_uid="43", body_text="Retained"
    )
    store.update_folder_last_seen_uid(account.id, folder.id, 43)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE messages DROP COLUMN body_fetch_failed")
    migrated = MailStore(path)
    assert migrated.failed_message_uids(account.id, folder.id) == ("42",)
    assert migrated.get_message(normal.id).body_text == "Retained"
    assert migrated.get_folder_last_seen_uid(account.id, folder.id) == 43
    migrated.save_message_metadata(
        account.id, folder.id, imap_uid="42", body_text="Recovered"
    )
    reopened = MailStore(path)
    assert reopened.get_message(failed.id).body_text == "Recovered"
    assert reopened.failed_message_uids(account.id, folder.id) == ()


def test_retry_queue_is_bounded_and_scoped_to_account_and_folder(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("First", "first@example.com")
    other = store.add_account("Second", "second@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    trash = store.add_folder(account.id, "Trash")
    other_inbox = store.add_folder(other.id, "INBOX")
    for owner, folder, uid in (
        (account, inbox, "1"),
        (account, inbox, "2"),
        (account, trash, "3"),
        (other, other_inbox, "4"),
    ):
        store.save_message_metadata(
            owner.id, folder.id, imap_uid=uid, body_fetch_failed=True
        )
    assert store.failed_message_uids(account.id, inbox.id, 1) == ("2",)
    assert store.failed_message_uids(account.id, inbox.id, 0) == ()
    assert store.failed_message_uids(account.id, inbox.id, 25) == ("2", "1")


def test_failed_refetch_preserves_cached_body_and_attachments(tmp_path, monkeypatch):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "INBOX", "INBOX")
    message = store.add_message(
        account.id,
        folder.id,
        imap_uid="42",
        body_text="Retained",
        body_html="<p>Retained</p>",
    )
    store.replace_message_attachments(
        message.id,
        [Attachment(0, message.id, "note.txt", "text/plain", 4, content=b"test")],
    )
    attachment = store.list_attachments(message.id)[0]
    store.update_folder_last_seen_uid(account.id, folder.id, 0)

    class FailedClient:
        def __init__(self, settings):
            pass

        def list_folders(self):
            return [ImapFolder("INBOX")]

        def fetch_messages(self, *args, **kwargs):
            return [
                ImapMessageHeader(
                    uid="42", parse_error=True, body_text="Temporary error"
                )
            ]

    monkeypatch.setattr(
        mail_sync, "get_mail_account_settings", lambda *a: SimpleNamespace(imap=None)
    )
    result = MailSyncService(store, imap_client_class=FailedClient).fetch_imap_headers(
        account.id, deletion_reconcile_limit=0, flag_refresh_limit=0
    )
    assert result.messages_failed == 1
    assert store.get_message(message.id).body_html == "<p>Retained</p>"
    assert store.get_message(message.id).body_text == "Retained"
    assert store.get_attachment_content(attachment.id) == b"test"
    assert store.failed_message_uids(account.id, folder.id) == ("42",)


@pytest.mark.parametrize("fault", ["wrong_uid", "truncate"])
def test_bad_part_response_is_rejected(fault):
    connection = PartsConnection()
    setattr(connection, fault, True)
    with pytest.raises(ValueError):
        client_for(connection).fetch_attachment("INBOX", "42", 0)
    assert connection.logged_out


def test_uidvalidity_change_prevents_any_part_fetch():
    connection = PartsConnection()
    client = client_for(connection)
    client.expect_uidvalidity("INBOX", 10)
    with pytest.raises(ValueError):
        client.fetch_attachment("INBOX", "42", 0)
    assert not connection.calls


def test_nested_multipart_inline_images_and_rfc2231_filename():
    structure = (
        b'((("TEXT" "HTML" NIL NIL NIL "7BIT" 5 1)'
        b'("IMAGE" "PNG" NIL "<logo>" NIL "BASE64" 4 NIL ("INLINE" NIL)) "RELATED")'
        b'("APPLICATION" "PDF" NIL NIL NIL "BASE64" 4 NIL '
        b'("ATTACHMENT" ("FILENAME*" "utf-8\'\'rapport%20%C3%A9.pdf"))) "MIXED")'
    )
    parsed = parse_fetch_response([b"1 (UID 1 BODYSTRUCTURE " + structure + b")"])
    parts = mime_parts(parsed[1][b"BODYSTRUCTURE"])
    assert [part.section for part in parts] == ["1.1", "1.2", "2"]
    assert [part.is_attachment for part in parts] == [False, False, True]
    assert parts[-1].headers.get_filename() == "rapport \u00e9.pdf"


def test_quoted_printable_and_singlepart_attachment():
    connection = PartsConnection()
    connection.structure = b'("TEXT" "PLAIN" NIL NIL NIL "QUOTED-PRINTABLE" 8 1 NIL ("ATTACHMENT" ("FILENAME" "note.txt")))'
    connection.parts = {
        "1": (
            b"Content-Type: text/plain\r\nContent-Disposition: attachment; filename=note.txt\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n",
            b"a=3Db=0A",
        )
    }
    result = client_for(connection).fetch_attachment("INBOX", "42", 0)
    assert result.content == b"a=b\n"


def test_mail_service_uses_selective_sync_and_persists_section(tmp_path, monkeypatch):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    connection = PartsConnection()

    class Client(ImapClient):
        def __init__(self, settings):
            super().__init__(settings, connection_factory=lambda *a, **kw: connection)

        def fetch_recent_flags(self, *args, **kwargs):
            return []

    monkeypatch.setattr(
        mail_sync,
        "get_mail_account_settings",
        lambda *args: SimpleNamespace(
            imap=ImapSettings("imap.example.com", 993, "test", "test")
        ),
    )
    result = MailSyncService(store, imap_client_class=Client).fetch_imap_headers(
        account.id
    )
    assert result.messages_seen == 1 and not result.messages_failed
    message = store.list_unified_inbox_messages()[0]
    assert store.list_attachments(message.id)[0].imap_section == "2"
    assert not store.list_attachments(message.id)[0].has_content


def test_cache_trim_preserves_local_only_attachments_and_metadata(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    folder = store.add_folder(account.id, "INBOX", "INBOX")
    store.reconcile_uidvalidity(account.id, folder.id, 9)
    ids = []
    for uid in ("1", "2", None):
        message = store.add_message(account.id, folder.id, imap_uid=uid)
        store.replace_message_attachments(
            message.id,
            [Attachment(0, message.id, "note.txt", "text/plain", 4, content=b"test")],
        )
        ids.append(store.list_attachments(message.id)[0].id)
    cache = AttachmentCache(store)
    assert cache.usage().total_bytes == 12 and cache.usage().removable_bytes == 8
    assert cache.trim(4) == 4
    assert store.get_attachment_content(ids[0]) is None
    assert store.get_attachment_content(ids[1]) == b"test"
    assert cache.trim(0, compact=True) == 4
    assert store.get_attachment_content(ids[2]) == b"test"
    assert store.list_attachments(message.id)[0].filename == "note.txt"
