"""Main application window."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QLabel,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mailklient.domain import Message
from mailklient.mail import get_mail_provider_defaults
from mailklient.security import (
    OAuthClientConfigError,
    delete_oauth_tokens,
    delete_password,
    get_oauth_client_id,
    get_oauth_client_secret,
    save_oauth_tokens,
    save_password,
)
from mailklient.services import MailStore, MailSyncService, OAuthLoginService
from mailklient.ui.account_dialog import AccountDialog


class MainWindow(QMainWindow):
    """Main window backed by the local mail store."""

    def __init__(
        self,
        mail_store: MailStore,
        mail_sync_service: MailSyncService | None = None,
        oauth_login_service: OAuthLoginService | None = None,
    ) -> None:
        super().__init__()
        self._mail_store = mail_store
        self._mail_sync_service = mail_sync_service or MailSyncService(mail_store)
        self._oauth_login_service = oauth_login_service or OAuthLoginService()
        self._messages_by_id: dict[int, Message] = {}

        self.setWindowTitle("Mailklient")
        self.resize(1200, 750)

        self._build_menu()
        self.setCentralWidget(self._build_central_widget())
        self.folder_list.currentItemChanged.connect(self._load_selected_folder)
        self.account_list.currentItemChanged.connect(self._load_selected_account)
        self.message_list.currentItemChanged.connect(self._show_selected_message)
        self._load_accounts()

    def _build_menu(self) -> None:
        account_menu = self.menuBar().addMenu("Konto")

        add_account_action = QAction("Legg til konto", self)
        add_account_action.setObjectName("add_account_action")
        add_account_action.triggered.connect(self._open_add_account_dialog)

        delete_account_action = QAction("Slett konto", self)
        delete_account_action.setObjectName("delete_account_action")
        delete_account_action.triggered.connect(self._delete_selected_account)

        test_imap_action = QAction("Test IMAP", self)
        test_imap_action.setObjectName("test_imap_action")
        test_imap_action.triggered.connect(self._test_selected_imap_connection)

        test_smtp_action = QAction("Test SMTP", self)
        test_smtp_action.setObjectName("test_smtp_action")
        test_smtp_action.triggered.connect(self._test_selected_smtp_connection)

        oauth_login_action = QAction("OAuth login", self)
        oauth_login_action.setObjectName("oauth_login_action")
        oauth_login_action.triggered.connect(self._authorize_selected_oauth_account)

        sync_account_action = QAction("Synk konto", self)
        sync_account_action.setObjectName("sync_account_action")
        sync_account_action.triggered.connect(self._sync_selected_account)

        account_menu.addAction(add_account_action)
        account_menu.addAction(delete_account_action)
        account_menu.addSeparator()
        account_menu.addAction(test_imap_action)
        account_menu.addAction(test_smtp_action)
        account_menu.addAction(oauth_login_action)
        account_menu.addAction(sync_account_action)

    def _build_central_widget(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("main_splitter")

        splitter.addWidget(self._build_folder_panel())
        splitter.addWidget(self._build_message_list())
        splitter.addWidget(self._build_message_view())
        splitter.setSizes([240, 420, 540])

        return splitter

    def _build_folder_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("folder_panel")

        layout = QVBoxLayout(panel)

        title = QLabel("Mapper")
        title.setObjectName("folder_panel_title")

        account_title = QLabel("Kontoer")
        account_title.setObjectName("account_panel_title")

        self.account_list = QListWidget()
        self.account_list.setObjectName("account_list")

        self.delete_account_button = QPushButton("Slett konto")
        self.delete_account_button.setObjectName("delete_account_button")
        self.delete_account_button.clicked.connect(self._delete_selected_account)

        self.test_imap_button = QPushButton("Test IMAP")
        self.test_imap_button.setObjectName("test_imap_button")
        self.test_imap_button.clicked.connect(self._test_selected_imap_connection)

        self.test_smtp_button = QPushButton("Test SMTP")
        self.test_smtp_button.setObjectName("test_smtp_button")
        self.test_smtp_button.clicked.connect(self._test_selected_smtp_connection)

        self.oauth_login_button = QPushButton("OAuth")
        self.oauth_login_button.setObjectName("oauth_login_button")
        self.oauth_login_button.clicked.connect(self._authorize_selected_oauth_account)

        self.sync_account_button = QPushButton("Synk")
        self.sync_account_button.setObjectName("sync_account_button")
        self.sync_account_button.clicked.connect(self._sync_selected_account)

        sync_button_layout = QHBoxLayout()
        sync_button_layout.addWidget(self.test_imap_button)
        sync_button_layout.addWidget(self.test_smtp_button)
        sync_button_layout.addWidget(self.oauth_login_button)
        sync_button_layout.addWidget(self.sync_account_button)

        self.sync_status_label = QLabel("")
        self.sync_status_label.setObjectName("sync_status_label")
        self.sync_status_label.setWordWrap(True)

        self.folder_list = QListWidget()
        self.folder_list.setObjectName("folder_list")

        layout.addWidget(account_title)
        layout.addWidget(self.account_list)
        layout.addWidget(self.delete_account_button)
        layout.addLayout(sync_button_layout)
        layout.addWidget(self.sync_status_label)
        layout.addWidget(title)
        layout.addWidget(self.folder_list)

        return panel

    def _build_message_list(self) -> QListWidget:
        self.message_list = QListWidget()
        self.message_list.setObjectName("message_list")

        return self.message_list

    def _build_message_view(self) -> QTextEdit:
        self.message_view = QTextEdit()
        self.message_view.setObjectName("message_view")
        self.message_view.setReadOnly(True)
        self.message_view.setPlainText("Velg en melding")

        return self.message_view

    def _load_accounts(self, select_account_id: int | None = None) -> None:
        self.account_list.clear()
        self.folder_list.clear()
        self.message_list.clear()
        self.message_view.setPlainText("Velg en melding")

        accounts = self._mail_store.list_accounts()
        if not accounts:
            self._set_account_actions_enabled(False)
            return

        selected_row = 0
        for account in accounts:
            item = QListWidgetItem(account.display_name)
            item.setData(Qt.ItemDataRole.UserRole, account.id)
            self.account_list.addItem(item)

            if select_account_id == account.id:
                selected_row = self.account_list.count() - 1

        self.account_list.setCurrentRow(selected_row)
        self._set_account_actions_enabled(True)

    def _load_selected_account(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self.folder_list.clear()
        self.message_list.clear()
        self._messages_by_id.clear()
        self.message_view.setPlainText("Velg en melding")

        if current is None:
            self._set_account_actions_enabled(False)
            return

        self._set_account_actions_enabled(True)
        account_id = current.data(Qt.ItemDataRole.UserRole)
        folders = sorted(
            self._mail_store.list_folders(account_id),
            key=lambda folder: _folder_sort_key(folder.name),
        )

        for folder in folders:
            item = QListWidgetItem(folder.name)
            item.setData(Qt.ItemDataRole.UserRole, (folder.account_id, folder.id))
            self.folder_list.addItem(item)

        if self.folder_list.count() > 0:
            self.folder_list.setCurrentRow(0)

    def _load_selected_folder(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self.message_list.clear()
        self._messages_by_id.clear()
        self.message_view.setPlainText("Velg en melding")

        if current is None:
            return

        account_id, folder_id = current.data(Qt.ItemDataRole.UserRole)
        messages = self._mail_store.list_messages(account_id, folder_id)

        for message in messages:
            item = QListWidgetItem(message.subject or "(uten emne)")
            item.setData(Qt.ItemDataRole.UserRole, message.id)
            self.message_list.addItem(item)
            self._messages_by_id[message.id] = message

    def _show_selected_message(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self.message_view.setPlainText("Velg en melding")
            return

        message_id = current.data(Qt.ItemDataRole.UserRole)
        message = self._messages_by_id[message_id]

        self.message_view.setPlainText(
            "\n".join(
                [
                    f"Emne: {message.subject}",
                    f"Fra: {message.sender}",
                    f"Til: {message.recipients}",
                    f"Dato: {message.received_at or message.sent_at or ''}",
                    "",
                    message.body_preview,
                ]
            )
        )

    def _open_add_account_dialog(self) -> None:
        dialog = AccountDialog(self)

        if dialog.exec() != AccountDialog.DialogCode.Accepted:
            return

        data = dialog.account_data()
        self._add_account(
            data.display_name,
            data.email_address,
            auth_method=data.auth_method,
            oauth_provider=data.oauth_provider,
            username=data.username,
            imap_host=data.imap_host,
            imap_port=data.imap_port,
            imap_security=data.imap_security,
            smtp_host=data.smtp_host,
            smtp_port=data.smtp_port,
            smtp_security=data.smtp_security,
            password=data.password,
        )

    def _add_account(
        self,
        display_name: str,
        email_address: str,
        *,
        auth_method: str = "password",
        username: str | None = None,
        imap_host: str | None = None,
        imap_port: int | None = None,
        imap_security: str = "ssl",
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_security: str = "starttls",
        password: str | None = None,
        oauth_provider: str | None = None,
    ) -> bool:
        client_id = None
        client_secret = None
        if auth_method == "oauth2":
            if oauth_provider is None:
                QMessageBox.warning(
                    self,
                    "OAuth mangler provider",
                    "Velg Gmail eller Outlook for OAuth2-kontoen.",
                )
                return False
            try:
                client_id = get_oauth_client_id(oauth_provider)
                client_secret = get_oauth_client_secret(oauth_provider)
            except (KeyError, OAuthClientConfigError) as error:
                QMessageBox.warning(self, "OAuth mangler konfigurasjon", str(error))
                return False

            (
                imap_host,
                imap_port,
                imap_security,
                smtp_host,
                smtp_port,
                smtp_security,
            ) = _apply_provider_defaults(
                oauth_provider,
                imap_host,
                imap_port,
                imap_security,
                smtp_host,
                smtp_port,
                smtp_security,
            )

        try:
            account = self._mail_store.add_account_with_default_folders(
                display_name,
                email_address,
                auth_method=auth_method,
                username=username,
                imap_host=imap_host,
                imap_port=imap_port,
                imap_security=imap_security,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_security=smtp_security,
                oauth_provider=oauth_provider,
            )
            if auth_method == "password" and password:
                save_password(account.email_address, password)
            if auth_method == "oauth2" and client_id is not None:
                self._authorize_oauth_provider(
                    account.email_address,
                    oauth_provider,
                    client_id,
                    client_secret,
                )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Kunne ikke legge til konto",
                "En konto med denne e-postadressen finnes allerede.",
            )
            return False
        except Exception as error:
            if "account" in locals():
                self._mail_store.delete_account(account.id)
            title = (
                "OAuth innlogging feilet"
                if auth_method == "oauth2"
                else "Kunne ikke lagre passord"
            )
            QMessageBox.warning(self, title, str(error))
            self._load_accounts()
            return False

        self._load_accounts(select_account_id=account.id)
        return True

    def _delete_selected_account(self) -> bool:
        current = self.account_list.currentItem()
        if current is None:
            return False

        account_id = current.data(Qt.ItemDataRole.UserRole)
        account_name = current.text()
        answer = QMessageBox.question(
            self,
            "Slett konto",
            f"Slette lokal konto '{account_name}'?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        return self._delete_account(account_id)

    def _delete_account(self, account_id: int) -> bool:
        account = self._mail_store.get_account(account_id)
        deleted = self._mail_store.delete_account(account_id)
        if deleted and account is not None:
            delete_password(account.email_address)
            delete_oauth_tokens(account.email_address)
        self._load_accounts()
        return deleted

    def _authorize_selected_oauth_account(self) -> bool:
        account_id = self._selected_account_id()
        if account_id is None:
            return False

        account = self._mail_store.get_account(account_id)
        if account is None:
            return False
        if account.auth_method != "oauth2" or account.oauth_provider is None:
            QMessageBox.warning(
                self,
                "OAuth ikke tilgjengelig",
                "Valgt konto bruker ikke OAuth2.",
            )
            return False

        try:
            client_id = get_oauth_client_id(account.oauth_provider)
            client_secret = get_oauth_client_secret(account.oauth_provider)
            self._authorize_oauth_provider(
                account.email_address,
                account.oauth_provider,
                client_id,
                client_secret,
            )
        except Exception as error:
            self._show_sync_error("OAuth innlogging feilet", error)
            return False

        self.sync_status_label.setText("OAuth innlogging OK")
        return True

    def _authorize_oauth_provider(
        self,
        email_address: str,
        provider: str,
        client_id: str,
        client_secret: str | None,
    ) -> None:
        tokens = self._oauth_login_service.authorize(
            provider,
            client_id,
            client_secret=client_secret,
        )
        save_oauth_tokens(email_address, tokens)

    def _test_selected_imap_connection(self) -> bool:
        account_id = self._selected_account_id()
        if account_id is None:
            return False

        try:
            connected = self._mail_sync_service.test_imap_connection(account_id)
        except Exception as error:
            self._show_sync_error("IMAP-test feilet", error)
            return False

        self.sync_status_label.setText(
            "IMAP-tilkobling OK" if connected else "IMAP-tilkobling feilet"
        )
        return connected

    def _test_selected_smtp_connection(self) -> bool:
        account_id = self._selected_account_id()
        if account_id is None:
            return False

        try:
            connected = self._mail_sync_service.test_smtp_connection(account_id)
        except Exception as error:
            self._show_sync_error("SMTP-test feilet", error)
            return False

        self.sync_status_label.setText(
            "SMTP-tilkobling OK" if connected else "SMTP-tilkobling feilet"
        )
        return connected

    def _sync_selected_account(self) -> bool:
        account_id = self._selected_account_id()
        if account_id is None:
            return False

        try:
            result = self._mail_sync_service.fetch_imap_headers(account_id)
        except Exception as error:
            self._show_sync_error("Synk feilet", error)
            return False

        self._load_accounts(select_account_id=account_id)
        self.sync_status_label.setText(
            f"Synkroniserte {result.folders_seen} mapper og "
            f"{result.messages_seen} meldinger."
        )
        return True

    def _selected_account_id(self) -> int | None:
        current = self.account_list.currentItem()
        if current is None:
            return None
        return current.data(Qt.ItemDataRole.UserRole)

    def _set_account_actions_enabled(self, enabled: bool) -> None:
        self.delete_account_button.setEnabled(enabled)
        self.test_imap_button.setEnabled(enabled)
        self.test_smtp_button.setEnabled(enabled)
        self.oauth_login_button.setEnabled(enabled)
        self.sync_account_button.setEnabled(enabled)

    def _show_sync_error(self, title: str, error: Exception) -> None:
        self.sync_status_label.setText(title)
        QMessageBox.warning(self, title, str(error))


def _folder_sort_key(name: str) -> tuple[int, str]:
    preferred_order = {
        "innboks": 0,
        "inbox": 0,
        "sendt": 1,
        "sent": 1,
        "arkiv": 2,
        "archive": 2,
    }
    normalized_name = name.casefold()
    return (preferred_order.get(normalized_name, 100), normalized_name)


def _apply_provider_defaults(
    provider: str,
    imap_host: str | None,
    imap_port: int | None,
    imap_security: str,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_security: str,
) -> tuple[str, int, str, str, int, str]:
    defaults = get_mail_provider_defaults(provider)
    return (
        imap_host or defaults.imap_host,
        imap_port or defaults.imap_port,
        imap_security or defaults.imap_security,
        smtp_host or defaults.smtp_host,
        smtp_port or defaults.smtp_port,
        smtp_security or defaults.smtp_security,
    )
