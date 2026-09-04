"""Main application window."""

from __future__ import annotations

import sqlite3
import smtplib
import tempfile
import base64
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, QThread, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mailklient.domain import Attachment, Message
from mailklient.mail import get_mail_provider_defaults
from mailklient.security import (
    OAuthCallbackError,
    OAuthClientConfigError,
    delete_oauth_tokens,
    delete_password,
    get_oauth_client_id,
    get_oauth_client_secret,
    save_oauth_tokens,
    save_password,
)
from mailklient.services import (
    ComposeDraft,
    HeaderSyncResult,
    MailSendService,
    MailStore,
    MailSyncService,
    OAuthLoginService,
    SendResult,
)
from mailklient.ui.account_dialog import AccountDialog
from mailklient.ui.compose_dialog import ComposeDialog
from mailklient.ui.message_viewer import MessageViewer
from mailklient.workers import MailSendWorker, MailSyncWorker

UNIFIED_INBOX_ROLE = "unified_inbox"
MESSAGE_TEXT_ROLE = Qt.ItemDataRole.UserRole + 1
DEFAULT_COLUMN_ORDER = ("folders", "reader", "messages")
COLUMN_WIDTHS = {
    "folders": 230,
    "reader": 610,
    "messages": 360,
}
COLUMN_TITLES = {
    "folders": "Kontoer og mapper",
    "reader": "Mail",
    "messages": "Meldinger",
}


class ColumnDragHandle(QLabel):
    """Small drag handle used to reorder the main columns."""

    def __init__(self, title: str, column_name: str, window: "MainWindow") -> None:
        super().__init__(f":: {title}")
        self._column_name = column_name
        self._window = window
        self._press_position: QPoint | None = None
        self._dragging = False
        self.setObjectName(f"column_drag_handle_{column_name}")
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        self.setToolTip("Dra for å flytte kolonnen")
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def mousePressEvent(self, event: object) -> None:
        if _event_button(event) != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._press_position = _event_position(event)
        self._dragging = False
        self._window._begin_column_drag(self._column_name)
        self.setProperty("dragging", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        if hasattr(event, "accept"):
            event.accept()

    def mouseMoveEvent(self, event: object) -> None:
        if self._press_position is None:
            return super().mouseMoveEvent(event)
        distance = (_event_position(event) - self._press_position).manhattanLength()
        if distance >= QApplication.startDragDistance():
            self._dragging = True
            self._window._preview_column_drop(
                self._column_name,
                _event_global_position(event),
            )
        if hasattr(event, "accept"):
            event.accept()

    def mouseReleaseEvent(self, event: object) -> None:
        if self._press_position is None:
            return super().mouseReleaseEvent(event)
        global_position = _event_global_position(event)
        self._window._finish_column_drag(
            self._column_name,
            global_position,
            commit=self._dragging,
        )
        self._press_position = None
        self._dragging = False
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        if hasattr(event, "accept"):
            event.accept()


class MainWindow(QMainWindow):
    """Main window backed by the local mail store."""

    def __init__(
        self,
        mail_store: MailStore,
        mail_sync_service: MailSyncService | None = None,
        mail_send_service: MailSendService | None = None,
        oauth_login_service: OAuthLoginService | None = None,
    ) -> None:
        super().__init__()
        self._mail_store = mail_store
        self._mail_sync_service = mail_sync_service or MailSyncService(mail_store)
        self._mail_send_service = mail_send_service or MailSendService(mail_store)
        self._oauth_login_service = oauth_login_service or OAuthLoginService()
        self._messages_by_id: dict[int, Message] = {}
        self._current_messages: list[Message] = []
        self._current_folder_is_unified = False
        self._attachments_by_id: dict[int, Attachment] = {}
        self._sync_in_progress = False
        self._sync_thread: QThread | None = None
        self._sync_worker: MailSyncWorker | None = None
        self._send_in_progress = False
        self._send_thread: QThread | None = None
        self._send_worker: MailSendWorker | None = None
        self._column_panels: dict[str, QWidget] = {}
        self._column_handles: dict[str, ColumnDragHandle] = {}
        self._drag_original_order: tuple[str, ...] = DEFAULT_COLUMN_ORDER
        self._drag_source_column: str | None = None
        self._drop_preview: tuple[str, bool] | None = None
        self._message_toolbar_mode = "compact"

        self.setWindowTitle("Mailklient")
        self.resize(1200, 750)
        self.setStyleSheet(_MAIN_WINDOW_STYLESHEET)

        self._build_menu()
        self.setCentralWidget(self._build_central_widget())
        self.folder_list.currentItemChanged.connect(self._load_selected_folder)
        self.account_list.currentItemChanged.connect(self._load_selected_account)
        self.message_list.currentItemChanged.connect(self._show_selected_message)
        self._load_accounts()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self._update_message_toolbar_layout()

    def eventFilter(self, watched: object, event: object) -> bool:
        if (
            hasattr(self, "message_toolbar")
            and watched is self.message_toolbar
            and getattr(event, "type")() == QEvent.Type.Resize
        ):
            QTimer.singleShot(0, self._update_message_toolbar_layout)
        return super().eventFilter(watched, event)

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

        self.sync_account_action = QAction("Synk konto", self)
        self.sync_account_action.setObjectName("sync_account_action")
        self.sync_account_action.triggered.connect(self._sync_selected_account)

        account_menu.addAction(add_account_action)
        account_menu.addAction(delete_account_action)
        account_menu.addSeparator()
        account_menu.addAction(test_imap_action)
        account_menu.addAction(test_smtp_action)
        account_menu.addAction(oauth_login_action)
        account_menu.addAction(self.sync_account_action)

        message_menu = self.menuBar().addMenu("Melding")

        self.compose_action = QAction("Ny e-post", self)
        self.compose_action.setObjectName("compose_action")
        self.compose_action.triggered.connect(self._open_new_message_dialog)

        self.reply_action = QAction("Svar", self)
        self.reply_action.setObjectName("reply_action")
        self.reply_action.triggered.connect(self._reply_to_selected_message)

        self.forward_action = QAction("Videresend", self)
        self.forward_action.setObjectName("forward_action")
        self.forward_action.triggered.connect(self._forward_selected_message)

        self.mark_read_action = QAction("Marker som lest", self)
        self.mark_read_action.setObjectName("mark_read_action")
        self.mark_read_action.triggered.connect(self._mark_selected_message_read)

        self.mark_unread_action = QAction("Marker som ulest", self)
        self.mark_unread_action.setObjectName("mark_unread_action")
        self.mark_unread_action.triggered.connect(self._mark_selected_message_unread)

        self.archive_action = QAction("Arkiver", self)
        self.archive_action.setObjectName("archive_action")
        self.archive_action.triggered.connect(self._archive_selected_message)

        self.trash_action = QAction("Flytt til søppel", self)
        self.trash_action.setObjectName("trash_action")
        self.trash_action.triggered.connect(self._move_selected_message_to_trash)

        self.move_action = QAction("Flytt...", self)
        self.move_action.setObjectName("move_action")
        self.move_action.triggered.connect(self._move_selected_message)

        message_menu.addAction(self.compose_action)
        message_menu.addSeparator()
        message_menu.addAction(self.reply_action)
        message_menu.addAction(self.forward_action)
        message_menu.addSeparator()
        message_menu.addAction(self.mark_read_action)
        message_menu.addAction(self.mark_unread_action)
        message_menu.addSeparator()
        message_menu.addAction(self.archive_action)
        message_menu.addAction(self.trash_action)
        message_menu.addAction(self.move_action)

    def _build_central_widget(self) -> QSplitter:
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("main_splitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.splitterMoved.connect(
            lambda _position, _index: self._update_message_toolbar_layout()
        )

        self._column_panels = {
            "folders": self._build_folder_panel(),
            "reader": self._build_message_view(),
            "messages": self._build_message_list(),
        }
        self._apply_column_order(DEFAULT_COLUMN_ORDER)

        return self.main_splitter

    def _build_folder_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("folder_panel")
        panel.setMinimumWidth(210)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_column_drag_handle("folders"))

        app_title = QLabel("Mailklient")
        app_title.setObjectName("app_title")

        title = QLabel("Mapper")
        title.setObjectName("folder_panel_title")

        account_title = QLabel("Kontoer")
        account_title.setObjectName("account_panel_title")

        self.account_list = QListWidget()
        self.account_list.setObjectName("account_list")
        self.account_list.setMaximumHeight(128)
        self.account_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.add_account_button = QPushButton("Legg til")
        self.add_account_button.setObjectName("add_account_button")
        self.add_account_button.clicked.connect(self._open_add_account_dialog)

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

        account_button_layout = QHBoxLayout()
        account_button_layout.setSpacing(6)
        account_button_layout.addWidget(self.add_account_button)
        account_button_layout.addWidget(self.delete_account_button)

        account_tools_panel = QWidget()
        account_tools_panel.setObjectName("account_tools_panel")
        account_tools_layout = QVBoxLayout(account_tools_panel)
        account_tools_layout.setContentsMargins(8, 8, 8, 8)
        account_tools_layout.setSpacing(6)

        sync_button_layout = QHBoxLayout()
        sync_button_layout.setSpacing(6)
        sync_button_layout.addWidget(self.oauth_login_button)
        sync_button_layout.addWidget(self.sync_account_button)

        connection_test_layout = QHBoxLayout()
        connection_test_layout.setSpacing(6)
        connection_test_layout.addWidget(self.test_imap_button)
        connection_test_layout.addWidget(self.test_smtp_button)

        account_tools_layout.addLayout(account_button_layout)
        account_tools_layout.addLayout(sync_button_layout)
        account_tools_layout.addLayout(connection_test_layout)

        self.sync_status_label = QLabel("")
        self.sync_status_label.setObjectName("sync_status_label")
        self.sync_status_label.setWordWrap(True)
        self.sync_status_label.setMaximumHeight(48)

        self.folder_list = QListWidget()
        self.folder_list.setObjectName("folder_list")

        layout.addWidget(app_title)
        layout.addWidget(account_title)
        layout.addWidget(self.account_list)
        layout.addWidget(account_tools_panel)
        layout.addWidget(self.sync_status_label)
        layout.addWidget(title)
        layout.addWidget(self.folder_list, 1)

        return panel

    def _build_message_list(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("message_list_panel")
        panel.setMinimumWidth(320)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(self._build_column_drag_handle("messages"))

        self.message_search_edit = QLineEdit()
        self.message_search_edit.setObjectName("message_search_edit")
        self.message_search_edit.setPlaceholderText("Søk")
        self.message_search_edit.textChanged.connect(self._apply_message_filters)

        self.unread_filter_checkbox = QCheckBox("Uleste")
        self.unread_filter_checkbox.setObjectName("unread_filter_checkbox")
        self.unread_filter_checkbox.toggled.connect(self._apply_message_filters)

        self.message_sort_combo = QComboBox()
        self.message_sort_combo.setObjectName("message_sort_combo")
        self.message_sort_combo.addItem("Nyeste først", "date_desc")
        self.message_sort_combo.addItem("Eldste først", "date_asc")
        self.message_sort_combo.addItem("Avsender", "sender")
        self.message_sort_combo.addItem("Emne", "subject")
        self.message_sort_combo.currentIndexChanged.connect(self._apply_message_filters)

        filter_bar = QWidget()
        filter_bar.setObjectName("message_filter_bar")
        filter_bar.setFixedHeight(42)
        filter_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filter_layout = QHBoxLayout(filter_bar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        filter_layout.addWidget(self.unread_filter_checkbox)
        filter_layout.addWidget(self.message_sort_combo)

        self.message_list = QListWidget()
        self.message_list.setObjectName("message_list")
        self.message_list.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.message_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        layout.addWidget(self.message_search_edit)
        layout.addWidget(filter_bar)
        layout.addWidget(self.message_list, 1)
        return panel

    def _build_message_view(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("message_view_panel")
        panel.setMinimumWidth(520)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 8, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_column_drag_handle("reader"))

        self.compose_button = _toolbar_button(
            "Ny",
            "mail-message-new",
            "Ny e-post",
            "compose_button",
        )
        self.compose_button.setObjectName("compose_button")
        self.compose_button.clicked.connect(self._open_new_message_dialog)

        self.reply_button = _toolbar_button(
            "Svar",
            "mail-reply-sender",
            "Svar på valgt e-post",
            "reply_button",
        )
        self.reply_button.setObjectName("reply_button")
        self.reply_button.clicked.connect(self._reply_to_selected_message)

        self.forward_button = _toolbar_button(
            "Videresend",
            "mail-forward",
            "Videresend valgt e-post",
            "forward_button",
        )
        self.forward_button.setObjectName("forward_button")
        self.forward_button.clicked.connect(self._forward_selected_message)

        self.archive_button = _toolbar_button(
            "Arkiver",
            "archive-insert",
            "Arkiver valgt e-post",
            "archive_button",
        )
        self.archive_button.setObjectName("archive_button")
        self.archive_button.clicked.connect(self._archive_selected_message)

        self.trash_button = _toolbar_button(
            "Søppel",
            "user-trash",
            "Flytt valgt e-post til søppel",
            "trash_button",
        )
        self.trash_button.setObjectName("trash_button")
        self.trash_button.clicked.connect(self._move_selected_message_to_trash)

        self.move_button = _toolbar_button(
            "Flytt",
            "folder-move",
            "Flytt valgt e-post",
            "move_button",
        )
        self.move_button.setObjectName("move_button")
        self.move_button.clicked.connect(self._move_selected_message)

        self.mark_read_button = _toolbar_button(
            "Lest",
            "mail-mark-read",
            "Marker valgt e-post som lest",
            "mark_read_button",
        )
        self.mark_read_button.setObjectName("mark_read_button")
        self.mark_read_button.clicked.connect(self._mark_selected_message_read)

        self.mark_unread_button = _toolbar_button(
            "Ulest",
            "mail-mark-unread",
            "Marker valgt e-post som ulest",
            "mark_unread_button",
        )
        self.mark_unread_button.setObjectName("mark_unread_button")
        self.mark_unread_button.clicked.connect(self._mark_selected_message_unread)

        self.remote_content_checkbox = QCheckBox("Eksternt innhold")
        self.remote_content_checkbox.setObjectName("remote_content_checkbox")
        self.remote_content_checkbox.setFixedHeight(34)
        self.remote_content_checkbox.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.remote_content_checkbox.setToolTip(
            "Tillat eksterne bilder og medier i valgt e-post"
        )
        self.remote_content_checkbox.toggled.connect(self._toggle_remote_content)

        message_toolbar = QWidget()
        self.message_toolbar = message_toolbar
        self.message_toolbar.setObjectName("message_toolbar")
        self.message_toolbar.setFixedHeight(112)
        self.message_toolbar.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.message_toolbar.installEventFilter(self)
        self.message_toolbar_layout = QVBoxLayout(self.message_toolbar)
        self.message_toolbar_layout.setContentsMargins(8, 7, 8, 7)
        self.message_toolbar_layout.setSpacing(7)

        self.message_action_layout = QHBoxLayout()
        self.message_action_layout.setObjectName("message_action_layout")
        self.message_action_layout.setContentsMargins(0, 0, 0, 0)
        self.message_action_layout.setSpacing(6)

        self.message_organize_layout = QHBoxLayout()
        self.message_organize_layout.setObjectName("message_organize_layout")
        self.message_organize_layout.setContentsMargins(0, 0, 0, 0)
        self.message_organize_layout.setSpacing(6)

        self.message_toolbar_layout.addLayout(self.message_action_layout)
        self.message_toolbar_layout.addLayout(self.message_organize_layout)
        self._rebuild_message_toolbar(mode="compact")

        self.message_view = MessageViewer()
        self.message_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("attachment_list")
        self.attachment_list.setMaximumHeight(96)
        self.attachment_list.currentItemChanged.connect(
            lambda _current, _previous: self._preview_selected_attachment()
        )

        self.attachment_preview = QTextBrowser()
        self.attachment_preview.setObjectName("attachment_preview")
        self.attachment_preview.setMaximumHeight(170)
        self.attachment_preview.setReadOnly(True)

        self.open_attachment_button = QPushButton("Åpne")
        self.open_attachment_button.setObjectName("open_attachment_button")
        self.open_attachment_button.clicked.connect(self._open_selected_attachment)

        self.save_attachment_button = QPushButton("Lagre som")
        self.save_attachment_button.setObjectName("save_attachment_button")
        self.save_attachment_button.clicked.connect(self._save_selected_attachment)

        self.save_all_attachments_button = QPushButton("Lagre alle")
        self.save_all_attachments_button.setObjectName("save_all_attachments_button")
        self.save_all_attachments_button.clicked.connect(
            self._save_all_available_attachments
        )

        attachment_button_layout = QHBoxLayout()
        attachment_button_layout.setSpacing(6)
        attachment_button_layout.addWidget(self.open_attachment_button)
        attachment_button_layout.addWidget(self.save_attachment_button)
        attachment_button_layout.addWidget(self.save_all_attachments_button)
        attachment_button_layout.addStretch()

        layout.addWidget(self.message_toolbar)
        layout.addWidget(self.message_view, 1)
        layout.addWidget(self.attachment_list)
        layout.addWidget(self.attachment_preview)
        layout.addLayout(attachment_button_layout)
        self._show_attachments([])

        return panel

    def _build_column_drag_handle(self, column_name: str) -> ColumnDragHandle:
        handle = ColumnDragHandle(COLUMN_TITLES[column_name], column_name, self)
        self._column_handles[column_name] = handle
        return handle

    def _update_message_toolbar_layout(self) -> None:
        if not hasattr(self, "message_toolbar"):
            return
        width = self.message_toolbar.width()
        if width >= _single_row_toolbar_width(self):
            mode = "single"
        elif width >= _button_chain_toolbar_width(self):
            mode = "chain"
        else:
            mode = "compact"
        if mode != self._message_toolbar_mode:
            self._rebuild_message_toolbar(mode=mode)

    def _rebuild_message_toolbar(self, *, mode: str) -> None:
        _clear_layout(self.message_action_layout)
        _clear_layout(self.message_organize_layout)

        if mode == "compact":
            self.message_toolbar.setFixedHeight(112)
            self.message_action_layout.addWidget(self.compose_button)
            self.message_action_layout.addSpacing(8)
            self.message_action_layout.addWidget(self.reply_button)
            self.message_action_layout.addWidget(self.forward_button)
            self.message_action_layout.addStretch()
            self.message_action_layout.addWidget(self.remote_content_checkbox)

            self.message_organize_layout.addWidget(self.archive_button)
            self.message_organize_layout.addWidget(self.trash_button)
            self.message_organize_layout.addWidget(self.move_button)
            self.message_organize_layout.addSpacing(8)
            self.message_organize_layout.addWidget(self.mark_read_button)
            self.message_organize_layout.addWidget(self.mark_unread_button)
            self.message_organize_layout.addStretch()
        elif mode == "chain":
            self.message_toolbar.setFixedHeight(104)
            self.message_action_layout.addStretch()
            self.message_action_layout.addWidget(self.remote_content_checkbox)

            self.message_organize_layout.addWidget(self.compose_button)
            self.message_organize_layout.addSpacing(8)
            for button in (
                self.reply_button,
                self.forward_button,
                self.archive_button,
                self.trash_button,
                self.move_button,
                self.mark_read_button,
                self.mark_unread_button,
            ):
                self.message_organize_layout.addWidget(button)
            self.message_organize_layout.addStretch()
        else:
            self.message_toolbar.setFixedHeight(64)
            self.message_action_layout.addWidget(self.compose_button)
            self.message_action_layout.addSpacing(8)
            for button in (
                self.reply_button,
                self.forward_button,
                self.archive_button,
                self.trash_button,
                self.move_button,
                self.mark_read_button,
                self.mark_unread_button,
            ):
                self.message_action_layout.addWidget(button)
            self.message_action_layout.addStretch()
            self.message_action_layout.addWidget(self.remote_content_checkbox)

        self.message_organize_layout.setEnabled(mode != "single")
        self._message_toolbar_mode = mode

    def _begin_column_drag(self, _column_name: str) -> None:
        self._drag_original_order = self._column_order()
        self._drag_source_column = _column_name
        _set_widget_property(self._column_panels[_column_name], "dragSource", True)
        source_handle = self._column_handles.get(_column_name)
        if source_handle is not None:
            _set_widget_property(source_handle, "dragging", True)

    def _finish_column_drag(
        self,
        column_name: str,
        global_position: QPoint,
        *,
        commit: bool,
    ) -> None:
        if not commit:
            self._apply_column_order(self._drag_original_order)
            self._clear_column_drag_state()
            return

        target_column = self._column_at_global_position(global_position)
        if target_column is None or target_column == column_name:
            self._apply_column_order(self._drag_original_order)
            self._clear_column_drag_state()
            return

        target_panel = self._column_panels[target_column]
        local_position = target_panel.mapFromGlobal(global_position)
        insert_after_target = local_position.x() > target_panel.width() / 2
        new_order = _reordered_columns(
            self._drag_original_order,
            column_name,
            target_column,
            insert_after_target=insert_after_target,
        )
        self._apply_column_order(new_order)
        self._clear_column_drag_state()

    def _preview_column_drop(self, column_name: str, global_position: QPoint) -> None:
        target_column = self._column_at_global_position(global_position)
        if target_column is None or target_column == column_name:
            self._clear_column_drop_preview()
            return

        target_panel = self._column_panels[target_column]
        local_position = target_panel.mapFromGlobal(global_position)
        insert_after_target = local_position.x() > target_panel.width() / 2
        preview = (target_column, insert_after_target)
        if preview == self._drop_preview:
            return

        self._clear_column_drop_preview()
        self._drop_preview = preview
        preview_value = "after" if insert_after_target else "before"
        _set_widget_property(target_panel, "dropPreview", preview_value)
        target_handle = self._column_handles.get(target_column)
        if target_handle is not None:
            _set_widget_property(target_handle, "dropPreview", preview_value)

    def _clear_column_drag_state(self) -> None:
        self._clear_column_drop_preview()
        if self._drag_source_column is not None:
            _set_widget_property(
                self._column_panels[self._drag_source_column],
                "dragSource",
                False,
            )
            source_handle = self._column_handles.get(self._drag_source_column)
            if source_handle is not None:
                _set_widget_property(source_handle, "dragging", False)
        self._drag_source_column = None

    def _clear_column_drop_preview(self) -> None:
        if self._drop_preview is None:
            return
        target_column, _insert_after_target = self._drop_preview
        target_panel = self._column_panels.get(target_column)
        if target_panel is not None:
            _set_widget_property(target_panel, "dropPreview", "")
        target_handle = self._column_handles.get(target_column)
        if target_handle is not None:
            _set_widget_property(target_handle, "dropPreview", "")
        self._drop_preview = None

    def _column_order(self) -> tuple[str, ...]:
        panel_columns = {
            panel: column_name
            for column_name, panel in self._column_panels.items()
        }
        return tuple(
            panel_columns[self.main_splitter.widget(index)]
            for index in range(self.main_splitter.count())
        )

    def _column_at_global_position(self, global_position: QPoint) -> str | None:
        for column_name, panel in self._column_panels.items():
            local_position = panel.mapFromGlobal(global_position)
            if panel.rect().contains(local_position):
                return column_name
        return None

    def _apply_column_order(self, order: tuple[str, ...]) -> None:
        if not self._column_panels:
            return

        for index, column_name in enumerate(order):
            self.main_splitter.insertWidget(index, self._column_panels[column_name])
            stretch = 1 if column_name == "reader" else 0
            self.main_splitter.setStretchFactor(index, stretch)

        self.main_splitter.setSizes(
            [COLUMN_WIDTHS[column_name] for column_name in order]
        )
        self._update_message_toolbar_layout()

    def _load_accounts(self, select_account_id: int | None = None) -> None:
        self.account_list.clear()
        self.folder_list.clear()
        self.message_list.clear()
        self._current_messages = []
        self._current_folder_is_unified = False
        self.message_view.set_empty()
        self._show_attachments([])
        self._set_message_actions_enabled(False)

        accounts = self._mail_store.list_accounts()
        if not accounts:
            self._set_account_actions_enabled(False)
            self._set_compose_actions_enabled(False)
            return

        unified_item = QListWidgetItem("Alle innbokser")
        unified_item.setData(Qt.ItemDataRole.UserRole, UNIFIED_INBOX_ROLE)
        self.account_list.addItem(unified_item)

        selected_row = 0
        for account in accounts:
            item = QListWidgetItem(account.display_name)
            item.setData(Qt.ItemDataRole.UserRole, account.id)
            self.account_list.addItem(item)

            if select_account_id == account.id:
                selected_row = self.account_list.count() - 1

        self.account_list.setCurrentRow(selected_row)
        self._set_compose_actions_enabled(True)
        self._set_account_actions_enabled(self._selected_account_id() is not None)

    def _load_selected_account(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self.folder_list.clear()
        self.message_list.clear()
        self._messages_by_id.clear()
        self._current_messages = []
        self._current_folder_is_unified = False
        self.message_view.set_empty()
        self._show_attachments([])
        self._set_message_actions_enabled(False)

        if current is None:
            self._set_account_actions_enabled(False)
            return

        self._set_account_actions_enabled(True)
        account_id = current.data(Qt.ItemDataRole.UserRole)
        if account_id == UNIFIED_INBOX_ROLE:
            self._set_account_actions_enabled(False)
            item = QListWidgetItem("Alle innbokser")
            item.setData(Qt.ItemDataRole.UserRole, UNIFIED_INBOX_ROLE)
            self.folder_list.addItem(item)
            self.folder_list.setCurrentRow(0)
            return

        folders = sorted(
            [
                folder
                for folder in self._mail_store.list_folders(account_id)
                if _is_core_folder(folder.name, folder.remote_id)
            ],
            key=lambda folder: _folder_sort_key(folder.name),
        )

        for folder in folders:
            item = QListWidgetItem(_folder_display_name(folder.name))
            item.setToolTip(folder.name)
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
        self._current_messages = []
        self._current_folder_is_unified = False
        self.message_view.set_empty()
        self._show_attachments([])
        self._set_message_actions_enabled(False)

        if current is None:
            return

        folder_data = current.data(Qt.ItemDataRole.UserRole)
        self._current_folder_is_unified = folder_data == UNIFIED_INBOX_ROLE
        if folder_data == UNIFIED_INBOX_ROLE:
            messages = self._mail_store.list_unified_inbox_messages()
        else:
            account_id, folder_id = folder_data
            messages = self._mail_store.list_messages(account_id, folder_id)

        self._current_messages = messages
        self._apply_message_filters()

    def _apply_message_filters(self, *_args: object) -> None:
        self.message_list.clear()
        self._messages_by_id.clear()

        query = self.message_search_edit.text().strip().casefold()
        unread_only = self.unread_filter_checkbox.isChecked()
        messages = [
            message
            for message in self._current_messages
            if _message_matches_filters(message, query, unread_only)
        ]
        messages = _sort_messages(messages, self.message_sort_combo.currentData())

        for message in messages:
            item = QListWidgetItem()
            item.setData(MESSAGE_TEXT_ROLE, self._message_item_text(message))
            item.setData(Qt.ItemDataRole.UserRole, message.id)
            item.setSizeHint(QSize(280, 102))
            _configure_message_item(item, message)
            self.message_list.addItem(item)
            self.message_list.setItemWidget(item, self._build_message_item(message))
            self._messages_by_id[message.id] = message

    def _build_message_item(self, message: Message) -> QWidget:
        card = QWidget()
        card.setObjectName("message_item_card")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)

        meta_layout = QHBoxLayout()
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(6)

        sender = _message_sender_label(message)
        sender_label = QLabel(sender)
        sender_label.setObjectName("message_item_sender")

        date_label = QLabel(_short_message_date(message))
        date_label.setObjectName("message_item_date")
        date_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        meta_layout.addWidget(sender_label, 1)
        meta_layout.addWidget(date_label)

        subject_label = QLabel(message.subject or "(uten emne)")
        subject_label.setObjectName("message_item_subject")

        preview_label = QLabel(message.body_preview.strip())
        preview_label.setObjectName("message_item_preview")
        preview_label.setWordWrap(True)

        account = self._mail_store.get_account(message.account_id)
        if (
            self._current_folder_is_unified
            and account is not None
            and account.email_address.casefold() != sender.casefold()
        ):
            account_label = QLabel(account.email_address)
            account_label.setObjectName("message_item_account")
            layout.addWidget(account_label)

        layout.addLayout(meta_layout)
        layout.addWidget(subject_label)
        if message.body_preview.strip():
            layout.addWidget(preview_label)
        layout.addStretch()

        return card

    def _message_item_text(self, message: Message) -> str:
        subject = message.subject or "(uten emne)"
        sender = _message_sender_label(message)
        preview = message.body_preview.strip()
        if not self._current_folder_is_unified:
            return _compact_message_item_text(sender, subject, preview)

        account = self._mail_store.get_account(message.account_id)
        if account is None:
            return _compact_message_item_text(sender, subject, preview)
        return _compact_message_item_text(
            sender,
            subject,
            preview,
            account_label=account.email_address,
        )

    def _show_selected_message(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self.message_view.set_empty()
            self._show_attachments([])
            self._set_message_actions_enabled(False)
            return

        message_id = current.data(Qt.ItemDataRole.UserRole)
        message = self._messages_by_id[message_id]
        account = self._mail_store.get_account(message.account_id)
        account_label = account.email_address if account is not None else ""
        attachments = self._mail_store.list_attachments(message.id)
        self.message_view.show_message(message, account_label, attachments)
        self._show_attachments(attachments)
        self._set_message_actions_enabled(True)

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
        if self._sync_in_progress:
            self.sync_status_label.setText("Synkronisering kjører allerede.")
            return False

        self._start_sync_worker(account_id)
        return True

    def _open_new_message_dialog(self) -> bool:
        accounts = self._mail_store.list_accounts()
        if not accounts:
            QMessageBox.warning(self, "Kan ikke sende", "Legg til en konto først.")
            return False

        account_id = self._selected_account_id() or accounts[0].id
        return self._open_compose_dialog(ComposeDraft(account_id=account_id))

    def _reply_to_selected_message(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False

        draft = self._mail_send_service.create_reply_draft(message_id)
        if draft is None:
            return False
        return self._open_compose_dialog(draft)

    def _forward_selected_message(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False

        draft = self._mail_send_service.create_forward_draft(message_id)
        if draft is None:
            return False
        return self._open_compose_dialog(draft)

    def _open_compose_dialog(self, draft: ComposeDraft) -> bool:
        accounts = self._mail_store.list_accounts()
        if not accounts:
            QMessageBox.warning(self, "Kan ikke sende", "Legg til en konto først.")
            return False

        dialog = ComposeDialog(accounts, draft, self)
        if dialog.exec() != ComposeDialog.DialogCode.Accepted:
            return False

        return self._send_draft(dialog.draft())

    def _send_draft(self, draft: ComposeDraft) -> bool:
        if self._send_in_progress:
            self.sync_status_label.setText("Sending kjører allerede.")
            return False

        self._start_send_worker(draft)
        return True

    def _start_send_worker(self, draft: ComposeDraft) -> None:
        self._set_send_in_progress(True)
        self.sync_status_label.setText("Sender...")

        thread = QThread(self)
        worker = MailSendWorker(self._mail_send_service, draft)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_send_finished)
        worker.failed.connect(self._handle_send_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_send_worker)

        self._send_thread = thread
        self._send_worker = worker
        thread.start()

    def _handle_send_finished(self, result: object) -> None:
        if isinstance(result, SendResult):
            if result.sent:
                self.sync_status_label.setText(_send_status_text(result))
                self._reload_current_folder()
                return
        elif result:
            self.sync_status_label.setText("E-post sendt.")
            self._reload_current_folder()
            return

        self.sync_status_label.setText("Sending feilet")
        QMessageBox.warning(
            self,
            "Sending feilet",
            "Mangler konto-, SMTP- eller innloggingsinformasjon.",
        )

    def _handle_send_failed(self, error_message: str) -> None:
        self.sync_status_label.setText("Sending feilet")
        QMessageBox.warning(
            self,
            "Sending feilet",
            _friendly_error_message(error_message),
        )

    def _cleanup_send_worker(self) -> None:
        self._send_thread = None
        self._send_worker = None
        self._set_send_in_progress(False)

    def _reload_current_folder(self) -> None:
        current = self.folder_list.currentItem()
        if current is not None:
            self._load_selected_folder(current, None)

    def _toggle_remote_content(self, allowed: bool) -> None:
        self.message_view.set_remote_content_allowed(allowed)
        self._rerender_selected_message()

    def _rerender_selected_message(self) -> None:
        message_id = self._selected_message_id()
        if message_id is None:
            return

        message = self._messages_by_id.get(message_id)
        if message is None:
            return

        account = self._mail_store.get_account(message.account_id)
        account_label = account.email_address if account is not None else ""
        attachments = self._mail_store.list_attachments(message.id)
        self.message_view.show_message(message, account_label, attachments)
        self._show_attachments(attachments)

    def _show_attachments(self, attachments: list[Attachment]) -> None:
        self._attachments_by_id = {attachment.id: attachment for attachment in attachments}
        has_attachments = bool(attachments)
        self.attachment_list.setVisible(has_attachments)
        self.attachment_preview.setVisible(False)
        self.open_attachment_button.setVisible(has_attachments)
        self.save_attachment_button.setVisible(has_attachments)
        self.save_all_attachments_button.setVisible(has_attachments)
        self.attachment_list.clear()
        for attachment in attachments:
            suffix = "" if attachment.has_content else " - ikke lagret lokalt"
            item = QListWidgetItem(
                f"{_attachment_type_label(attachment)} {attachment.filename} "
                f"({_format_attachment_size(attachment.size)}, "
                f"{_display_content_type(attachment.content_type)}){suffix}"
            )
            item.setData(Qt.ItemDataRole.UserRole, attachment.id)
            item.setToolTip(attachment.filename)
            self.attachment_list.addItem(item)
        self._preview_selected_attachment()

    def _selected_attachment(self) -> Attachment | None:
        current = self.attachment_list.currentItem()
        if current is None:
            return None
        attachment_id = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(attachment_id, int):
            return None
        return self._attachments_by_id.get(attachment_id)

    def _update_attachment_actions(self) -> None:
        attachment = self._selected_attachment()
        enabled = attachment is not None and attachment.has_content
        self.open_attachment_button.setEnabled(enabled)
        self.save_attachment_button.setEnabled(enabled)
        self.save_all_attachments_button.setEnabled(
            any(attachment.has_content for attachment in self._attachments_by_id.values())
        )

    def _preview_selected_attachment(self) -> None:
        attachment = self._selected_attachment()
        self._update_attachment_actions()
        if attachment is None:
            self.attachment_preview.setHtml("")
            self.attachment_preview.setVisible(False)
            return
        self.attachment_preview.setVisible(True)
        if not attachment.has_content:
            self.attachment_preview.setHtml(
                "<p>Vedlegget er ikke lagret lokalt ennå.</p>"
            )
            return

        content = self._mail_store.get_attachment_content(attachment.id)
        if content is None:
            self.attachment_preview.setHtml(
                "<p>Vedlegget er ikke lagret lokalt ennå.</p>"
            )
            return

        self.attachment_preview.setHtml(_attachment_preview_html(attachment, content))

    def _save_selected_attachment(self) -> bool:
        attachment = self._selected_attachment()
        if attachment is None:
            return False

        content = self._mail_store.get_attachment_content(attachment.id)
        if content is None:
            QMessageBox.warning(
                self,
                "Vedlegg mangler",
                "Vedlegget er ikke lagret lokalt ennå.",
            )
            return False

        save_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Lagre vedlegg",
            str(Path.home() / "Nedlastinger" / _safe_filename(attachment.filename)),
        )
        if not save_path:
            return False

        Path(save_path).write_bytes(content)
        self.sync_status_label.setText("Vedlegg lagret.")
        return True

    def _save_all_available_attachments(self) -> bool:
        attachments = [
            attachment
            for attachment in self._attachments_by_id.values()
            if attachment.has_content
        ]
        if not attachments:
            return False

        directory = QFileDialog.getExistingDirectory(
            self,
            "Lagre alle vedlegg",
            str(Path.home() / "Nedlastinger"),
        )
        if not directory:
            return False

        target_dir = Path(directory)
        saved_count = 0
        for attachment in attachments:
            content = self._mail_store.get_attachment_content(attachment.id)
            if content is None:
                continue
            target_path = _unique_attachment_path(
                target_dir,
                _safe_filename(attachment.filename),
            )
            target_path.write_bytes(content)
            saved_count += 1

        if saved_count == 0:
            return False

        self.sync_status_label.setText(f"Lagret {saved_count} vedlegg.")
        return True

    def _open_selected_attachment(self) -> bool:
        attachment = self._selected_attachment()
        if attachment is None:
            return False

        content = self._mail_store.get_attachment_content(attachment.id)
        if content is None:
            QMessageBox.warning(
                self,
                "Vedlegg mangler",
                "Vedlegget er ikke lagret lokalt ennå.",
            )
            return False

        cache_dir = Path(tempfile.gettempdir()) / "mailklient-attachments"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{attachment.id}-{_safe_filename(attachment.filename)}"
        path.write_bytes(content)

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not opened:
            QMessageBox.warning(
                self,
                "Kunne ikke åpne vedlegg",
                "Fant ingen app som kunne åpne filen.",
            )
            return False

        return True

    def _mark_selected_message_read(self) -> bool:
        return self._mark_selected_message(is_read=True)

    def _mark_selected_message_unread(self) -> bool:
        return self._mark_selected_message(is_read=False)

    def _mark_selected_message(self, *, is_read: bool) -> bool:
        current = self.message_list.currentItem()
        if current is None:
            return False

        message_id = current.data(Qt.ItemDataRole.UserRole)
        try:
            updated = self._mail_sync_service.mark_message_read(message_id, is_read)
        except Exception as error:
            self._show_sync_error("Kunne ikke oppdatere melding", error)
            return False

        if not updated:
            self.sync_status_label.setText("Kunne ikke oppdatere melding.")
            return False

        message = self._mail_store.get_message(message_id)
        if message is not None:
            self._messages_by_id[message.id] = message
            _configure_message_item(current, message)
            account = self._mail_store.get_account(message.account_id)
            account_label = account.email_address if account is not None else ""
            attachments = self._mail_store.list_attachments(message.id)
            self.message_view.show_message(message, account_label, attachments)
            self._show_attachments(attachments)

        self.sync_status_label.setText(
            "Melding markert som lest." if is_read else "Melding markert som ulest."
        )
        return True

    def _move_selected_message_to_trash(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False

        try:
            moved = self._mail_sync_service.move_message_to_trash(message_id)
        except Exception as error:
            self._show_sync_error("Kunne ikke flytte til søppel", error)
            return False

        if moved:
            self.sync_status_label.setText("Melding flyttet til søppel.")
            self._reload_current_folder()
        return moved

    def _archive_selected_message(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False

        try:
            archived = self._mail_sync_service.archive_message(message_id)
        except Exception as error:
            self._show_sync_error("Kunne ikke arkivere melding", error)
            return False

        if archived:
            self.sync_status_label.setText("Melding arkivert.")
            self._reload_current_folder()
        return archived

    def _move_selected_message(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False

        message = self._messages_by_id.get(message_id)
        if message is None:
            return False

        folders = [
            folder
            for folder in self._mail_store.list_folders(message.account_id)
            if folder.id != message.folder_id
            and _is_core_folder(folder.name, folder.remote_id)
        ]
        if not folders:
            return False

        labels = [_folder_display_name(folder.name) for folder in folders]
        selected_label, accepted = QInputDialog.getItem(
            self,
            "Flytt melding",
            "Mappe",
            labels,
            0,
            False,
        )
        if not accepted:
            return False

        destination = folders[labels.index(selected_label)]
        try:
            moved = self._mail_sync_service.move_message_to_folder(
                message_id,
                destination.id,
            )
        except Exception as error:
            self._show_sync_error("Kunne ikke flytte melding", error)
            return False

        if moved:
            self.sync_status_label.setText(
                f"Melding flyttet til {_folder_display_name(destination.name)}."
            )
            self._reload_current_folder()
        return moved

    def _start_sync_worker(self, account_id: int) -> None:
        self._set_sync_in_progress(True)
        self.sync_status_label.setText("Synkroniserer...")

        thread = QThread(self)
        worker = MailSyncWorker(self._mail_sync_service, account_id)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_sync_finished)
        worker.failed.connect(self._handle_sync_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_sync_worker)

        self._sync_thread = thread
        self._sync_worker = worker
        thread.start()

    def _handle_sync_finished(self, account_id: int, result: object) -> None:
        if not isinstance(result, HeaderSyncResult):
            self.sync_status_label.setText("Synk fullført.")
            return
        self._load_accounts(select_account_id=account_id)
        self.sync_status_label.setText(
            f"Synkroniserte {result.folders_seen} mapper og "
            f"{result.messages_seen} meldinger."
        )

    def _handle_sync_failed(self, _account_id: int, error_message: str) -> None:
        self.sync_status_label.setText("Synk feilet")
        QMessageBox.warning(
            self,
            "Synk feilet",
            _friendly_error_message(error_message),
        )

    def _cleanup_sync_worker(self) -> None:
        self._sync_thread = None
        self._sync_worker = None
        self._set_sync_in_progress(False)

    def _selected_account_id(self) -> int | None:
        current = self.account_list.currentItem()
        if current is None:
            return None
        account_id = current.data(Qt.ItemDataRole.UserRole)
        return account_id if isinstance(account_id, int) else None

    def _selected_message_id(self) -> int | None:
        current = self.message_list.currentItem()
        if current is None:
            return None
        message_id = current.data(Qt.ItemDataRole.UserRole)
        return message_id if isinstance(message_id, int) else None

    def _set_account_actions_enabled(self, enabled: bool) -> None:
        effective_enabled = enabled and not self._sync_in_progress
        self.delete_account_button.setEnabled(effective_enabled)
        self.test_imap_button.setEnabled(effective_enabled)
        self.test_smtp_button.setEnabled(effective_enabled)
        self.oauth_login_button.setEnabled(effective_enabled)
        self.sync_account_button.setEnabled(effective_enabled)
        self.sync_account_action.setEnabled(effective_enabled)

    def _set_message_actions_enabled(self, enabled: bool) -> None:
        effective_enabled = enabled and not self._send_in_progress
        self.reply_button.setEnabled(effective_enabled)
        self.forward_button.setEnabled(effective_enabled)
        self.reply_action.setEnabled(effective_enabled)
        self.forward_action.setEnabled(effective_enabled)
        self.mark_read_button.setEnabled(enabled)
        self.mark_unread_button.setEnabled(enabled)
        self.mark_read_action.setEnabled(enabled)
        self.mark_unread_action.setEnabled(enabled)
        self.archive_button.setEnabled(effective_enabled)
        self.trash_button.setEnabled(effective_enabled)
        self.move_button.setEnabled(effective_enabled)
        self.archive_action.setEnabled(effective_enabled)
        self.trash_action.setEnabled(effective_enabled)
        self.move_action.setEnabled(effective_enabled)

    def _set_compose_actions_enabled(self, enabled: bool) -> None:
        effective_enabled = enabled and not self._send_in_progress
        self.compose_button.setEnabled(effective_enabled)
        self.compose_action.setEnabled(effective_enabled)

    def _set_send_in_progress(self, in_progress: bool) -> None:
        self._send_in_progress = in_progress
        self.compose_button.setText("Sender..." if in_progress else "Ny")
        self._set_compose_actions_enabled(bool(self._mail_store.list_accounts()))
        self._set_message_actions_enabled(self._selected_message_id() is not None)

    def _set_sync_in_progress(self, in_progress: bool) -> None:
        self._sync_in_progress = in_progress
        self.sync_account_button.setText("Synker..." if in_progress else "Synk")
        self._set_account_actions_enabled(self._selected_account_id() is not None)

    def _show_sync_error(self, title: str, error: Exception) -> None:
        self.sync_status_label.setText(title)
        QMessageBox.warning(self, title, _friendly_error_message(error))


def _folder_sort_key(name: str) -> tuple[int, str]:
    preferred_order = {
        "innboks": 0,
        "inbox": 0,
        "sendt": 4,
        "sent": 4,
        "sent mail": 4,
        "spam": 90,
        "junk": 90,
        "søppelpost": 90,
        "papirkurv": 99,
        "trash": 99,
        "deleted items": 99,
    }
    normalized_name = _normalized_folder_name(name)
    return (preferred_order.get(normalized_name, 100), normalized_name)


def _configure_message_item(item: QListWidgetItem, message: Message) -> None:
    font = QFont()
    font.setBold(not message.is_read)
    item.setFont(font)
    item.setToolTip(message.body_preview)


def _toolbar_button(
    text: str,
    icon_name: str,
    tooltip: str,
    object_name: str,
) -> QToolButton:
    button = QToolButton()
    button.setObjectName(object_name)
    button.setText(text)
    button.setToolTip(tooltip)
    button.setIcon(QIcon.fromTheme(icon_name))
    button.setIconSize(QSize(18, 18))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setAutoRaise(False)
    button.setFixedHeight(40)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return button


def _clear_layout(layout: QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(layout.parentWidget())


def _set_widget_property(widget: QWidget, name: str, value: object) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _single_row_toolbar_width(window: MainWindow) -> int:
    controls = (
        window.compose_button,
        window.reply_button,
        window.forward_button,
        window.archive_button,
        window.trash_button,
        window.move_button,
        window.mark_read_button,
        window.mark_unread_button,
        window.remote_content_checkbox,
    )
    controls_width = sum(control.sizeHint().width() for control in controls)
    spacings_width = 6 * (len(controls) - 1) + 16
    margins_width = 16
    buffer_width = 24
    return controls_width + spacings_width + margins_width + buffer_width


def _button_chain_toolbar_width(window: MainWindow) -> int:
    buttons = (
        window.compose_button,
        window.reply_button,
        window.forward_button,
        window.archive_button,
        window.trash_button,
        window.move_button,
        window.mark_read_button,
        window.mark_unread_button,
    )
    buttons_width = sum(button.sizeHint().width() for button in buttons)
    spacings_width = 6 * (len(buttons) - 1) + 8
    margins_width = 16
    buffer_width = 24
    return buttons_width + spacings_width + margins_width + buffer_width


def _reordered_columns(
    order: tuple[str, ...],
    source_column: str,
    target_column: str,
    *,
    insert_after_target: bool,
) -> tuple[str, ...]:
    remaining = [column for column in order if column != source_column]
    target_index = remaining.index(target_column)
    insert_index = target_index + 1 if insert_after_target else target_index
    remaining.insert(insert_index, source_column)
    return tuple(remaining)


def _event_button(event: object) -> Qt.MouseButton:
    button = getattr(event, "button")()
    return button


def _event_position(event: object) -> QPoint:
    position = getattr(event, "position")()
    return position.toPoint()


def _event_global_position(event: object) -> QPoint:
    global_position = getattr(event, "globalPosition")()
    return global_position.toPoint()


def _message_sender_label(message: Message) -> str:
    sender = message.sender.strip()
    if sender:
        return sender
    recipients = message.recipients.strip()
    if recipients:
        return f"Til {recipients}"
    return "(ukjent)"


def _compact_message_item_text(
    sender: str,
    subject: str,
    preview: str,
    *,
    account_label: str | None = None,
) -> str:
    lines = []
    if account_label and account_label.casefold() != sender.casefold():
        lines.append(account_label)
    lines.append(sender)
    lines.append(subject)
    if preview:
        lines.append(preview)
    return "\n".join(lines)


def _message_matches_filters(
    message: Message,
    query: str,
    unread_only: bool,
) -> bool:
    if unread_only and message.is_read:
        return False
    if not query:
        return True

    searchable = " ".join(
        [
            message.subject,
            message.sender,
            message.recipients,
            message.body_preview,
        ]
    ).casefold()
    return query in searchable


def _sort_messages(messages: list[Message], sort_key: object) -> list[Message]:
    if sort_key == "date_asc":
        return sorted(messages, key=_message_date_value)
    if sort_key == "sender":
        return sorted(messages, key=lambda message: message.sender.casefold())
    if sort_key == "subject":
        return sorted(messages, key=lambda message: message.subject.casefold())
    return sorted(messages, key=_message_date_value, reverse=True)


def _message_date_value(message: Message) -> str:
    return message.received_at or message.sent_at or ""


def _short_message_date(message: Message) -> str:
    value = _message_date_value(message)
    if not value:
        return ""
    if "T" in value:
        return value.split("T", maxsplit=1)[0]
    return value[:16]


def _folder_display_name(name: str) -> str:
    normalized_name = _normalized_folder_name(name)
    display_names = {
        "inbox": "Innboks",
        "innboks": "Innboks",
        "sent": "Sendt",
        "sent mail": "Sendt",
        "sendt": "Sendt",
        "trash": "Papirkurv",
        "papirkurv": "Papirkurv",
        "deleted items": "Papirkurv",
        "spam": "Søppelpost",
        "junk": "Søppelpost",
        "søppelpost": "Søppelpost",
    }
    return display_names.get(normalized_name, name.rsplit("/", maxsplit=1)[-1])


def _is_core_folder(name: str, remote_id: str | None = None) -> bool:
    aliases = _folder_aliases(name)
    if remote_id:
        aliases.update(_folder_aliases(remote_id))
    return bool(
        aliases
        & {
            "inbox",
            "innboks",
            "sent",
            "sent mail",
            "sendt",
            "spam",
            "junk",
            "søppelpost",
            "trash",
            "papirkurv",
            "deleted items",
        }
    )


def _folder_aliases(name: str) -> set[str]:
    normalized = _normalized_folder_name(name)
    aliases = {name.casefold(), normalized}
    if normalized in {"inbox", "innboks"}:
        aliases.update({"inbox", "innboks"})
    elif normalized in {"sent", "sent mail", "sendt"}:
        aliases.update({"sent", "sent mail", "sendt"})
    elif normalized in {"spam", "junk", "søppelpost"}:
        aliases.update({"spam", "junk", "søppelpost"})
    elif normalized in {"trash", "papirkurv", "deleted items"}:
        aliases.update({"trash", "papirkurv", "deleted items"})
    return aliases


def _friendly_error_message(error: Exception | str) -> str:
    if isinstance(error, str):
        return error or "Ukjent feil."
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "SMTP-innlogging feilet. Sjekk konto/OAuth og prøv igjen."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "SMTP-serveren avviste mottakeren."
    if isinstance(error, smtplib.SMTPException):
        return f"SMTP-feil: {error}"
    if isinstance(error, OAuthCallbackError):
        return f"OAuth-feil: {error}"
    if isinstance(error, TimeoutError):
        return "Tilkoblingen tok for lang tid."
    if isinstance(error, OSError):
        return f"Nettverksfeil: {error}"
    return str(error) or error.__class__.__name__


def _send_status_text(result: SendResult) -> str:
    if not result.server_copy_attempted:
        return "E-post sendt. Sendt-kopi håndteres av mailserveren."
    if result.server_copy_saved:
        return "E-post sendt og lagret i Sendt."
    return "E-post sendt, men serverkopi til Sendt feilet."


def _format_attachment_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _attachment_preview_html(attachment: Attachment, content: bytes) -> str:
    escaped_name = _html_escape(attachment.filename)
    escaped_type = _html_escape(attachment.content_type)
    size = _format_attachment_size(len(content))
    summary = (
        f"<p><strong>{escaped_name}</strong><br>"
        f"{_html_escape(_display_content_type(attachment.content_type))}, {size}</p>"
    )

    if attachment.content_type.startswith("image/"):
        encoded = base64.b64encode(content).decode("ascii")
        return (
            summary
            +
            f'<img src="data:{escaped_type};base64,{encoded}" '
            'style="max-width: 100%; max-height: 130px;">'
        )

    if attachment.content_type.startswith("text/"):
        return (
            summary
            +
            f"<pre>{_html_escape(_decode_preview_text(content))}</pre>"
        )

    if (
        attachment.content_type == "application/pdf"
        or attachment.filename.casefold().endswith(".pdf")
    ):
        page_count = _guess_pdf_page_count(content)
        page_text = f"{page_count} sider" if page_count is not None else "PDF-dokument"
        return (
            summary
            +
            f"<p>{page_text}. Bruk Åpne for å forhåndsvise i PDF-leseren, "
            "eller Lagre som hvis du vil beholde filen.</p>"
        )

    return (
        summary
        +
        "<p>Direkte forhåndsvisning støttes ikke for denne filtypen. "
        "Bruk Åpne eller Lagre som.</p>"
    )


def _attachment_type_label(attachment: Attachment) -> str:
    content_type = attachment.content_type.casefold()
    filename = attachment.filename.casefold()
    if content_type.startswith("image/"):
        return "[BILDE]"
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        return "[PDF]"
    if content_type.startswith("text/"):
        return "[TEKST]"
    if content_type.startswith("audio/"):
        return "[LYD]"
    if content_type.startswith("video/"):
        return "[VIDEO]"
    if "zip" in content_type or filename.endswith((".zip", ".tar", ".gz")):
        return "[ARKIV]"
    return "[FIL]"


def _display_content_type(content_type: str) -> str:
    labels = {
        "application/pdf": "PDF",
        "application/zip": "ZIP-arkiv",
        "text/plain": "Tekst",
        "text/html": "HTML",
        "image/jpeg": "JPEG-bilde",
        "image/png": "PNG-bilde",
        "image/gif": "GIF-bilde",
    }
    return labels.get(content_type.casefold(), content_type)


def _guess_pdf_page_count(content: bytes) -> int | None:
    if not content.startswith(b"%PDF"):
        return None
    matches = set()
    for match in re.finditer(rb"/Type\s*/Page\b", content):
        matches.add(match.start())
    if not matches:
        return None
    return len(matches)


def _decode_preview_text(content: bytes, max_length: int = 8000) -> str:
    preview = content[:max_length]
    for encoding in ("utf-8", "latin-1"):
        try:
            text = preview.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = preview.decode("utf-8", errors="replace")

    if len(content) > max_length:
        text += "\n\n..."
    return text


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _safe_filename(filename: str) -> str:
    safe_characters = []
    for character in filename:
        if character.isalnum() or character in {" ", ".", "-", "_"}:
            safe_characters.append(character)
        else:
            safe_characters.append("_")

    safe_name = "".join(safe_characters).strip(" .")
    return safe_name or "vedlegg"


def _unique_attachment_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    if not path.exists():
        return path

    stem = path.stem or "vedlegg"
    suffix = path.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _normalized_folder_name(name: str) -> str:
    return name.rsplit("/", maxsplit=1)[-1].casefold()


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


_MAIN_WINDOW_STYLESHEET = """
QMainWindow {
    background: #f4f7fa;
    color: #232629;
    font-family: "Noto Sans", "Inter", "Segoe UI", sans-serif;
    font-size: 10pt;
}

QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #d7e0ea;
    padding: 3px 8px;
}

QMenuBar::item {
    border-radius: 6px;
    padding: 6px 10px;
}

QMenuBar::item:selected {
    background: #e7f4fd;
    color: #232629;
}

QSplitter::handle {
    background: #d7e0ea;
    width: 1px;
}

QWidget#folder_panel {
    background: #edf3f8;
    border-right: 1px solid #d7e0ea;
}

QWidget#message_list_panel,
QWidget#message_view_panel {
    background: #f8fafc;
}

QWidget#message_list_panel {
    border-left: 1px solid #d7e0ea;
}

QWidget#message_view_panel {
    background: #f8fafc;
}

QWidget#folder_panel[dragSource="true"],
QWidget#message_view_panel[dragSource="true"],
QWidget#message_list_panel[dragSource="true"] {
    background: #eef8fd;
}

QWidget#folder_panel[dropPreview="before"],
QWidget#message_view_panel[dropPreview="before"],
QWidget#message_list_panel[dropPreview="before"] {
    border-left: 5px solid #3daee9;
    background: #eaf8ff;
}

QWidget#folder_panel[dropPreview="after"],
QWidget#message_view_panel[dropPreview="after"],
QWidget#message_list_panel[dropPreview="after"] {
    border-right: 5px solid #3daee9;
    background: #eaf8ff;
}

QWidget#message_toolbar {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
}

QWidget#account_tools_panel {
    background: #f8fbfd;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
    margin: 2px 0 6px;
}

QWidget#message_filter_bar {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
    padding: 5px;
}

QLabel#app_title {
    color: #232629;
    font-size: 20px;
    font-weight: 700;
    padding: 2px 6px 4px;
}

QLabel#folder_panel_title,
QLabel#account_panel_title,
QLabel#message_list_title {
    color: #52616f;
    font-size: 13px;
    font-weight: 600;
    padding: 8px 6px 2px;
}

QLabel#column_drag_handle_folders,
QLabel#column_drag_handle_reader,
QLabel#column_drag_handle_messages {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
    color: #52616f;
    font-size: 12px;
    font-weight: 700;
    min-height: 28px;
    padding: 5px 10px;
}

QLabel#column_drag_handle_folders[dragging="true"],
QLabel#column_drag_handle_reader[dragging="true"],
QLabel#column_drag_handle_messages[dragging="true"] {
    background: #dff2fc;
    border: 1px solid #3daee9;
    color: #14384b;
}

QLabel#column_drag_handle_folders[dropPreview="before"],
QLabel#column_drag_handle_reader[dropPreview="before"],
QLabel#column_drag_handle_messages[dropPreview="before"] {
    background: #e7f7ff;
    border-left: 5px solid #3daee9;
    color: #14384b;
}

QLabel#column_drag_handle_folders[dropPreview="after"],
QLabel#column_drag_handle_reader[dropPreview="after"],
QLabel#column_drag_handle_messages[dropPreview="after"] {
    background: #e7f7ff;
    border-right: 5px solid #3daee9;
    color: #14384b;
}

QListWidget#account_list,
QListWidget#folder_list,
QListWidget#message_list,
QListWidget#attachment_list {
    background: transparent;
    border: 0;
    color: #232629;
    outline: 0;
}

QListWidget#account_list::item,
QListWidget#folder_list::item {
    border-radius: 6px;
    margin: 2px 6px;
    min-height: 30px;
    padding: 6px 10px;
}

QListWidget#account_list::item:hover,
QListWidget#folder_list::item:hover {
    background: #e1eaf2;
}

QListWidget#account_list::item:selected,
QListWidget#folder_list::item:selected {
    background: #dff2fc;
    border-left: 3px solid #3daee9;
    color: #14384b;
}

QListWidget#message_list {
    background: transparent;
    border: 0;
    border-radius: 8px;
}

QListWidget#message_list::item {
    background: #ffffff;
    border: 1px solid #dfe7ef;
    border-radius: 8px;
    margin: 5px 2px;
    min-height: 96px;
}

QListWidget#message_list::item:hover {
    background: #f8fcff;
    border: 1px solid #9fcde8;
}

QListWidget#message_list::item:selected {
    background: #dff2fc;
    border: 1px solid #3daee9;
    color: #14384b;
}

QWidget#message_item_card {
    background: transparent;
}

QLabel#message_item_account {
    color: #22769f;
    font-size: 11px;
    font-weight: 600;
}

QLabel#message_item_sender {
    color: #1f2933;
    font-size: 13px;
    font-weight: 700;
}

QLabel#message_item_date {
    color: #7b8794;
    font-size: 11px;
}

QLabel#message_item_subject {
    color: #232629;
    font-size: 13px;
    font-weight: 600;
}

QLabel#message_item_preview {
    color: #627282;
    font-size: 12px;
}

QListWidget#attachment_list {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 6px;
    margin: 0;
}

QListWidget#attachment_list::item {
    border-radius: 4px;
    margin: 2px;
    min-height: 28px;
    padding: 6px 8px;
}

QListWidget#attachment_list::item:selected {
    background: #d8edf9;
    color: #14384b;
}

QTextBrowser#attachment_preview {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 6px;
    color: #232629;
    margin: 0;
    padding: 8px;
}

QLineEdit,
QComboBox {
    background: #ffffff;
    border: 1px solid #b8c6d3;
    border-radius: 6px;
    color: #232629;
    min-height: 30px;
    padding: 4px 8px;
    selection-background-color: #3daee9;
}

QLineEdit#message_search_edit {
    border-radius: 8px;
    min-height: 34px;
}

QLineEdit:focus,
QComboBox:focus {
    border: 1px solid #3daee9;
}

QComboBox::drop-down {
    border: 0;
    width: 24px;
}

QCheckBox {
    color: #4b5563;
    padding: 4px 8px;
}

QCheckBox#remote_content_checkbox {
    background: #f8fbfd;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
    color: #31546c;
    min-width: 136px;
    padding: 6px 10px;
}

QCheckBox::indicator {
    background: #ffffff;
    border: 1px solid #9aa8b5;
    border-radius: 4px;
    height: 15px;
    width: 15px;
}

QCheckBox::indicator:checked {
    background: #3daee9;
    border: 1px solid #2586bd;
}

QPushButton {
    background: #ffffff;
    border: 1px solid #b8c6d3;
    border-radius: 6px;
    color: #232629;
    font-weight: 500;
    min-height: 30px;
    padding: 4px 10px;
}

QPushButton#compose_button {
    min-height: 30px;
}

QPushButton:hover {
    background: #e7f4fd;
    border: 1px solid #3daee9;
}

QPushButton:pressed {
    background: #cbe7f7;
}

QPushButton:disabled {
    background: #eef2f6;
    border: 1px solid #d7dee7;
    color: #9aa8b5;
}

QToolButton {
    background: #ffffff;
    border: 1px solid #b8c6d3;
    border-radius: 7px;
    color: #232629;
    font-weight: 600;
    min-height: 32px;
    min-width: 68px;
    padding: 5px 8px;
}

QToolButton:hover {
    background: #eaf6fd;
    border: 1px solid #3daee9;
}

QToolButton:pressed {
    background: #cfeaf8;
}

QToolButton:disabled {
    background: #f1f4f7;
    border: 1px solid #d7dee7;
    color: #9aa8b5;
}

QToolButton#compose_button {
    background: #3daee9;
    border: 1px solid #2586bd;
    color: #ffffff;
    min-width: 72px;
}

QToolButton#forward_button {
    min-width: 96px;
}

QToolButton#mark_read_button,
QToolButton#mark_unread_button {
    min-width: 60px;
}

QToolButton#compose_button:hover {
    background: #45b8f2;
}

QToolButton#trash_button {
    color: #8f3a38;
}

QLabel#sync_status_label {
    color: #31546c;
    font-size: 12px;
    margin: 0 6px;
    padding: 0 2px;
}

QWidget#message_view {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
}

QScrollBar:vertical {
    background: transparent;
    margin: 4px 2px 4px 0;
    width: 10px;
}

QScrollBar::handle:vertical {
    background: #c4d1dd;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: #9fb4c7;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}
"""
