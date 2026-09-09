"""Contracts for future read tools, with a real temporary SQLite cache."""

import json
import logging
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from mailklient.config import default_database_path
from mailklient.database import connect, initialize_database
from mailklient.domain import Attachment
from mailklient.domain.errors import (
    EmailNotFound,
    InvalidSearchQuery,
    MailStoreUnavailable,
    ThreadNotFound,
)
from mailklient.domain.mail_queries import EmailFilters
from mailklient.mail.imap_client import _parse_message_header
from mailklient.services import MailStore
from mailklient.services.mail_read import MailReadService
from mailklient.services.mail_send import MailSendService
from mailklient.services.mail_sync import MailSyncService


@pytest.fixture
def mailbox(tmp_path):
    store = MailStore(tmp_path / "cache #1.sqlite3")
    account = store.add_account("Personal", "me@example.com")
    other = store.add_account("Work", "work@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    sent = store.add_folder(account.id, "Sent")
    remote = store.add_folder(other.id, "INBOX")
    first = store.add_message(
        account.id,
        inbox.id,
        message_id="<root@example.com>",
        sender='"Alice" <ALICE@example.com>',
        recipients="Me <me@example.com>",
        subject="Plan 100%_ready",
        body_text="Private agenda: Stra\u00dfe",
        received_at="2026-01-01T21:00:00Z",
    )
    second = store.add_message(
        account.id,
        inbox.id,
        message_id="<reply@example.com>",
        in_reply_to="<root@example.com>",
        references="<root@example.com>",
        sender="bob@example.com",
        recipients="me@example.com, cc@example.com",
        subject="Re: Plan",
        is_read=True,
        received_at="2026-01-02T00:30:00+02:00",
    )
    third = store.add_message(
        account.id,
        sent.id,
        message_id="<last@example.com>",
        in_reply_to="<reply@example.com>",
        sender="me@example.com",
        recipients="Alice <alice@example.com>",
        subject="Re: Plan",
        sent_at="Fri, 02 Jan 2026 00:00:00 +0000",
    )
    outside = store.add_message(
        other.id,
        remote.id,
        message_id="<root@example.com>",
        subject="Plan",
        sender="notalice@example.com",
        received_at="2026-01-03T00:00:00Z",
    )
    store.replace_message_attachments(
        first.id,
        [
            Attachment(0, first.id, "agenda.txt", "text/plain", 7, content=b"private"),
            Attachment(
                0, first.id, "remote.pdf", "application/pdf", 100, imap_section="2"
            ),
        ],
    )
    return SimpleNamespace(
        store=store,
        reader=MailReadService(store.database_path),
        account=account,
        other=other,
        inbox=inbox,
        sent=sent,
        first=first,
        second=second,
        third=third,
        outside=outside,
    )


def ids(page):
    return [item.id for item in page.items]


def test_recent_unread_sender_and_combined_filters(mailbox):
    m = mailbox
    assert ids(m.reader.get_recent_emails()) == [
        m.outside.id,
        m.third.id,
        m.second.id,
        m.first.id,
    ]
    assert ids(
        m.reader.get_unread_emails(filters=EmailFilters(account_id=m.account.id))
    ) == [m.third.id, m.first.id]
    assert ids(m.reader.get_emails_from_sender("Alice <alice@example.com>")) == [
        m.first.id
    ]
    result = m.reader.search_emails(
        "STRASSE",
        EmailFilters(
            account_id=m.account.id,
            mailbox="Inbox",
            folder_id=m.inbox.id,
            sender="alice@example.com",
            recipient="me@example.com",
            subject="Plan",
            is_read=False,
            has_attachments=True,
            after="2026-01-01",
            before="2026-01-02",
        ),
    )
    assert ids(result) == [m.first.id]
    assert ids(
        m.reader.search_emails(filters=EmailFilters(recipient="cc@example.com"))
    ) == [m.second.id]
    assert m.first.id not in ids(
        m.reader.search_emails(filters=EmailFilters(has_attachments=False))
    )
    assert ids(m.reader.search_emails(filters=EmailFilters(is_read=True))) == [
        m.second.id
    ]


@pytest.mark.parametrize("query", ["missing", "' OR 1=1 --", "_%", "Plan%"])
def test_search_is_literal_and_empty_results_are_pages(mailbox, query):
    result = mailbox.reader.search_emails(query)
    assert result.items == () and result.next_offset is None


def test_wildcards_are_not_sql_and_read_details_are_serializable(mailbox):
    m = mailbox
    assert ids(m.reader.search_emails("100%_")) == [m.first.id]
    detail = m.reader.get_email(m.first.id)
    assert detail.message == m.first
    assert detail.thread_id == m.first.id
    assert detail.to_dict()["has_attachments"] is True
    assert json.loads(json.dumps(detail.to_dict()))["message"]["id"] == m.first.id
    assert json.loads(json.dumps(m.reader.get_recent_emails().to_dict()))["items"]
    attachments = m.reader.get_attachments(m.first.id)
    assert [item.has_content for item in attachments] == [True, False]
    assert all(item.content is None for item in attachments)
    json.dumps([asdict(item) for item in attachments])
    assert m.reader.get_attachments(m.second.id) == ()


def test_dates_use_instants_inclusive_start_exclusive_end_and_sent_fallback(mailbox):
    m = mailbox
    assert ids(
        m.reader.search_emails(
            filters=EmailFilters(
                after="2026-01-01",
                before="2026-01-02",
            )
        )
    ) == [m.second.id, m.first.id]
    assert ids(
        m.reader.search_emails(
            filters=EmailFilters(
                after="2026-01-02T01:00:00+01:00",
                before="2026-01-03",
            )
        )
    ) == [m.third.id]
    undated = m.store.add_message(m.account.id, m.inbox.id, received_at="broken")
    assert ids(m.reader.get_recent_emails())[-1] == undated.id
    assert ids(m.reader.search_emails(sort_order="date_asc"))[-1] == undated.id
    assert undated.id not in ids(
        m.reader.search_emails(filters=EmailFilters(after="2020-01-01"))
    )


@pytest.mark.parametrize("sort_order", ["date_desc", "date_asc", "subject", "sender"])
def test_pagination_is_deterministic_and_complete(mailbox, sort_order):
    m = mailbox
    for _ in range(3):
        m.store.add_message(
            m.account.id, m.inbox.id, subject="same", received_at="2026-01-01"
        )
    expected = ids(m.reader.search_emails(sort_order=sort_order))
    actual, offset = [], 0
    while True:
        page = m.reader.search_emails(limit=2, offset=offset, sort_order=sort_order)
        assert len(page.items) <= 2 and page.limit == 2 and page.offset == offset
        actual.extend(ids(page))
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert actual == expected and len(set(actual)) == len(actual)
    assert m.reader.search_emails(offset=1000).items == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": None},
        {"query": "x" * 1001},
        {"query": "secret\x00"},
        {"filters": {}},
        {"filters": EmailFilters(account_id=True)},
        {"filters": EmailFilters(folder_id=-1)},
        {"filters": EmailFilters(thread_id=0)},
        {"filters": EmailFilters(is_read=1)},
        {"filters": EmailFilters(has_attachments="yes")},
        {"filters": EmailFilters(sender="Alice")},
        {"filters": EmailFilters(recipient="a@example.com, b@example.com")},
        {"filters": EmailFilters(subject="")},
        {"filters": EmailFilters(mailbox="custom")},
        {"filters": EmailFilters(after="yesterday")},
        {"filters": EmailFilters(before="2026-01-02T12:00:00")},
        {"filters": EmailFilters(after="2026-01-02", before="2026-01-02")},
        {"filters": EmailFilters(after="2026-01-03", before="2026-01-02")},
        {"limit": 0},
        {"limit": 201},
        {"limit": True},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": True},
        {"offset": 2**63},
        {"sort_order": "date; DROP TABLE messages"},
        {"sort_order": []},
    ],
)
def test_invalid_search_input(mailbox, kwargs):
    with pytest.raises(InvalidSearchQuery):
        mailbox.reader.search_emails(**kwargs)


@pytest.mark.parametrize("method", ["get_email", "get_thread", "get_attachments"])
@pytest.mark.parametrize("identifier", [None, 0, -1, "1", True, 2**63])
def test_invalid_identifiers(mailbox, method, identifier):
    with pytest.raises(InvalidSearchQuery):
        getattr(mailbox.reader, method)(identifier)


def test_unknown_ids_and_conflicting_filters(mailbox):
    for method in (mailbox.reader.get_email, mailbox.reader.get_attachments):
        with pytest.raises(EmailNotFound):
            method(999)
    with pytest.raises(ThreadNotFound):
        mailbox.reader.get_thread(999)
    assert mailbox.reader.search_emails(filters=EmailFilters(thread_id=999)).items == ()
    with pytest.raises(InvalidSearchQuery):
        mailbox.reader.get_unread_emails(filters=EmailFilters(is_read=True))
    with pytest.raises(InvalidSearchQuery):
        mailbox.reader.get_emails_from_sender(
            "a@example.com", filters=EmailFilters(sender="b@example.com")
        )


def test_threads_follow_references_across_folders_but_not_accounts(mailbox):
    m = mailbox
    unrelated = m.store.add_message(m.account.id, m.inbox.id, subject="Re: Plan")
    for message in (m.first, m.second, m.third):
        assert m.reader.get_email(message.id).thread_id == m.first.id
        assert ids(m.reader.get_thread(message.id)) == [
            m.first.id,
            m.second.id,
            m.third.id,
        ]
    assert ids(m.reader.get_thread(m.outside.id)) == [m.outside.id]
    assert ids(m.reader.get_thread(unrelated.id)) == [unrelated.id]
    assert ids(m.reader.get_thread(m.first.id, limit=1, offset=1)) == [m.second.id]
    assert m.reader.get_thread(m.first.id, offset=100).items == ()
    assert ids(
        m.reader.search_emails(filters=EmailFilters(thread_id=m.first.id, is_read=True))
    ) == [m.second.id]


def test_missing_ancestors_cycles_and_upserts(mailbox):
    m = mailbox
    a = m.store.add_message(
        m.account.id,
        m.inbox.id,
        message_id="<a@example.com>",
        references="<missing@example.com> <b@example.com>",
    )
    b = m.store.add_message(
        m.account.id,
        m.sent.id,
        message_id="<b@example.com>",
        references="<missing@example.com> <a@example.com>",
    )
    assert set(ids(m.reader.get_thread(a.id))) == {a.id, b.id}
    m.store.save_message_metadata(m.account.id, m.sent.id, message_id="<b@example.com>")
    # A still explicitly references B, so they remain linked.
    assert set(ids(m.reader.get_thread(b.id))) == {a.id, b.id}
    m.store.delete_message(a.id)
    assert ids(m.reader.get_thread(b.id)) == [b.id]
    with connect(m.store.database_path) as connection:
        assert not connection.execute(
            "SELECT 1 FROM message_references WHERE message_id = ?", (a.id,)
        ).fetchone()


def test_reading_never_writes_or_contacts_providers(mailbox, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Read API must not prompt, sync, fetch or use credentials")

    monkeypatch.setattr("keyring.get_password", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    before = mailbox.store.database_path.read_bytes()
    mailbox.reader.get_recent_emails()
    mailbox.reader.get_email(mailbox.first.id)
    mailbox.reader.get_attachments(mailbox.first.id)
    mailbox.reader.get_thread(mailbox.first.id)
    assert mailbox.store.database_path.read_bytes() == before
    assert mailbox.store.get_message(mailbox.first.id).is_read is False
    with (
        mailbox.reader._repository._connection() as connection,
        pytest.raises(sqlite3.OperationalError),
    ):
        connection.execute("DELETE FROM messages")
    assert not any(
        hasattr(mailbox.reader, name)
        for name in ("send_draft", "mark_message_read", "delete_message", "sync")
    )


def test_database_failures_are_stable_and_logs_are_private(
    mailbox, monkeypatch, caplog
):
    private = "do-not-log-password-or-mail@example.com"
    caplog.set_level(logging.INFO, logger="mailklient.services.mail_read")
    mailbox.reader.search_emails(private)
    mailbox.reader.get_attachments(mailbox.first.id)
    with pytest.raises(EmailNotFound):
        mailbox.reader.get_email(999)

    def failed(*_args, **_kwargs):
        raise sqlite3.OperationalError(private)

    monkeypatch.setattr(mailbox.reader._repository, "search", failed)
    with pytest.raises(MailStoreUnavailable) as caught:
        mailbox.reader.get_recent_emails()
    assert private not in str(caught.value) and caught.value.__suppress_context__
    records = [
        record
        for record in caplog.records
        if record.name == "mailklient.services.mail_read"
    ]
    assert [record.operation for record in records] == [
        "search_emails",
        "get_attachments",
        "get_email",
        "get_recent_emails",
    ]
    assert [record.result_count for record in records] == [0, 2, 0, 0]
    assert [record.success for record in records] == [True, True, False, False]
    assert all(
        record.duration_ms >= 0 and record.exc_info is None for record in records
    )
    assert private not in repr([record.__dict__ for record in records])


def test_missing_or_broken_cache_does_not_get_created_or_migrated(tmp_path):
    path = tmp_path / "missing" / "cache.sqlite3"
    reader = MailReadService(path)
    with pytest.raises(MailStoreUnavailable):
        reader.get_recent_emails()
    assert not path.parent.exists()
    broken = tmp_path / "broken.sqlite3"
    with sqlite3.connect(broken):
        pass
    before = broken.read_bytes()
    with pytest.raises(MailStoreUnavailable):
        MailReadService(broken).get_email(1)
    assert broken.read_bytes() == before


def test_thread_migration_preserves_legacy_mail_and_is_idempotent(mailbox):
    path = mailbox.store.database_path
    with connect(path) as connection:
        connection.execute("DROP TABLE message_references")
        connection.execute('ALTER TABLE messages DROP COLUMN "references"')
        connection.execute("ALTER TABLE messages DROP COLUMN in_reply_to")
        connection.execute("DELETE FROM schema_version WHERE version = 2")
    initialize_database(path)
    initialize_database(path)
    detail = mailbox.reader.get_email(mailbox.first.id)
    assert detail.message.subject == mailbox.first.subject
    assert detail.message.references == ""
    assert len(mailbox.reader.get_recent_emails().items) == 4
    with connect(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM message_references").fetchone()[0]
            == 4
        )


def test_imap_parser_preserves_thread_headers_and_cc():
    header = _parse_message_header(
        "1",
        (
            b"Message-ID: <child@example.com>\r\nIn-Reply-To: <parent@example.com>\r\n"
            b"References: <root@example.com>\r\n <parent@example.com>\r\n"
            b"To: me@example.com\r\nCc: cc@example.com\r\n\r\nhello"
        ),
        (),
    )
    assert header.in_reply_to == "<parent@example.com>"
    assert "<root@example.com>" in header.references
    assert "<parent@example.com>" in header.references
    assert header.recipients == "me@example.com, cc@example.com"


def test_configuration_and_headless_import(tmp_path):
    assert (
        default_database_path({"XDG_DATA_HOME": str(tmp_path)})
        == tmp_path / "mailklient/mailklient.sqlite3"
    )
    explicit = tmp_path / "server.sqlite3"
    assert (
        default_database_path(
            {"MAILKLIENT_DATABASE_PATH": str(explicit), "XDG_DATA_HOME": "/unused"}
        )
        == explicit
    )
    with pytest.raises(ValueError):
        default_database_path({"MAILKLIENT_DATABASE_PATH": "relative.db"})
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys\nfrom mailklient.services.mail_read import MailReadService\n"
                "from mailklient.config import default_database_path\n"
                "assert not any(name.startswith('PySide6') for name in sys.modules)\n"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_synced_and_local_sent_replies_keep_their_thread(mailbox, monkeypatch):
    from datetime import UTC, datetime

    from mailklient.mail import ImapFolder

    header = _parse_message_header(
        "42",
        (
            b"Message-ID: <synced@example.com>\r\nIn-Reply-To: <root@example.com>\r\n"
            b"References: <root@example.com>\r\n\r\nSynced body"
        ),
        (),
    )

    class Provider:
        def __init__(self, settings):
            pass

        def list_folders(self):
            return [ImapFolder("INBOX")]

        def fetch_headers(self, *args):
            return [header]

        def fetch_recent_flags(self, *args):
            return []

    monkeypatch.setattr(
        "mailklient.services.mail_sync.get_mail_account_settings",
        lambda *args: SimpleNamespace(imap=None),
    )
    MailSyncService(mailbox.store, imap_client_class=Provider).fetch_imap_headers(
        mailbox.account.id,
        deletion_reconcile_limit=0,
    )
    reply = next(
        item
        for item in mailbox.reader.get_thread(mailbox.first.id).items
        if item.imap_uid == "42"
    )
    assert (
        reply.in_reply_to == header.in_reply_to
        and reply.references == header.references
    )

    sender = MailSendService(mailbox.store)
    draft = sender.create_reply_draft(reply.id)
    # Exercise only sent-cache persistence, never SMTP or a real account.
    sender._save_sent_copy(
        draft, "me@example.com", "<sent@example.com>", datetime.now(UTC)
    )
    sent = next(
        item
        for item in mailbox.reader.get_thread(reply.id).items
        if item.message_id == "<sent@example.com>"
    )
    assert sent.in_reply_to == reply.message_id
    assert mailbox.reader.get_email(sent.id).thread_id == mailbox.first.id
