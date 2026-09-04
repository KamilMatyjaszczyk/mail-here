from __future__ import annotations

import sqlite3

import pytest

from mailklient.domain import Account, Attachment, Folder, Message
from mailklient.services import MailStore


@pytest.fixture()
def store(tmp_path) -> MailStore:
    return MailStore(tmp_path / "mailklient.sqlite3")


def test_mail_store_adds_and_lists_accounts(store: MailStore) -> None:
    store.add_account("Privat", "privat@example.com")
    store.add_account("Arbeid", "arbeid@example.com")

    assert store.list_accounts() == [
        Account(id=2, display_name="Arbeid", email_address="arbeid@example.com"),
        Account(id=1, display_name="Privat", email_address="privat@example.com"),
    ]


def test_mail_store_adds_account_with_server_metadata(store: MailStore) -> None:
    store.add_account(
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

    assert store.list_accounts() == [
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


def test_mail_store_adds_and_lists_folders(store: MailStore) -> None:
    account = store.add_account("Privat", "privat@example.com")
    store.add_folder(account.id, "Sent", remote_id="[Gmail]/Sent Mail")
    store.add_folder(account.id, "Inbox")

    assert store.list_folders(account.id) == [
        Folder(id=2, account_id=account.id, name="Inbox", remote_id=None),
        Folder(
            id=1,
            account_id=account.id,
            name="Sent",
            remote_id="[Gmail]/Sent Mail",
        ),
    ]


def test_mail_store_adds_account_with_default_folders(store: MailStore) -> None:
    account = store.add_account_with_default_folders(
        "Privat",
        "privat@example.com",
    )

    assert store.list_folders(account.id) == [
        Folder(id=1, account_id=account.id, name="Innboks", remote_id=None),
        Folder(id=4, account_id=account.id, name="Papirkurv", remote_id=None),
        Folder(id=2, account_id=account.id, name="Sendt", remote_id=None),
        Folder(id=3, account_id=account.id, name="Søppelpost", remote_id=None),
    ]


def test_mail_store_adds_lists_and_updates_messages(store: MailStore) -> None:
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "Inbox")

    message = store.add_message(
        account.id,
        inbox.id,
        message_id="<message@example.com>",
        subject="Hei",
        sender="sender@example.com",
        recipients="privat@example.com",
        received_at="2026-01-03T12:00:00",
        body_preview="Kort preview",
    )

    assert store.list_messages(account.id, inbox.id) == [
        Message(
            id=message.id,
            account_id=account.id,
            folder_id=inbox.id,
            message_id="<message@example.com>",
            subject="Hei",
            sender="sender@example.com",
            recipients="privat@example.com",
            sent_at=None,
            received_at="2026-01-03T12:00:00",
            is_read=False,
            body_preview="Kort preview",
        )
    ]

    store.mark_message_read(message.id)

    assert store.list_messages(account.id, inbox.id)[0].is_read is True


def test_mail_store_lists_unified_inbox_messages(store: MailStore) -> None:
    private_account = store.add_account("Privat", "privat@example.com")
    work_account = store.add_account("Arbeid", "arbeid@example.com")
    private_inbox = store.add_folder(private_account.id, "INBOX")
    work_inbox = store.add_folder(work_account.id, "Innboks")

    store.add_message(
        private_account.id,
        private_inbox.id,
        subject="Privat",
        received_at="2026-01-03T12:00:00",
    )
    store.add_message(
        work_account.id,
        work_inbox.id,
        subject="Arbeid",
        received_at="2026-01-04T12:00:00",
    )

    assert [
        message.subject for message in store.list_unified_inbox_messages()
    ] == ["Arbeid", "Privat"]


def test_mail_store_preserves_data_between_instances(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"
    first_store = MailStore(database_path)
    first_store.add_account("Privat", "privat@example.com")

    second_store = MailStore(database_path)

    assert second_store.list_accounts() == [
        Account(id=1, display_name="Privat", email_address="privat@example.com")
    ]


def test_mail_store_surfaces_database_constraints(store: MailStore) -> None:
    store.add_account("Privat", "privat@example.com")

    with pytest.raises(sqlite3.IntegrityError):
        store.add_account("Duplikat", "privat@example.com")


def test_mail_store_deletes_account_with_cached_data(store: MailStore) -> None:
    account = store.add_account_with_default_folders("Privat", "privat@example.com")
    inbox = store.list_folders(account.id)[1]
    store.add_message(account.id, inbox.id, subject="Hei")

    assert store.delete_account(account.id)
    assert store.list_accounts() == []
    assert store.list_folders(account.id) == []


def test_mail_store_tracks_folder_last_seen_uid(store: MailStore) -> None:
    account = store.add_account("Privat", "privat@example.com")
    folder = store.add_folder(account.id, "Inbox")

    assert store.get_folder_last_seen_uid(account.id, folder.id) == 0

    store.update_folder_last_seen_uid(account.id, folder.id, 42)

    assert store.get_folder_last_seen_uid(account.id, folder.id) == 42


def test_mail_store_replaces_and_lists_message_attachments(
    store: MailStore,
) -> None:
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "Inbox")
    message = store.add_message(account.id, inbox.id, subject="Vedlegg")

    store.replace_message_attachments(
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

    assert store.list_attachments(message.id) == [
        Attachment(
            id=1,
            message_id=message.id,
            filename="rapport.pdf",
            content_type="application/pdf",
            size=1234,
            has_content=True,
        )
    ]
    assert store.get_attachment_content(1) == b"pdf-bytes"


def test_mail_store_moves_deletes_and_prunes_messages(store: MailStore) -> None:
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "Inbox")
    archive = store.add_folder(account.id, "Arkiv")
    kept = store.add_message(account.id, inbox.id, imap_uid="1", subject="Behold")
    removed = store.add_message(account.id, inbox.id, imap_uid="2", subject="Fjern")

    assert store.move_message_to_folder(kept.id, archive.id)
    assert store.delete_messages_missing_from_folder(account.id, inbox.id, ["3"]) == 1

    assert store.get_message(kept.id).folder_id == archive.id
    assert store.get_message(removed.id) is None
    assert store.delete_message(kept.id)
    assert store.get_message(kept.id) is None
