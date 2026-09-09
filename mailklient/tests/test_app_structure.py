from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QWidget,
)

from mailklient.domain import Account, Attachment
from mailklient.main import default_database_path, main
from mailklient.security import OAuthTokens
from mailklient.services import (
    ComposeDraft,
    HeaderSyncResult,
    MailStore,
    SendResult,
    seed_demo_data,
)
from mailklient.ui import attachment_controller, main_window
from mailklient.ui import message_viewer as message_viewer_module
from mailklient.ui.main_window import MainWindow
from mailklient.ui.message_viewer import MessageViewer


def _get_qapplication() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _process_events_until(predicate, timeout_seconds: float = 2.0) -> None:
    app = _get_qapplication()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out while waiting for Qt event processing")


def _message_list_item_text(message_list: QListWidget, index: int) -> str:
    return message_list.item(index).data(main_window.MESSAGE_TEXT_ROLE)


class FakeMailSyncService:
    def __init__(self) -> None:
        self.imap_tested_account_id: int | None = None
        self.smtp_tested_account_id: int | None = None
        self.synced_account_id: int | None = None
        self.marked_messages: list[tuple[int, bool]] = []
        self.trashed_messages: list[int] = []
        self.archived_messages: list[int] = []
        self.moved_messages: list[tuple[int, int]] = []

    def test_imap_connection(self, account_id: int) -> bool:
        self.imap_tested_account_id = account_id
        return True

    def test_smtp_connection(self, account_id: int) -> bool:
        self.smtp_tested_account_id = account_id
        return True

    def fetch_imap_headers(self, account_id: int, **_kwargs) -> HeaderSyncResult:
        self.synced_account_id = account_id
        return HeaderSyncResult(folders_seen=2, messages_seen=3)

    def mark_message_read(self, message_id: int, is_read: bool = True) -> bool:
        self.marked_messages.append((message_id, is_read))
        return True

    def move_message_to_trash(self, message_id: int) -> bool:
        self.trashed_messages.append(message_id)
        return True

    def archive_message(self, message_id: int) -> bool:
        self.archived_messages.append(message_id)
        return True

    def move_message_to_folder(
        self,
        message_id: int,
        destination_folder_id: int,
    ) -> bool:
        self.moved_messages.append((message_id, destination_folder_id))
        return True


class FakeOAuthLoginService:
    def __init__(self) -> None:
        self.authorized: list[tuple[str, str, str | None]] = []

    def authorize(
        self,
        provider: str,
        client_id: str,
        *,
        client_secret: str | None = None,
    ) -> OAuthTokens:
        self.authorized.append((provider, client_id, client_secret))
        return OAuthTokens(access_token="access", refresh_token="refresh")


class FakeMailSendService:
    def __init__(self) -> None:
        self.sent_drafts: list[ComposeDraft] = []
        self.reply_drafts: list[int] = []
        self.forward_drafts: list[int] = []

    def send_draft(self, draft: ComposeDraft) -> bool:
        self.sent_drafts.append(draft)
        return True

    def create_reply_draft(self, message_id: int) -> ComposeDraft:
        self.reply_drafts.append(message_id)
        return ComposeDraft(
            account_id=2,
            recipients="sender@example.com",
            subject="Re: Test",
            body_text="\n\n> Original",
            in_reply_to="<original@example.com>",
        )

    def create_forward_draft(self, message_id: int) -> ComposeDraft:
        self.forward_drafts.append(message_id)
        return ComposeDraft(
            account_id=2,
            subject="Fwd: Test",
            body_text="---------- Forwarded message ----------",
        )


class FakeComposeDialog:
    DialogCode = QDialog.DialogCode
    shown_drafts: list[ComposeDraft] = []

    def __init__(self, _accounts, draft: ComposeDraft, _parent=None, **kwargs) -> None:
        self._draft = draft
        self.draft_id = kwargs.get("draft_id")
        self.shown_drafts.append(draft)

    def exec(self):
        return self.DialogCode.Accepted

    def draft(self) -> ComposeDraft:
        return self._draft


def test_entry_point_is_callable() -> None:
    assert callable(main)
    assert callable(default_database_path)


def test_main_window_defaults_and_store_backed_layout(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    seed_demo_data(store)

    window = MainWindow(store)

    assert window.windowTitle() == "mcpMail"
    assert window._preferences.organizationName() == "Mailklient"
    assert window._preferences.applicationName() == "Mailklient"
    assert window.size().width() == 1200
    assert window.size().height() == 750

    central_widget = window.centralWidget()
    assert isinstance(central_widget, QSplitter)
    assert central_widget.count() == 3

    folder_panel = window.findChild(QWidget, "folder_panel")
    account_list = window.findChild(QListWidget, "account_list")
    folder_list = window.findChild(QListWidget, "folder_list")
    message_list = window.findChild(QListWidget, "message_list")
    message_view = window.findChild(MessageViewer, "message_view")
    add_account_action = window.findChild(QAction, "add_account_action")
    delete_account_action = window.findChild(QAction, "delete_account_action")
    test_imap_action = window.findChild(QAction, "test_imap_action")
    test_smtp_action = window.findChild(QAction, "test_smtp_action")
    oauth_login_action = window.findChild(QAction, "oauth_login_action")
    sync_account_action = window.findChild(QAction, "sync_account_action")
    compose_action = window.findChild(QAction, "compose_action")
    reply_action = window.findChild(QAction, "reply_action")
    forward_action = window.findChild(QAction, "forward_action")
    account_menu_button = window.findChild(QToolButton, "account_menu_button")
    compose_button = window.findChild(QToolButton, "compose_button")
    reply_button = window.findChild(QToolButton, "reply_button")
    forward_button = window.findChild(QToolButton, "forward_button")
    sync_status_label = window.findChild(QLabel, "sync_status_label")

    assert folder_panel is not None
    assert account_list is not None
    assert folder_list is not None
    assert message_list is not None
    assert message_view is not None
    assert add_account_action is not None
    assert delete_account_action is not None
    assert test_imap_action is not None
    assert test_smtp_action is not None
    assert oauth_login_action is not None
    assert sync_account_action is not None
    assert compose_action is not None
    assert reply_action is not None
    assert forward_action is not None
    assert account_menu_button is not None
    assert account_menu_button.menu() is window.account_menu
    assert compose_button is not None
    assert reply_button is not None
    assert forward_button is not None
    assert sync_status_label is not None
    assert not test_imap_action.isEnabled()
    assert not test_smtp_action.isEnabled()
    assert not oauth_login_action.isEnabled()
    assert not sync_account_action.isEnabled()
    assert compose_button.isEnabled()
    assert not reply_button.isEnabled()
    assert not forward_button.isEnabled()
    assert [account_list.item(index).text() for index in range(account_list.count())] == [
        "All inboxes",
        "Demo",
    ]
    assert [folder_list.item(index).text() for index in range(folder_list.count())] == [
        "Inbox",
        "Trash",
        "Spam",
    ]
    assert message_list.count() == 2
    assert _message_list_item_text(message_list, 0) == (
        "demo@example.com\n"
        "Welcome to mcpMail\n"
        "This is local demo data from SQLite."
    )
    assert message_view.isReadOnly()
    assert message_view.toPlainText() == "Select a message"

    window.close()


def test_main_window_can_reorder_columns(tmp_path) -> None:
    app = _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    seed_demo_data(store)

    window = MainWindow(store)
    window.show()
    app.processEvents()

    splitter = window.findChild(QSplitter, "main_splitter")
    folders_panel = window.findChild(QWidget, "folder_panel")
    folders_handle = window.findChild(QLabel, "column_drag_handle_folders")
    reader_handle = window.findChild(QLabel, "column_drag_handle_reader")
    messages_panel = window.findChild(QWidget, "message_list_panel")

    assert splitter is not None
    assert folders_panel is not None
    assert folders_handle is not None
    assert reader_handle is not None
    assert messages_panel is not None
    assert [splitter.widget(index).objectName() for index in range(splitter.count())] == [
        "folder_panel",
        "message_view_panel",
        "message_list_panel",
    ]

    window._begin_column_drag("folders")
    window._preview_column_drop(
        "folders",
        messages_panel.mapToGlobal(QPoint(8, messages_panel.height() // 2)),
    )
    app.processEvents()

    assert folders_panel.property("dragSource") is True
    assert folders_handle.property("dragging") is True
    assert messages_panel.property("dropPreview") == "before"

    window._finish_column_drag(
        "folders",
        window.mapToGlobal(QPoint(-40, -40)),
        commit=True,
    )
    app.processEvents()

    assert messages_panel.property("dropPreview") == ""
    assert folders_handle.property("dragging") is False
    assert [splitter.widget(index).objectName() for index in range(splitter.count())] == [
        "folder_panel",
        "message_view_panel",
        "message_list_panel",
    ]

    window._begin_column_drag("folders")
    window._finish_column_drag(
        "folders",
        messages_panel.mapToGlobal(
            QPoint(messages_panel.width() - 5, messages_panel.height() // 2)
        ),
        commit=True,
    )
    app.processEvents()

    assert [splitter.widget(index).objectName() for index in range(splitter.count())] == [
        "message_view_panel",
        "message_list_panel",
        "folder_panel",
    ]

    current_order = [
        splitter.widget(index).objectName() for index in range(splitter.count())
    ]
    window._begin_column_drag("reader")
    window._finish_column_drag(
        "reader",
        window.mapToGlobal(QPoint(-40, -40)),
        commit=True,
    )
    app.processEvents()

    assert [splitter.widget(index).objectName() for index in range(splitter.count())] == [
        *current_order,
    ]

    window._begin_column_drag("reader")
    window._finish_column_drag(
        "reader",
        messages_panel.mapToGlobal(QPoint(8, messages_panel.height() // 2)),
        commit=False,
    )
    app.processEvents()

    assert [splitter.widget(index).objectName() for index in range(splitter.count())] == [
        *current_order,
    ]

    window.close()


def test_main_window_disables_account_actions_without_accounts(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")

    window = MainWindow(store)

    assert not window.findChild(QAction, "delete_account_action").isEnabled()
    assert not window.findChild(QAction, "test_imap_action").isEnabled()
    assert not window.findChild(QAction, "test_smtp_action").isEnabled()
    assert not window.findChild(QAction, "oauth_login_action").isEnabled()
    assert not window.findChild(QAction, "sync_account_action").isEnabled()
    assert not window.findChild(QToolButton, "compose_button").isEnabled()

    window.close()


def test_main_window_can_add_local_account(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    seed_demo_data(store)

    window = MainWindow(store)

    assert window._add_account("Privat", "privat@example.com")

    folder_list = window.findChild(QListWidget, "folder_list")
    account_list = window.findChild(QListWidget, "account_list")

    assert folder_list is not None
    assert account_list is not None
    assert [account_list.item(index).text() for index in range(account_list.count())] == [
        "All inboxes",
        "Demo",
        "Privat",
    ]
    assert [folder_list.item(index).text() for index in range(folder_list.count())] == [
        "Inbox",
        "Trash",
        "Spam",
    ]
    assert account_list.currentItem().text() == "Privat"
    assert folder_list.currentItem().text() == "Inbox"

    window.close()


def test_main_window_can_add_local_account_with_server_metadata(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")

    window = MainWindow(store)

    assert window._add_account(
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

    window.close()


def test_main_window_saves_password_to_keyring_when_account_is_added(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    saved_passwords: list[tuple[str, str]] = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    window = MainWindow(store)

    def fake_save_password(email_address: str, password: str) -> None:
        saved_passwords.append((email_address, password))

    monkeypatch.setattr(main_window, "save_password", fake_save_password)

    assert window._add_account(
        "Privat",
        "privat@example.com",
        password="hemmelig",
    )

    assert saved_passwords == [("privat@example.com", "hemmelig")]
    assert store.list_accounts()[0].email_address == "privat@example.com"

    window.close()


def test_main_window_does_not_save_password_for_oauth2_account(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    saved_passwords: list[tuple[str, str]] = []
    saved_tokens: list[tuple[str, OAuthTokens]] = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    oauth_login_service = FakeOAuthLoginService()
    window = MainWindow(store, oauth_login_service=oauth_login_service)

    def fake_save_password(email_address: str, password: str) -> None:
        saved_passwords.append((email_address, password))

    monkeypatch.setattr(main_window, "save_password", fake_save_password)
    monkeypatch.setattr(
        main_window,
        "save_oauth_tokens",
        lambda email_address, tokens: saved_tokens.append((email_address, tokens)),
    )
    monkeypatch.setattr(
        main_window,
        "get_oauth_client_id",
        lambda _provider: "client-id",
    )
    monkeypatch.setattr(
        main_window,
        "get_oauth_client_secret",
        lambda _provider: "client-secret",
    )

    assert window._add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="outlook",
        password="skal-ikke-lagres",
    )
    _process_events_until(lambda: not window._task_runner.busy)

    account = store.list_accounts()[0]
    assert account.auth_method == "oauth2"
    assert account.oauth_provider == "outlook"
    assert account.imap_host == "outlook.office365.com"
    assert account.smtp_host == "smtp.office365.com"
    assert saved_passwords == []
    assert saved_tokens == [
        (
            "privat@example.com",
            OAuthTokens(access_token="access", refresh_token="refresh"),
        )
    ]
    assert oauth_login_service.authorized == [
        ("outlook", "client-id", "client-secret")
    ]

    window.close()


def test_main_window_can_delete_local_account(tmp_path, monkeypatch) -> None:
    _get_qapplication()
    deleted_passwords: list[str] = []
    deleted_oauth_tokens: list[str] = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    demo = store.add_account_with_default_folders("Demo", "demo@example.com")
    store.add_account_with_default_folders("Privat", "privat@example.com")

    window = MainWindow(store)
    monkeypatch.setattr(main_window, "delete_password", deleted_passwords.append)
    monkeypatch.setattr(main_window, "delete_oauth_tokens", deleted_oauth_tokens.append)

    assert window._delete_account(demo.id)

    account_list = window.findChild(QListWidget, "account_list")
    folder_list = window.findChild(QListWidget, "folder_list")

    assert account_list is not None
    assert folder_list is not None
    assert [account_list.item(index).text() for index in range(account_list.count())] == [
        "All inboxes",
        "Privat",
    ]
    assert [account.email_address for account in store.list_accounts()] == [
        "privat@example.com",
    ]
    assert deleted_passwords == ["demo@example.com"]
    assert deleted_oauth_tokens == ["demo@example.com"]

    window.close()


def test_main_window_can_authorize_existing_oauth_account(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    saved_tokens: list[tuple[str, OAuthTokens]] = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account_with_default_folders(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
    )
    oauth_login_service = FakeOAuthLoginService()
    window = MainWindow(store, oauth_login_service=oauth_login_service)
    account_list = window.findChild(QListWidget, "account_list")

    assert account_list is not None
    account_list.setCurrentRow(1)

    monkeypatch.setattr(
        main_window,
        "get_oauth_client_id",
        lambda _provider: "client-id",
    )
    monkeypatch.setattr(
        main_window,
        "get_oauth_client_secret",
        lambda _provider: "client-secret",
    )
    monkeypatch.setattr(
        main_window,
        "save_oauth_tokens",
        lambda email_address, tokens: saved_tokens.append((email_address, tokens)),
    )

    assert window._authorize_selected_oauth_account()
    _process_events_until(lambda: not window._task_runner.busy)

    status_label = window.findChild(QLabel, "sync_status_label")
    assert oauth_login_service.authorized == [("gmail", "client-id", "client-secret")]
    assert saved_tokens == [
        (
            "privat@example.com",
            OAuthTokens(access_token="access", refresh_token="refresh"),
        )
    ]
    assert status_label is not None
    assert status_label.text() == "OAuth sign-in successful"
    assert account.email_address == "privat@example.com"

    window.close()


def test_main_window_shows_selected_message_from_store(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "Innboks")
    store.add_message(
        account.id,
        inbox.id,
        subject="Fra testen",
        sender="sender@example.com",
        recipients="privat@example.com",
        received_at="2026-01-03T12:00:00",
        body_preview="Dette kommer fra lokal SQLite.",
        body_text="Dette er hele e-posten.",
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    message_view = window.findChild(MessageViewer, "message_view")

    assert message_list is not None
    assert message_view is not None

    message_list.setCurrentRow(0)

    assert "Account: privat@example.com" in message_view.toPlainText()
    assert "Subject: Fra testen" in message_view.toPlainText()
    assert "Dette er hele e-posten." in message_view.toPlainText()

    window.close()


def test_main_window_renders_html_message_when_available(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "Innboks")
    store.add_message(
        account.id,
        inbox.id,
        subject="HTML-test",
        sender="sender@example.com",
        recipients="privat@example.com",
        received_at="2026-01-03T12:00:00",
        body_text="Fallback tekst",
        body_html="<h1>HTML tittel</h1><p>HTML body</p>",
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    message_view = window.findChild(MessageViewer, "message_view")

    assert message_list is not None
    assert message_view is not None

    message_list.setCurrentRow(0)

    assert "Account: privat@example.com" in message_view.toPlainText()
    assert "HTML tittel" in message_view.toPlainText()
    assert "HTML body" in message_view.toPlainText()

    window.close()


def test_message_viewer_renders_html_without_script_handlers() -> None:
    _get_qapplication()
    viewer = MessageViewer()
    message = main_window.Message(
        id=1,
        account_id=1,
        folder_id=1,
        subject="HTML",
        sender="sender@example.com",
        recipients="privat@example.com",
        body_html=(
            "<h1 onclick=\"alert('x')\">Tittel</h1>"
            "<script>alert('x')</script>"
            "<img src=\"https://example.com/image.png\">"
            "<a href=\"javascript:alert('x')\">Lenke</a>"
        ),
    )

    viewer.show_message(message, "privat@example.com")

    rendered_html = viewer.rendered_html()
    assert "Tittel" in viewer.toPlainText()
    assert "https://example.com/image.png" not in rendered_html
    assert "blocked-remote-image" in rendered_html
    assert "<script" not in rendered_html
    assert "onclick" not in rendered_html
    assert "javascript:" not in rendered_html


def test_message_viewer_disables_browser_context_menu() -> None:
    _get_qapplication()
    viewer = MessageViewer()

    assert viewer._viewer.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu


def test_message_viewer_can_allow_remote_images() -> None:
    _get_qapplication()
    viewer = MessageViewer()
    message = main_window.Message(
        id=1,
        account_id=1,
        folder_id=1,
        subject="HTML",
        sender="sender@example.com",
        recipients="privat@example.com",
        body_html='<img src="https://example.com/image.png">',
    )

    viewer.set_remote_content_allowed(True)
    viewer.show_message(message, "privat@example.com")

    assert "https://example.com/image.png" in viewer.rendered_html()


def test_message_viewer_opens_links_in_default_browser(monkeypatch) -> None:
    _get_qapplication()
    opened_urls: list[str] = []

    monkeypatch.setattr(
        message_viewer_module.QDesktopServices,
        "openUrl",
        lambda url: opened_urls.append(url.toString()) or True,
    )
    viewer = MessageViewer()

    assert isinstance(viewer._viewer, QTextBrowser)
    assert not viewer._viewer.openExternalLinks()

    viewer._viewer.anchorClicked.emit(QUrl("https://example.com/path"))

    assert opened_urls == ["https://example.com/path"]


def test_message_viewer_does_not_open_unsafe_external_urls(monkeypatch) -> None:
    _get_qapplication()
    opened_urls: list[str] = []

    monkeypatch.setattr(
        message_viewer_module.QDesktopServices,
        "openUrl",
        lambda url: opened_urls.append(url.toString()) or True,
    )

    assert not message_viewer_module._open_external_url(QUrl("file:///tmp/test.txt"))
    assert opened_urls == []


def test_message_viewer_normalizes_protocol_relative_remote_images() -> None:
    _get_qapplication()
    viewer = MessageViewer()
    message = main_window.Message(
        id=1,
        account_id=1,
        folder_id=1,
        subject="TikTok",
        sender="sender@example.com",
        recipients="privat@example.com",
        body_html=(
            '<img src="//sf16-va.tiktokcdn.com/thumb.jpg" '
            'srcset="//sf16-va.tiktokcdn.com/small.jpg 1x, '
            '//sf16-va.tiktokcdn.com/large.jpg 2x">'
        ),
    )

    viewer.set_remote_content_allowed(True)
    viewer.show_message(message, "privat@example.com")

    rendered_html = viewer.rendered_html()
    assert 'src="https://sf16-va.tiktokcdn.com/thumb.jpg"' in rendered_html
    assert "https://sf16-va.tiktokcdn.com/small.jpg 1x" in rendered_html
    assert "https://sf16-va.tiktokcdn.com/large.jpg 2x" in rendered_html


def test_message_viewer_allows_remote_video_when_enabled() -> None:
    _get_qapplication()
    viewer = MessageViewer()
    message = main_window.Message(
        id=1,
        account_id=1,
        folder_id=1,
        subject="TikTok",
        sender="sender@example.com",
        recipients="privat@example.com",
        body_html=(
            '<video controls poster="//sf16-va.tiktokcdn.com/poster.jpg">'
            '<source src="//sf16-va.tiktokcdn.com/video.mp4" type="video/mp4">'
            "</video>"
        ),
    )

    viewer.set_remote_content_allowed(True)
    viewer.show_message(message, "privat@example.com")

    rendered_html = viewer.rendered_html()
    assert "<video" in rendered_html
    assert 'poster="https://sf16-va.tiktokcdn.com/poster.jpg"' in rendered_html
    assert 'src="https://sf16-va.tiktokcdn.com/video.mp4"' in rendered_html
    assert "controls" in rendered_html


def test_message_viewer_blocks_remote_video_by_default() -> None:
    _get_qapplication()
    viewer = MessageViewer()
    message = main_window.Message(
        id=1,
        account_id=1,
        folder_id=1,
        subject="TikTok",
        sender="sender@example.com",
        recipients="privat@example.com",
        body_html=(
            '<video controls poster="https://example.com/poster.jpg">'
            '<source src="https://example.com/video.mp4" type="video/mp4">'
            "</video>"
        ),
    )

    viewer.show_message(message, "privat@example.com")

    rendered_html = viewer.rendered_html()
    assert "https://example.com/poster.jpg" not in rendered_html
    assert "https://example.com/video.mp4" not in rendered_html


def test_message_viewer_allows_remote_background_images_when_enabled() -> None:
    _get_qapplication()
    viewer = MessageViewer()
    message = main_window.Message(
        id=1,
        account_id=1,
        folder_id=1,
        subject="TikTok",
        sender="sender@example.com",
        recipients="privat@example.com",
        body_html=(
            '<div style="background-image: url(//sf16-va.tiktokcdn.com/bg.jpg); '
            'color: #111;">Preview</div>'
        ),
    )

    viewer.set_remote_content_allowed(True)
    viewer.show_message(message, "privat@example.com")

    rendered_html = viewer.rendered_html()
    assert "url(&quot;https://sf16-va.tiktokcdn.com/bg.jpg&quot;)" in rendered_html
    assert "Preview" in viewer.toPlainText()


def test_main_window_can_save_and_open_cached_attachments(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(account.id, inbox.id, subject="Attachments")
    store.replace_message_attachments(
        message.id,
        [
            Attachment(
                id=0,
                message_id=message.id,
                filename="../rapport.txt",
                content_type="text/plain",
                size=len(b"rapport"),
                has_content=True,
                content=b"rapport",
            )
        ],
    )
    save_path = tmp_path / "lagret.txt"
    opened_paths: list[str] = []

    monkeypatch.setattr(
        attachment_controller.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(save_path), ""),
    )
    monkeypatch.setattr(
        attachment_controller.QDesktopServices,
        "openUrl",
        lambda url: opened_paths.append(url.toLocalFile()) or True,
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    attachment_list = window.findChild(QListWidget, "attachment_list")
    attachment_preview = window.findChild(QTextBrowser, "attachment_preview")
    save_button = window.findChild(QPushButton, "save_attachment_button")
    open_button = window.findChild(QPushButton, "open_attachment_button")

    assert message_list is not None
    assert attachment_list is not None
    assert attachment_preview is not None
    assert save_button is not None
    assert open_button is not None
    assert not save_button.isEnabled()
    assert not open_button.isEnabled()

    message_list.setCurrentRow(0)
    attachment_list.setCurrentRow(0)

    assert "rapport.txt" in attachment_list.currentItem().text()
    assert "rapport" in attachment_preview.toPlainText()
    assert save_button.isEnabled()
    assert open_button.isEnabled()
    assert window._save_selected_attachment()
    _process_events_until(lambda: not window._task_runner.busy)
    assert window._open_selected_attachment()
    _process_events_until(lambda: not window._task_runner.busy)

    assert save_path.read_bytes() == b"rapport"
    assert opened_paths
    assert ".." not in opened_paths[0]
    assert "rapport.txt" in opened_paths[0]

    window.close()


def test_main_window_can_save_all_cached_attachments(tmp_path, monkeypatch) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(account.id, inbox.id, subject="Attachments")
    store.replace_message_attachments(
        message.id,
        [
            Attachment(
                id=0,
                message_id=message.id,
                filename="rapport.txt",
                content_type="text/plain",
                size=len(b"rapport"),
                has_content=True,
                content=b"rapport",
            ),
            Attachment(
                id=0,
                message_id=message.id,
                filename="rapport.txt",
                content_type="text/plain",
                size=len(b"kopi"),
                has_content=True,
                content=b"kopi",
            ),
        ],
    )
    save_dir = tmp_path / "nedlastinger"
    save_dir.mkdir()

    monkeypatch.setattr(
        attachment_controller.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(save_dir),
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    save_all_button = window.findChild(QToolButton, "save_all_attachments_button")
    status_label = window.findChild(QLabel, "sync_status_label")

    assert message_list is not None
    assert save_all_button is not None
    assert status_label is not None

    message_list.setCurrentRow(0)

    assert save_all_button.isEnabled()
    assert window._save_all_available_attachments()
    _process_events_until(lambda: not window._task_runner.busy)
    assert (save_dir / "rapport.txt").read_bytes() == b"rapport"
    assert (save_dir / "rapport (2).txt").read_bytes() == b"kopi"
    assert status_label.text() == "Saved 2 attachments."

    window.close()


def test_main_window_starts_with_unified_inbox_messages(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    private_account = store.add_account("Privat", "privat@example.com")
    work_account = store.add_account("Arbeid", "arbeid@example.com")
    private_inbox = store.add_folder(private_account.id, "INBOX")
    work_inbox = store.add_folder(work_account.id, "Innboks")

    store.add_message(
        private_account.id,
        private_inbox.id,
        subject="Privat",
        sender="privat@example.com",
        received_at="2026-01-03T12:00:00",
    )
    store.add_message(
        work_account.id,
        work_inbox.id,
        subject="Arbeid",
        sender="arbeid@example.com",
        received_at="2026-01-04T12:00:00",
    )

    window = MainWindow(store)
    account_list = window.findChild(QListWidget, "account_list")
    message_list = window.findChild(QListWidget, "message_list")
    message_view = window.findChild(MessageViewer, "message_view")

    assert account_list is not None
    assert message_list is not None
    assert message_view is not None
    assert account_list.currentItem().text() == "All inboxes"
    assert [
        _message_list_item_text(message_list, index)
        for index in range(message_list.count())
    ] == [
        "arbeid@example.com\nArbeid",
        "privat@example.com\nPrivat",
    ]

    message_list.setCurrentRow(0)

    assert "Account: arbeid@example.com" in message_view.toPlainText()
    assert "Subject: Arbeid" in message_view.toPlainText()

    window.close()


def test_main_window_filters_and_sorts_unified_inbox_messages(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    store.add_message(
        account.id,
        inbox.id,
        subject="Beta",
        sender="beta@example.com",
        received_at="2026-01-03T12:00:00",
        is_read=True,
    )
    store.add_message(
        account.id,
        inbox.id,
        subject="Alfa",
        sender="alfa@example.com",
        received_at="2026-01-04T12:00:00",
        is_read=False,
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    search_edit = window.findChild(QLineEdit, "message_search_edit")
    unread_checkbox = window.findChild(QCheckBox, "unread_filter_checkbox")
    sort_combo = window.findChild(QComboBox, "message_sort_combo")

    assert message_list is not None
    assert search_edit is not None
    assert unread_checkbox is not None
    assert sort_combo is not None

    assert [
        _message_list_item_text(message_list, index)
        for index in range(message_list.count())
    ] == [
        "privat@example.com\nalfa@example.com\nAlfa",
        "privat@example.com\nbeta@example.com\nBeta",
    ]

    search_edit.setText("beta")

    assert message_list.count() == 1
    assert _message_list_item_text(message_list, 0) == (
        "privat@example.com\nbeta@example.com\nBeta"
    )

    search_edit.clear()
    unread_checkbox.setChecked(True)

    assert message_list.count() == 1
    assert _message_list_item_text(message_list, 0) == (
        "privat@example.com\nalfa@example.com\nAlfa"
    )

    unread_checkbox.setChecked(False)
    sort_combo.setCurrentIndex(sort_combo.findData("subject"))

    assert [
        _message_list_item_text(message_list, index)
        for index in range(message_list.count())
    ] == [
        "privat@example.com\nalfa@example.com\nAlfa",
        "privat@example.com\nbeta@example.com\nBeta",
    ]

    window.close()


def test_main_window_can_mark_selected_message_read_and_unread(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(
        account.id,
        inbox.id,
        subject="Unread",
        sender="sender@example.com",
        is_read=False,
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    mark_read_button = window.findChild(QToolButton, "mark_read_button")
    mark_unread_button = window.findChild(QToolButton, "mark_unread_button")
    status_label = window.findChild(QLabel, "sync_status_label")

    assert message_list is not None
    assert mark_read_button is not None
    assert mark_unread_button is not None
    assert status_label is not None
    assert not mark_read_button.isEnabled()
    assert not mark_unread_button.isEnabled()

    message_list.setCurrentRow(0)

    assert message_list.currentItem().font().bold()
    assert mark_read_button.isEnabled()
    assert mark_unread_button.isEnabled()

    assert window._mark_selected_message_read()
    _process_events_until(lambda: not window._task_runner.busy)

    assert store.get_message(message.id).is_read is True
    assert not message_list.currentItem().font().bold()
    assert status_label.text() == "Message marked as read."

    assert window._mark_selected_message_unread()
    _process_events_until(lambda: not window._task_runner.busy)

    assert store.get_message(message.id).is_read is False
    assert message_list.currentItem().font().bold()
    assert status_label.text() == "Message marked as unread."

    window.close()


def test_main_window_can_archive_trash_and_move_selected_message(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    spam = store.add_folder(account.id, "Søppelpost")
    message = store.add_message(account.id, inbox.id, subject="Flytt meg")
    sync_service = FakeMailSyncService()
    window = MainWindow(store, mail_sync_service=sync_service)
    message_list = window.findChild(QListWidget, "message_list")

    monkeypatch.setattr(
        main_window.QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: ("Spam", True),
    )

    assert message_list is not None
    message_list.setCurrentRow(0)

    assert window._archive_selected_message()
    _process_events_until(lambda: not window._task_runner.busy)
    message_list.setCurrentRow(0)
    assert window._move_selected_message_to_trash()
    _process_events_until(lambda: not window._task_runner.busy)
    message_list.setCurrentRow(0)
    assert window._move_selected_message()
    _process_events_until(lambda: not window._task_runner.busy)

    assert sync_service.archived_messages == [message.id]
    assert sync_service.trashed_messages == [message.id]
    assert sync_service.moved_messages == [(message.id, spam.id)]

    window.close()


def test_main_window_reply_uses_original_message_account(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    FakeComposeDialog.shown_drafts = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    private = store.add_account("Privat", "privat@example.com")
    work = store.add_account("Arbeid", "arbeid@example.com")
    private_inbox = store.add_folder(private.id, "INBOX")
    work_inbox = store.add_folder(work.id, "INBOX")
    store.add_message(
        private.id,
        private_inbox.id,
        subject="Privat",
        received_at="2026-01-03T12:00:00",
    )
    message = store.add_message(
        work.id,
        work_inbox.id,
        subject="Arbeid",
        sender="sender@example.com",
        received_at="2026-01-04T12:00:00",
    )
    send_service = FakeMailSendService()
    window = MainWindow(store, mail_send_service=send_service)
    message_list = window.findChild(QListWidget, "message_list")

    monkeypatch.setattr(main_window, "ComposeDialog", FakeComposeDialog)

    assert message_list is not None
    message_list.setCurrentRow(0)

    assert window._reply_to_selected_message()

    assert send_service.reply_drafts == [message.id]
    assert FakeComposeDialog.shown_drafts[0].account_id == work.id
    _process_events_until(lambda: window._send_thread is None)
    assert send_service.sent_drafts[0].account_id == work.id

    window.close()


def test_main_window_can_test_selected_account_connections(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account_with_default_folders("Privat", "privat@example.com")
    sync_service = FakeMailSyncService()

    window = MainWindow(store, mail_sync_service=sync_service)
    account_list = window.findChild(QListWidget, "account_list")

    assert account_list is not None
    account_list.setCurrentRow(1)

    assert window._test_selected_imap_connection()
    _process_events_until(lambda: not window._task_runner.busy)
    assert window._test_selected_smtp_connection()
    _process_events_until(lambda: not window._task_runner.busy)

    status_label = window.findChild(QLabel, "sync_status_label")
    assert sync_service.imap_tested_account_id == account.id
    assert sync_service.smtp_tested_account_id == account.id
    assert status_label is not None
    assert status_label.text() == "SMTP connection successful"

    window.close()


def test_main_window_can_sync_selected_account(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account_with_default_folders("Privat", "privat@example.com")
    sync_service = FakeMailSyncService()

    window = MainWindow(store, mail_sync_service=sync_service)
    account_list = window.findChild(QListWidget, "account_list")

    assert account_list is not None
    account_list.setCurrentRow(1)

    assert window._sync_selected_account()
    status_label = window.findChild(QLabel, "sync_status_label")
    assert status_label is not None

    _process_events_until(lambda: window._sync_thread is None)

    assert sync_service.synced_account_id == account.id
    assert status_label.text() == "Synced 2 folders and 3 messages."

    window.close()


def test_main_window_reports_partial_sent_copy_failure(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    store.add_account_with_default_folders("Privat", "privat@example.com")
    window = MainWindow(store)
    status_label = window.findChild(QLabel, "sync_status_label")

    assert status_label is not None

    window._handle_send_finished(
        SendResult(
            sent=True,
            local_copy_saved=True,
            server_copy_attempted=True,
            server_copy_saved=False,
        )
    )

    assert status_label.text() == "Email sent, but saving a server copy in Sent failed."

    window.close()
