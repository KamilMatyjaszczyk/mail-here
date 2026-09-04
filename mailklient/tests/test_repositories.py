from __future__ import annotations

import sqlite3

import pytest

from mailklient.database import (
    connect,
    create_account,
    create_folder,
    create_message,
    delete_account,
    delete_message,
    get_attachment_content,
    get_folder_last_seen_uid,
    initialize_database,
    list_attachments_for_message,
    list_accounts,
    list_folders,
    list_messages_for_folder,
    list_unified_inbox_messages,
    mark_message_read,
    move_message_to_folder,
    replace_message_attachments,
    set_attachment_content,
    update_folder_last_seen_uid,
    upsert_message,
)
from mailklient.domain import Account, Attachment, Folder, Message


@pytest.fixture()
def connection(tmp_path):
    database_path = tmp_path / "mailklient.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as database_connection:
        yield database_connection


def test_create_and_list_accounts(connection) -> None:
    create_account(connection, "Privat", "privat@example.com")
    create_account(connection, "Arbeid", "arbeid@example.com")

    accounts = list_accounts(connection)

    assert accounts == [
        Account(id=2, display_name="Arbeid", email_address="arbeid@example.com"),
        Account(id=1, display_name="Privat", email_address="privat@example.com"),
    ]


def test_create_account_stores_server_metadata(connection) -> None:
    create_account(
        connection,
        "Privat",
        "privat@example.com",
        username="privatbruker",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
    )

    assert list_accounts(connection) == [
        Account(
            id=1,
            display_name="Privat",
            email_address="privat@example.com",
            username="privatbruker",
            imap_host="imap.example.com",
            imap_port=993,
            imap_security="ssl",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_security="starttls",
        )
    ]


def test_create_account_stores_auth_method(connection) -> None:
    create_account(
        connection,
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
    )

    assert list_accounts(connection) == [
        Account(
            id=1,
            display_name="Privat",
            email_address="privat@example.com",
            auth_method="oauth2",
            oauth_provider="gmail",
        )
    ]


def test_create_account_rejects_invalid_ports(connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_account(
            connection,
            "Privat",
            "privat@example.com",
            imap_port=70000,
        )


def test_create_account_rejects_invalid_security_mode(connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_account(
            connection,
            "Privat",
            "privat@example.com",
            smtp_security="plain",
        )


def test_create_account_rejects_invalid_auth_method(connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_account(
            connection,
            "Privat",
            "privat@example.com",
            auth_method="token",
        )


def test_create_account_rejects_invalid_oauth_provider(connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_account(
            connection,
            "Privat",
            "privat@example.com",
            auth_method="oauth2",
            oauth_provider="example",
        )


def test_delete_account_removes_account_folders_and_messages(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    folder = create_folder(connection, account.id, "Inbox")
    create_message(connection, account.id, folder.id, subject="Hei")

    assert delete_account(connection, account.id)
    assert list_accounts(connection) == []
    assert list_folders(connection, account.id) == []
    assert list_messages_for_folder(connection, account.id, folder.id) == []


def test_folder_last_seen_uid_defaults_to_zero(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    folder = create_folder(connection, account.id, "Inbox")

    assert get_folder_last_seen_uid(connection, account.id, folder.id) == 0


def test_folder_last_seen_uid_can_be_saved(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    folder = create_folder(connection, account.id, "Inbox")

    update_folder_last_seen_uid(connection, account.id, folder.id, 42)

    assert get_folder_last_seen_uid(connection, account.id, folder.id) == 42


def test_folder_last_seen_uid_never_moves_backwards(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    folder = create_folder(connection, account.id, "Inbox")

    update_folder_last_seen_uid(connection, account.id, folder.id, 42)
    update_folder_last_seen_uid(connection, account.id, folder.id, 10)

    assert get_folder_last_seen_uid(connection, account.id, folder.id) == 42


def test_folder_last_seen_uid_falls_back_to_cached_messages(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    folder = create_folder(connection, account.id, "Inbox")
    create_message(connection, account.id, folder.id, imap_uid="41")
    create_message(connection, account.id, folder.id, imap_uid="42")

    assert get_folder_last_seen_uid(connection, account.id, folder.id) == 42


def test_delete_account_returns_false_for_missing_account(connection) -> None:
    assert not delete_account(connection, 999)


def test_create_account_rejects_duplicate_email_address(connection) -> None:
    create_account(connection, "Privat", "person@example.com")

    with pytest.raises(sqlite3.IntegrityError):
        create_account(connection, "Jobb", "person@example.com")


def test_create_and_list_folders_for_account(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    other_account = create_account(connection, "Arbeid", "arbeid@example.com")

    create_folder(connection, account.id, "Sent", remote_id="[Gmail]/Sent Mail")
    create_folder(connection, account.id, "Inbox")
    create_folder(connection, other_account.id, "Archive")

    folders = list_folders(connection, account.id)

    assert folders == [
        Folder(id=2, account_id=account.id, name="Inbox", remote_id=None),
        Folder(
            id=1,
            account_id=account.id,
            name="Sent",
            remote_id="[Gmail]/Sent Mail",
        ),
    ]


def test_create_folder_rejects_duplicate_name_for_same_account(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    create_folder(connection, account.id, "Inbox")

    with pytest.raises(sqlite3.IntegrityError):
        create_folder(connection, account.id, "Inbox")


def test_create_and_list_messages_for_folder(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    archive = create_folder(connection, account.id, "Archive")

    create_message(
        connection,
        account.id,
        inbox.id,
        message_id="<old@example.com>",
        subject="Eldre melding",
        sender="old@example.com",
        recipients="privat@example.com",
        received_at="2026-01-01T12:00:00",
        body_preview="Kort preview",
    )
    create_message(
        connection,
        account.id,
        archive.id,
        message_id="<archive@example.com>",
        subject="Arkivert",
        sender="archive@example.com",
        recipients="privat@example.com",
        received_at="2026-01-02T12:00:00",
    )
    create_message(
        connection,
        account.id,
        inbox.id,
        message_id="<new@example.com>",
        subject="Nyere melding",
        sender="new@example.com",
        recipients="privat@example.com",
        received_at="2026-01-03T12:00:00",
        is_read=True,
    )

    messages = list_messages_for_folder(connection, account.id, inbox.id)

    assert messages == [
        Message(
            id=3,
            account_id=account.id,
            folder_id=inbox.id,
            imap_uid=None,
            flags="",
            message_id="<new@example.com>",
            subject="Nyere melding",
            sender="new@example.com",
            recipients="privat@example.com",
            sent_at=None,
            received_at="2026-01-03T12:00:00",
            is_read=True,
            body_preview="",
        ),
        Message(
            id=1,
            account_id=account.id,
            folder_id=inbox.id,
            imap_uid=None,
            flags="",
            message_id="<old@example.com>",
            subject="Eldre melding",
            sender="old@example.com",
            recipients="privat@example.com",
            sent_at=None,
            received_at="2026-01-01T12:00:00",
            is_read=False,
            body_preview="Kort preview",
        ),
    ]


def test_list_unified_inbox_messages_across_accounts(connection) -> None:
    private_account = create_account(connection, "Privat", "privat@example.com")
    work_account = create_account(connection, "Arbeid", "arbeid@example.com")
    private_inbox = create_folder(connection, private_account.id, "INBOX")
    work_inbox = create_folder(connection, work_account.id, "Innboks")
    archive = create_folder(connection, private_account.id, "Archive")

    create_message(
        connection,
        private_account.id,
        private_inbox.id,
        subject="Privat",
        received_at="2026-01-03T12:00:00",
    )
    create_message(
        connection,
        work_account.id,
        work_inbox.id,
        subject="Arbeid",
        received_at="2026-01-04T12:00:00",
    )
    create_message(
        connection,
        private_account.id,
        archive.id,
        subject="Arkivert",
        received_at="2026-01-05T12:00:00",
    )

    messages = list_unified_inbox_messages(connection)

    assert [message.subject for message in messages] == ["Arbeid", "Privat"]


def test_list_unified_inbox_messages_respects_limit(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "INBOX")

    create_message(
        connection,
        account.id,
        inbox.id,
        subject="Eldst",
        received_at="2026-01-03T12:00:00",
    )
    create_message(
        connection,
        account.id,
        inbox.id,
        subject="Nyest",
        received_at="2026-01-04T12:00:00",
    )

    messages = list_unified_inbox_messages(connection, limit=1)

    assert [message.subject for message in messages] == ["Nyest"]


def test_list_unified_inbox_messages_matches_inbox_case_insensitively(
    connection,
) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "inbox")

    create_message(
        connection,
        account.id,
        inbox.id,
        subject="Lowercase inbox",
        received_at="2026-01-03T12:00:00",
    )

    messages = list_unified_inbox_messages(connection)

    assert [message.subject for message in messages] == ["Lowercase inbox"]


def test_mark_message_read_updates_read_state(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    message = create_message(connection, account.id, inbox.id, subject="Hei")

    mark_message_read(connection, message.id)

    messages = list_messages_for_folder(connection, account.id, inbox.id)

    assert messages[0].is_read is True


def test_replace_and_list_message_attachments(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    message = create_message(connection, account.id, inbox.id, subject="Vedlegg")

    replace_message_attachments(
        connection,
        message.id,
        [
            Attachment(
                id=0,
                message_id=message.id,
                filename="rapport.pdf",
                content_type="application/pdf",
                size=1234,
                has_content=True,
                content=b"pdf-bytes",
            )
        ],
    )
    replace_message_attachments(
        connection,
        message.id,
        [
            Attachment(
                id=0,
                message_id=message.id,
                filename="bilde.png",
                content_type="image/png",
                size=42,
                content_id="image-1",
                is_inline=False,
            )
        ],
    )

    attachments = list_attachments_for_message(connection, message.id)

    assert attachments == [
        Attachment(
            id=2,
            message_id=message.id,
            filename="bilde.png",
            content_type="image/png",
            size=42,
            content_id="image-1",
            is_inline=False,
            has_content=False,
        )
    ]


def test_get_and_set_attachment_content(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    message = create_message(connection, account.id, inbox.id, subject="Vedlegg")

    replace_message_attachments(
        connection,
        message.id,
        [
            Attachment(
                id=0,
                message_id=message.id,
                filename="rapport.pdf",
                content_type="application/pdf",
                size=0,
            )
        ],
    )
    attachment = list_attachments_for_message(connection, message.id)[0]

    assert get_attachment_content(connection, attachment.id) is None

    set_attachment_content(connection, attachment.id, b"pdf-bytes")

    stored_attachment = list_attachments_for_message(connection, message.id)[0]
    assert get_attachment_content(connection, attachment.id) == b"pdf-bytes"
    assert stored_attachment.size == len(b"pdf-bytes")
    assert stored_attachment.has_content is True


def test_move_and_delete_message(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    archive = create_folder(connection, account.id, "Arkiv")
    message = create_message(connection, account.id, inbox.id, subject="Hei")

    assert move_message_to_folder(connection, message.id, archive.id)

    assert list_messages_for_folder(connection, account.id, inbox.id) == []
    assert list_messages_for_folder(connection, account.id, archive.id)[0].id == (
        message.id
    )

    assert delete_message(connection, message.id)
    assert list_messages_for_folder(connection, account.id, archive.id) == []


def test_message_rejects_duplicate_remote_message_id_in_same_folder(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    create_message(
        connection,
        account.id,
        inbox.id,
        message_id="<same@example.com>",
    )

    with pytest.raises(sqlite3.IntegrityError):
        create_message(
            connection,
            account.id,
            inbox.id,
            message_id="<same@example.com>",
        )


def test_message_rejects_folder_from_another_account(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    other_account = create_account(connection, "Arbeid", "arbeid@example.com")
    other_inbox = create_folder(connection, other_account.id, "Inbox")

    with pytest.raises(sqlite3.IntegrityError):
        create_message(connection, account.id, other_inbox.id, subject="Feil konto")


def test_upsert_message_updates_existing_message_by_imap_uid(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")

    first_message = upsert_message(
        connection,
        account.id,
        inbox.id,
        imap_uid="42",
        flags="",
        message_id="<old@example.com>",
        subject="Gammelt emne",
    )
    second_message = upsert_message(
        connection,
        account.id,
        inbox.id,
        imap_uid="42",
        flags="\\Seen",
        message_id="<new@example.com>",
        subject="Nytt emne",
        is_read=True,
    )

    messages = list_messages_for_folder(connection, account.id, inbox.id)

    assert first_message.id == second_message.id
    assert messages == [
        Message(
            id=first_message.id,
            account_id=account.id,
            folder_id=inbox.id,
            imap_uid="42",
            flags="\\Seen",
            message_id="<new@example.com>",
            subject="Nytt emne",
            is_read=True,
        )
    ]
