from __future__ import annotations

import pytest

from mailklient.services import MailStore
from mailklient.ui.main_window import (
    _folder_display_name,
    _folder_labels,
    _is_core_folder,
)


def test_startup_removes_only_empty_local_duplicates(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders("Test", "test@example.com")
    placeholders = {f.name: f for f in store.list_folders(account.id)}
    inbox = store.add_folder(account.id, "INBOX", "INBOX")
    spam = store.add_folder(account.id, "Spam", "Junk")
    sent = store.add_folder(account.id, "[Gmail]/Sendt e-post", "[Gmail]/Sendt e-post")
    store.add_message(account.id, inbox.id, imap_uid="1")
    local_message = store.add_message(
        account.id, placeholders["Sendt"].id, subject="Local copy"
    )
    untouched_account = store.add_account_with_default_folders(
        "Other", "other@example.com"
    )

    reopened = MailStore(store.database_path)
    ids = {f.id for f in reopened.list_folders(account.id)}
    assert placeholders["Innboks"].id not in ids
    assert placeholders["Søppelpost"].id not in ids
    assert {
        inbox.id,
        spam.id,
        sent.id,
        placeholders["Sendt"].id,
        placeholders["Papirkurv"].id,
    } == ids
    assert reopened.get_message(local_message.id) == local_message
    assert len(reopened.list_folders(untouched_account.id)) == 4
    assert reopened.remove_empty_folder_placeholders() == 0


def test_sync_reuses_remote_before_local_placeholder(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders("Test", "test@example.com")
    inbox = store.add_folder(account.id, "INBOX", "INBOX")
    spam = store.add_folder(account.id, "Spam", "Junk")
    assert store.get_or_add_folder(account.id, "Innboks", "INBOX") == inbox
    assert store.get_or_add_folder(account.id, "Søppelpost", "Junk") == spam
    assert store.get_or_add_folder(account.id, "Søppelpost") == spam
    assert store.remove_empty_folder_placeholders(account.id) == 2


def test_new_spam_binding_reuses_default_folder(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders("Test", "test@example.com")
    placeholder = next(
        f for f in store.list_folders(account.id) if f.name == "Søppelpost"
    )
    assert (
        store.get_or_add_folder(account.id, "Spam", "[Gmail]/Spam").id == placeholder.id
    )
    assert len(store.list_folders(account.id)) == 4


def test_distinct_remote_mailboxes_never_share_uids(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Test", "test@example.com")
    sent = store.get_or_add_folder(account.id, "Sendt", "Sent")
    other = store.get_or_add_folder(account.id, "Sendt", "[Gmail]/Sent Mail")
    assert sent.id != other.id
    assert store.get_folder(sent.id).remote_id == "Sent"
    nested = store.get_or_add_folder(account.id, "Projects/Sent", "Projects/Sent")
    assert nested.id not in {sent.id, other.id}
    assert _folder_labels([sent, other]) == [
        "Sent (Sent)",
        "Sent ([Gmail]/Sent Mail)",
    ]


@pytest.mark.parametrize(
    ("wire_name", "label"),
    [
        ("INBOX", "Inbox"),
        ("[Gmail]/Sendt e-post", "Sent"),
        ("Sendte elementer", "Sent"),
        ("Slettede elementer", "Trash"),
        ("Deleted", "Trash"),
        ("[Gmail]/S&APg-ppelpost", "Spam"),
        ("Junk", "Spam"),
    ],
)
def test_localized_server_folders_have_consistent_labels(wire_name, label):
    assert _folder_display_name(wire_name) == label
    assert _is_core_folder(wire_name, wire_name)
