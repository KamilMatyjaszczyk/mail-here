from __future__ import annotations

import sqlite3

import pytest

from mailklient.database import (
    connect,
    create_account,
    create_folder,
    create_message,
    delete_account,
    initialize_database,
    list_accounts,
    list_folders,
    list_messages_for_folder,
    mark_message_read,
    upsert_message,
)
from mailklient.domain import Account, Folder, Message


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


def test_mark_message_read_updates_read_state(connection) -> None:
    account = create_account(connection, "Privat", "privat@example.com")
    inbox = create_folder(connection, account.id, "Inbox")
    message = create_message(connection, account.id, inbox.id, subject="Hei")

    mark_message_read(connection, message.id)

    messages = list_messages_for_folder(connection, account.id, inbox.id)

    assert messages[0].is_read is True


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
