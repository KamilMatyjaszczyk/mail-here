from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTextEdit,
    QWidget,
)

from mailklient.domain import Account
from mailklient.main import default_database_path, main
from mailklient.security import OAuthTokens
from mailklient.services import HeaderSyncResult, MailStore, seed_demo_data
from mailklient.ui import main_window
from mailklient.ui.main_window import MainWindow


def _get_qapplication() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeMailSyncService:
    def __init__(self) -> None:
        self.imap_tested_account_id: int | None = None
        self.smtp_tested_account_id: int | None = None
        self.synced_account_id: int | None = None

    def test_imap_connection(self, account_id: int) -> bool:
        self.imap_tested_account_id = account_id
        return True

    def test_smtp_connection(self, account_id: int) -> bool:
        self.smtp_tested_account_id = account_id
        return True

    def fetch_imap_headers(self, account_id: int) -> HeaderSyncResult:
        self.synced_account_id = account_id
        return HeaderSyncResult(folders_seen=2, messages_seen=3)


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


def test_entry_point_is_callable() -> None:
    assert callable(main)
    assert callable(default_database_path)


def test_main_window_defaults_and_store_backed_layout(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    seed_demo_data(store)

    window = MainWindow(store)

    assert window.windowTitle() == "Mailklient"
    assert window.size().width() == 1200
    assert window.size().height() == 750

    central_widget = window.centralWidget()
    assert isinstance(central_widget, QSplitter)
    assert central_widget.count() == 3

    folder_panel = window.findChild(QWidget, "folder_panel")
    account_list = window.findChild(QListWidget, "account_list")
    folder_list = window.findChild(QListWidget, "folder_list")
    message_list = window.findChild(QListWidget, "message_list")
    message_view = window.findChild(QTextEdit, "message_view")
    add_account_action = window.findChild(QAction, "add_account_action")
    delete_account_action = window.findChild(QAction, "delete_account_action")
    test_imap_action = window.findChild(QAction, "test_imap_action")
    test_smtp_action = window.findChild(QAction, "test_smtp_action")
    oauth_login_action = window.findChild(QAction, "oauth_login_action")
    sync_account_action = window.findChild(QAction, "sync_account_action")
    test_imap_button = window.findChild(QPushButton, "test_imap_button")
    test_smtp_button = window.findChild(QPushButton, "test_smtp_button")
    oauth_login_button = window.findChild(QPushButton, "oauth_login_button")
    sync_account_button = window.findChild(QPushButton, "sync_account_button")
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
    assert test_imap_button is not None
    assert test_smtp_button is not None
    assert oauth_login_button is not None
    assert sync_account_button is not None
    assert sync_status_label is not None
    assert test_imap_button.isEnabled()
    assert test_smtp_button.isEnabled()
    assert oauth_login_button.isEnabled()
    assert sync_account_button.isEnabled()
    assert [account_list.item(index).text() for index in range(account_list.count())] == [
        "Demo",
    ]
    assert [folder_list.item(index).text() for index in range(folder_list.count())] == [
        "Innboks",
        "Sendt",
        "Arkiv",
    ]
    assert message_list.count() == 2
    assert message_list.item(0).text() == "Velkommen til Mailklient"
    assert message_view.isReadOnly()
    assert message_view.toPlainText() == "Velg en melding"

    window.close()


def test_main_window_disables_account_actions_without_accounts(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")

    window = MainWindow(store)

    assert not window.findChild(QPushButton, "delete_account_button").isEnabled()
    assert not window.findChild(QPushButton, "test_imap_button").isEnabled()
    assert not window.findChild(QPushButton, "test_smtp_button").isEnabled()
    assert not window.findChild(QPushButton, "oauth_login_button").isEnabled()
    assert not window.findChild(QPushButton, "sync_account_button").isEnabled()

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
        "Demo",
        "Privat",
    ]
    assert [folder_list.item(index).text() for index in range(folder_list.count())] == [
        "Innboks",
        "Sendt",
        "Arkiv",
    ]
    assert account_list.currentItem().text() == "Privat"
    assert folder_list.currentItem().text() == "Innboks"

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

    status_label = window.findChild(QLabel, "sync_status_label")
    assert oauth_login_service.authorized == [("gmail", "client-id", "client-secret")]
    assert saved_tokens == [
        (
            "privat@example.com",
            OAuthTokens(access_token="access", refresh_token="refresh"),
        )
    ]
    assert status_label is not None
    assert status_label.text() == "OAuth innlogging OK"
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
    )

    window = MainWindow(store)
    message_list = window.findChild(QListWidget, "message_list")
    message_view = window.findChild(QTextEdit, "message_view")

    assert message_list is not None
    assert message_view is not None

    message_list.setCurrentRow(0)

    assert "Emne: Fra testen" in message_view.toPlainText()
    assert "Dette kommer fra lokal SQLite." in message_view.toPlainText()

    window.close()


def test_main_window_can_test_selected_account_connections(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account_with_default_folders("Privat", "privat@example.com")
    sync_service = FakeMailSyncService()

    window = MainWindow(store, mail_sync_service=sync_service)

    assert window._test_selected_imap_connection()
    assert window._test_selected_smtp_connection()

    status_label = window.findChild(QLabel, "sync_status_label")
    assert sync_service.imap_tested_account_id == account.id
    assert sync_service.smtp_tested_account_id == account.id
    assert status_label is not None
    assert status_label.text() == "SMTP-tilkobling OK"

    window.close()


def test_main_window_can_sync_selected_account(tmp_path) -> None:
    _get_qapplication()
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account_with_default_folders("Privat", "privat@example.com")
    sync_service = FakeMailSyncService()

    window = MainWindow(store, mail_sync_service=sync_service)

    assert window._sync_selected_account()

    status_label = window.findChild(QLabel, "sync_status_label")
    assert sync_service.synced_account_id == account.id
    assert status_label is not None
    assert status_label.text() == "Synkroniserte 2 mapper og 3 meldinger."

    window.close()
