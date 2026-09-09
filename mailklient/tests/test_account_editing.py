from __future__ import annotations

import os
import sqlite3
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from mailklient.services import ComposeDraft, MailStore, account_settings
from mailklient.services.account_settings import AccountSettingsService
from mailklient.services.drafts import DraftService
from mailklient.ui import main_window
from mailklient.ui.account_dialog import AccountDialog


@pytest.fixture
def store(tmp_path):
    return MailStore(tmp_path / "mail.sqlite3")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_edit_preserves_cache_drafts_and_password(store, monkeypatch):
    account = store.add_account_with_default_folders("Before", "test@example.com")
    inbox = store.get_or_add_folder(account.id, "INBOX", "INBOX")
    store.reconcile_uidvalidity(account.id, inbox.id, 10)
    message = store.add_message(account.id, inbox.id, subject="Kept", imap_uid="1")
    drafts = DraftService(store)
    draft = ComposeDraft(
        account_id=account.id,
        recipients="recipient@example.com",
        subject="Draft",
        body_text="Unsent",
    )
    draft_id = drafts.save(draft)
    monkeypatch.setattr(
        account_settings,
        "get_password",
        lambda *_: pytest.fail("Do not read unchanged secrets"),
    )
    updated = AccountSettingsService(store).save(
        replace(account, display_name="After", smtp_port=465)
    )
    assert updated.id == account.id
    assert store.get_account(account.id) == updated
    assert store.get_message(message.id) == message
    assert store.folder_uidvalidity(account.id, inbox.id) == 10
    assert drafts.list_drafts()[0].id == draft_id
    assert drafts.list_drafts()[0].draft == draft


def test_incoming_server_edit_invalidates_uid_state_without_deleting_mail(store):
    account = store.add_account("Test", "test@example.com", imap_host="old.example.com")
    folder = store.add_folder(account.id, "INBOX", "INBOX")
    store.reconcile_uidvalidity(account.id, folder.id, 10)
    store.update_folder_last_seen_uid(account.id, folder.id, 99)
    message = store.add_message(account.id, folder.id, imap_uid="99")
    AccountSettingsService(store).save(replace(account, imap_host="new.example.com"))
    assert store.get_message(message.id) == message
    assert store.folder_uidvalidity(account.id, folder.id) is None
    assert store.get_folder_last_seen_uid(account.id, folder.id) == 0


def test_password_edit_uses_keyring_only_and_rolls_back_on_database_error(
    store, monkeypatch
):
    account = store.add_account("Test", "test@example.com")
    secrets = {account.email_address: "old-password"}
    monkeypatch.setattr(account_settings, "get_password", secrets.get)
    monkeypatch.setattr(account_settings, "save_password", secrets.__setitem__)
    service = AccountSettingsService(store)
    service.save(replace(account, display_name="New"), " new-password ")
    assert secrets[account.email_address] == " new-password "
    assert b"new-password" not in store.database_path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError):
        service.save(replace(account, smtp_port=-1), "rejected-password")
    assert secrets[account.email_address] == " new-password "
    assert store.get_account(account.id).display_name == "New"


def test_keyring_failure_leaves_account_unchanged(store, monkeypatch):
    account = store.add_account("Test", "test@example.com")
    monkeypatch.setattr(account_settings, "get_password", lambda *_: "old")

    def fail(*_):
        raise RuntimeError("Locked keyring")

    monkeypatch.setattr(account_settings, "save_password", fail)
    with pytest.raises(RuntimeError):
        AccountSettingsService(store).save(
            replace(account, display_name="Changed"), "new"
        )
    assert store.get_account(account.id) == account


def test_edit_rejects_identity_changes_and_never_touches_oauth_tokens(
    store, monkeypatch
):
    account = store.add_account(
        "Test", "test@example.com", auth_method="oauth2", oauth_provider="outlook"
    )
    service = AccountSettingsService(store)
    with pytest.raises(ValueError):
        service.save(replace(account, email_address="other@example.com"))
    monkeypatch.setattr(
        account_settings,
        "save_password",
        lambda *_: pytest.fail("OAuth must stay unchanged"),
    )
    service.save(replace(account, display_name="Outlook"), "ignored")
    assert store.get_account(account.id).auth_method == "oauth2"


def test_edit_dialog_prefills_custom_settings_and_keeps_identity(app, store):
    account = store.add_account(
        "Test",
        "test@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
        provider="gmail",
        imap_host="custom.example.com",
        imap_port=999,
        smtp_host="custom-smtp.example.com",
        smtp_port=465,
        smtp_security="ssl",
    )
    dialog = AccountDialog(account=account)
    data = dialog.account_data()
    assert data.imap_host == account.imap_host
    assert data.imap_port == 999
    assert data.smtp_security == "ssl"
    assert data.password is None
    assert dialog.email_address_edit.isReadOnly()
    assert not dialog.auth_method_combo.isEnabled()
    assert not dialog.provider_combo.isEnabled()
    assert not dialog.password_edit.isEnabled()
    dialog.reject()


def test_window_edits_selected_account_and_blocks_background_sync(
    app, store, monkeypatch
):
    account = store.add_account_with_default_folders(
        "Before", "test@example.com", imap_host="imap.example.com"
    )
    window = main_window.MainWindow(store)
    window._sync_timer.stop()
    window._load_accounts(select_account_id=account.id)

    def accept(dialog):
        assert window._editing_account
        window._queue_auto_sync()
        assert not window._sync_in_progress
        dialog.display_name_edit.setText("After")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AccountDialog, "exec", accept)
    window.edit_account_action.trigger()
    assert store.get_account(account.id).display_name == "After"
    assert window.account_list.currentItem().text() == "After"
    assert not window._editing_account
    window._set_send_in_progress(True)
    assert not window.edit_account_action.isEnabled()
    assert window.account_menu_button.isEnabled()
    assert window.edit_account_action in window.account_menu_button.menu().actions()
    window._set_send_in_progress(False)
    window.close()


def test_cancel_does_not_save(app, store, monkeypatch):
    account = store.add_account("Before", "test@example.com")
    window = main_window.MainWindow(store)
    window._sync_timer.stop()
    window._load_accounts(select_account_id=account.id)
    monkeypatch.setattr(AccountDialog, "exec", lambda *_: QDialog.DialogCode.Rejected)
    window._open_edit_account_dialog()
    assert store.get_account(account.id) == account
    window.close()
