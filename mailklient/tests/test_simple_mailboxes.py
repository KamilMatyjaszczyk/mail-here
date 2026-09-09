from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from mailklient.services import MailStore
from mailklient.ui.main_window import MainWindow


def test_mailbox_views_group_aliases_without_changing_source_folders(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Gmail", "test@example.com")
    expected = {"Innboks": [], "Papirkurv": [], "Søppelpost": []}
    for name, remote_id, role in (
        ("Innboks", None, "Innboks"),
        ("INBOX", "INBOX", "Innboks"),
        ("Papirkurv", "[Gmail]/Trash", "Papirkurv"),
        ("Slettede elementer", "Slettede elementer", "Papirkurv"),
        ("Spam", "[Gmail]/S&APg-ppelpost", "Søppelpost"),
        ("Junk", "Junk", "Søppelpost"),
        ("Sendt", "[Gmail]/Sent Mail", None),
        ("All e-post", "[Gmail]/All Mail", None),
        ("Projects/Inbox", "Projects/Inbox", None),
        ("Arkiv", "Archive", None),
    ):
        folder = store.add_folder(account.id, name, remote_id)
        message = store.add_message(account.id, folder.id, imap_uid="1", subject=name)
        if role:
            expected[role].append(message)

    folders_before = store.list_folders(account.id)
    for role, messages in expected.items():
        actual = store.list_mailbox_messages(account.id, role)
        assert set(actual) == set(messages)
        for message in actual:
            assert store.get_message(message.id) == message
    assert store.list_folders(account.id) == folders_before
    assert sum(store.count_messages(account.id, f.id) for f in folders_before) == 10


def test_combined_views_preserve_account_and_uid_scope(tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    messages = []
    for address in ("first@example.com", "second@example.com"):
        account = store.add_account(address, address)
        folder = store.add_folder(account.id, "Spam", "Junk")
        messages.append(store.add_message(account.id, folder.id, imap_uid="1"))
    assert set(store.list_mailbox_messages(None, "Søppelpost")) == set(messages)
    assert store.list_mailbox_messages(messages[0].account_id, "Søppelpost") == [
        messages[0]
    ]
    assert store.list_mailbox_messages(None, "Innboks") == []


def test_sidebar_has_only_three_views_and_refresh_finds_new_folders(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders("Gmail", "test@example.com")
    store.add_folder(account.id, "Custom label", "Custom label")
    store.add_folder(account.id, "All e-post", "[Gmail]/All Mail")
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=settings)
    try:
        for account_row in (0, 1):
            window.account_list.setCurrentRow(account_row)
            assert [
                window.folder_list.item(i).text()
                for i in range(window.folder_list.count())
            ] == [
                "Inbox",
                "Trash",
                "Spam",
            ]
        window.folder_list.setCurrentRow(2)
        assert window.message_list.count() == 0
        spam = store.add_folder(account.id, "Spam", "[Gmail]/Spam")
        message = store.add_message(account.id, spam.id, subject="Spam example")
        window._reload_current_folder()
        assert window.folder_list.currentItem().text() == "Spam"
        assert window._current_messages == [message]
        assert window.message_list.count() == 1
        assert not window._current_folder_is_unified
        window.account_list.setCurrentRow(0)
        window.folder_list.setCurrentRow(2)
        assert window._current_folder_is_unified
        assert window._current_messages == [message]
        app.processEvents()
    finally:
        window.close()
