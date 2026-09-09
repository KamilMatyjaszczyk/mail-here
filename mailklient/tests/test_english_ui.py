"""English presentation must not rename cached folders or translate email content."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QMessageBox

from mailklient.domain import Attachment
from mailklient.services import ComposeDraft, HeaderSyncResult, MailStore
from mailklient.ui.account_dialog import AccountDialog
from mailklient.ui.attachment_controller import (
    _attachment_preview_html,
    _download_directory,
)
from mailklient.ui.compose_dialog import ComposeDialog
from mailklient.ui.main_window import MainWindow
from mailklient.ui.oauth_settings_dialog import OAuthSettingsDialog
from mailklient.ui.presentation import _folder_display_name


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_english_window_preserves_existing_folders_and_message_content(app, tmp_path):
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders("Personlig", "user@example.com")
    folders = store.list_folders(account.id)
    inbox = next(folder for folder in folders if folder.name == "Innboks")
    original = store.add_message(
        account.id,
        inbox.id,
        subject="Norsk emne",
        sender="sender@example.com",
        recipients=account.email_address,
        body_text="Hei! Dette skal ikke oversettes.",
        body_html="<p>Hei! Dette skal ikke oversettes.</p>",
        received_at="2026-09-09T12:30:00",
    )
    store.replace_message_attachments(
        original.id,
        [Attachment(0, original.id, "notater.txt", "text/plain", 4, content=b"test")],
    )
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=settings)
    try:
        assert [action.text() for action in window.menuBar().actions()] == [
            "Account",
            "Message",
        ]
        assert window.account_list.item(0).text() == "All inboxes"
        assert window.account_list.item(1).text() == "Personlig"
        assert [window.folder_list.item(i).text() for i in range(3)] == [
            "Inbox",
            "Trash",
            "Spam",
        ]
        assert window.remote_content_checkbox.text() == "External content"
        assert window.message_search_edit.placeholderText() == "Search"
        assert window.reply_button.toolTip() == "Reply to selected email"
        assert window._column_handles["messages"].toolTip() == "Drag to move the column"
        window.message_list.setCurrentRow(0)
        app.processEvents()
        assert window.attachments_button.text() == "1 attachment"
        assert store.list_attachments(original.id)[0].filename == "notater.txt"
        rendered = window.message_view.rendered_html()
        assert '<html lang="en">' in rendered
        assert "Account: user@example.com" in rendered
        assert "2026-09-09 12:30" in rendered
        assert original.body_html in rendered
        assert "Subject: Norsk emne" in window.message_view.toPlainText()
        assert store.get_message(original.id) == original
        assert store.list_folders(account.id) == folders
        assert _folder_display_name("Sendt") == "Sent"
        assert _folder_display_name("Ferier") == "Ferier"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_account_compose_and_oauth_dialog_labels_are_english(
    app, tmp_path, monkeypatch
):
    from mailklient.ui import oauth_settings_dialog

    monkeypatch.setattr(
        oauth_settings_dialog, "load_oauth_client_config", lambda _provider: ("", None)
    )
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account("Personal", "user@example.com")
    account_dialog = AccountDialog(account=account)
    oauth_dialog = OAuthSettingsDialog()
    compose = ComposeDialog([account], ComposeDraft(account_id=account.id))
    try:
        assert account_dialog.windowTitle() == "Edit account"
        assert account_dialog.auth_method_combo.itemText(0) == "Password"
        assert account_dialog.password_edit.placeholderText() == "Unchanged"
        assert (
            account_dialog.button_box.button(
                QDialogButtonBox.StandardButton.Cancel
            ).text()
            == "Cancel"
        )
        assert oauth_dialog.windowTitle() == "OAuth app settings"
        assert (
            oauth_dialog.buttons.button(QDialogButtonBox.StandardButton.Save).text()
            == "Save"
        )
        assert compose.windowTitle() == "New email"
        assert compose.bold_button.toolTip() == "Bold"
        assert compose.add_attachment_button.text() == "Attach..."
        assert compose.recipients_edit.placeholderText() == "recipient@example.com"
    finally:
        for dialog in (account_dialog, oauth_dialog, compose):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("location", ["/tmp/Custom downloads", ""])
def test_download_folder_follows_system_settings(monkeypatch, location):
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda _kind: location)
    assert _download_directory() == (
        Path(location) if location else Path.home() / "Downloads"
    )


@pytest.fixture
def status_window(app, tmp_path):
    store = MailStore(tmp_path / "status.sqlite3")
    account = store.add_account_with_default_folders("Personal", "user@example.com")
    settings = QSettings(str(tmp_path / "status.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=settings)
    try:
        yield window, account
    finally:
        window._manual_sync_batch = False
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("folders", "messages", "failed", "expected"),
    [
        (0, 0, 0, "Synced 0 folders and 0 messages."),
        (1, 1, 0, "Synced 1 folder and 1 message."),
        (2, 3, 0, "Synced 2 folders and 3 messages."),
        (1, 2, 1, "Synced 1 folder and 2 messages. 1 message could not be parsed."),
        (2, 3, 2, "Synced 2 folders and 3 messages. 2 messages could not be parsed."),
    ],
)
def test_sync_status_uses_english_singular_and_plural(
    status_window, folders, messages, failed, expected
):
    window, account = status_window
    window._manual_sync_batch = True
    window._handle_sync_finished(
        account.id, HeaderSyncResult(folders, messages, failed)
    )
    assert window.sync_status_label.text() == expected
    if failed:
        assert window._sync_queue_failures == [
            f"Personal: {expected.split('. ', 1)[1]}"
        ]


@pytest.mark.parametrize(("count", "noun"), [(1, "account"), (2, "accounts")])
def test_failed_account_summary_uses_english_singular_and_plural(
    status_window, monkeypatch, count, noun
):
    window, _account = status_window
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    window._manual_sync_batch = True
    window._sync_queue_failures = ["Test failure"] * count
    window._next_queued_sync()
    assert window.sync_status_label.text() == (
        f"Sync completed with errors for {count} {noun}."
    )


@pytest.mark.parametrize(("count", "noun"), [(1, "attachment"), (2, "attachments")])
def test_save_all_status_uses_english_singular_and_plural(
    status_window, tmp_path, monkeypatch, count, noun
):
    window, account = status_window
    store = window._mail_store
    inbox = next(f for f in store.list_folders(account.id) if f.name == "Innboks")
    message = store.add_message(account.id, inbox.id)
    store.replace_message_attachments(
        message.id,
        [
            Attachment(0, message.id, f"note-{i}.txt", "text/plain", 4, content=b"test")
            for i in range(count)
        ],
    )
    window._show_attachments(store.list_attachments(message.id))
    directory = tmp_path / "downloads"
    directory.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *_args: str(directory)
    )

    def run_task(_status, work, finished):
        finished(work())
        return True

    monkeypatch.setattr(window, "_start_task", run_task)
    assert window._save_all_available_attachments()
    assert window.sync_status_label.text() == f"Saved {count} {noun}."
    assert len(list(directory.iterdir())) == count


@pytest.mark.parametrize(
    ("count", "expected"), [(None, "PDF document"), (1, "1 page"), (2, "2 pages")]
)
def test_pdf_page_count_uses_english_singular_and_plural(monkeypatch, count, expected):
    from mailklient.ui import attachment_controller

    monkeypatch.setattr(
        attachment_controller, "_guess_pdf_page_count", lambda _body: count
    )
    attachment = Attachment(0, 0, "document.pdf", "application/pdf", 0)
    assert f"<p>{expected}. Use Open" in _attachment_preview_html(attachment, b"")
