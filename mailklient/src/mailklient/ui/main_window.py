"""Main application window."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mailklient.domain import Account, Attachment, Message
from mailklient.domain.errors import MailReadError
from mailklient.domain.mail_queries import EmailFilters
from mailklient.install_launcher import install_launcher
from mailklient.security import (
    OAuthClientConfigError,
    delete_oauth_tokens,
    delete_password,
    get_oauth_client_id,
    get_oauth_client_secret,
    save_oauth_tokens,
    save_password,
)
from mailklient.security.attachment_files import AttachmentWorkspace
from mailklient.services import (
    ComposeDraft,
    HeaderSyncResult,
    MailSendService,
    MailStore,
    MailSyncService,
    OAuthLoginService,
    SendResult,
)
from mailklient.services.account_settings import AccountSettingsService
from mailklient.services.attachments import AttachmentService
from mailklient.services.bridge_runtime import BridgeRuntimeService, BridgeStatus
from mailklient.services.drafts import DraftService
from mailklient.services.mail_read import MAX_PAGE_SIZE, MailReadService
from mailklient.services.tuta_setup import TutaSetupService
from mailklient.ui.account_dialog import AccountDialog
from mailklient.ui.attachment_controller import AttachmentController
from mailklient.ui.columns import (
    DEFAULT_COLUMN_ORDER,
    ColumnDragHandle,
    ColumnLayoutController,
)
from mailklient.ui.compose_dialog import ComposeDialog
from mailklient.ui.draft_dialog import DraftDialog
from mailklient.ui.message_viewer import MessageViewer
from mailklient.ui.oauth_settings_dialog import OAuthSettingsDialog
from mailklient.ui.presentation import (
    ElidedLabel,
    _apply_provider_defaults,
    _compact_message_item_text,
    _configure_message_item,
    _folder_display_name,
    _folder_labels,
    _friendly_error_message,
    _is_core_folder,
    _message_sender_label,
    _send_status_text,
    _short_message_date,
    _toolbar_button,
)
from mailklient.ui.theme import _MAIN_WINDOW_STYLESHEET
from mailklient.workers import MailSendWorker, MailSyncWorker
from mailklient.workers.bridge_worker import BridgeWorker
from mailklient.workers.task_runner import TaskRunner
from mailklient.workers.tuta_setup_worker import TutaSetupWorker

UNIFIED_INBOX_ROLE = "unified_inbox"
MESSAGE_TEXT_ROLE = Qt.ItemDataRole.UserRole + 1


class MainWindow(QMainWindow):
    """Main window backed by the local mail store."""

    def __init__(
        self,
        mail_store: MailStore,
        mail_sync_service: MailSyncService | None = None,
        mail_send_service: MailSendService | None = None,
        oauth_login_service: OAuthLoginService | None = None,
        bridge_runtime_service: BridgeRuntimeService | None = None,
        preferences: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._mail_store = mail_store
        self._mail_reader = MailReadService(mail_store.database_path)
        self._layout_controller = ColumnLayoutController(self)
        self._attachment_controller = AttachmentController(self)
        self._task_runner = TaskRunner(self)
        self._task_runner.busyChanged.connect(self._refresh_busy_actions)
        self._pending_attachment_preview: int | None = None
        self._attachment_service = AttachmentService(mail_store)
        self._attachment_files = AttachmentWorkspace()
        self._draft_service = DraftService(mail_store)
        self._sending_draft_id: int | None = None
        self._sync_queue: list[int] = []
        self._manual_sync_batch = False
        self._sync_queue_failures: list[str] = []
        self._background_sync = False
        self._mail_sync_service = mail_sync_service or MailSyncService(mail_store)
        self._mail_send_service = mail_send_service or MailSendService(mail_store)
        self._oauth_login_service = oauth_login_service or OAuthLoginService()
        self._bridge_runtime_service = bridge_runtime_service or BridgeRuntimeService()
        # Preserve saved layouts and preferences across the mcpMail rename.
        self._preferences = preferences or QSettings("Mailklient", "Mailklient")
        self._bridge_thread: QThread | None = None
        self._bridge_worker: BridgeWorker | None = None
        self._messages_by_id: dict[int, Message] = {}
        self._current_messages: list[Message] = []
        self._current_folder_is_unified = False
        self._attachments_by_id: dict[int, Attachment] = {}
        self._sync_in_progress = False
        self._sync_thread: QThread | None = None
        self._sync_worker: MailSyncWorker | None = None
        self._sync_account_id: int | None = None
        self._sync_account_label = ""
        self._tuta_setup_thread: QThread | None = None
        self._tuta_setup_worker: TutaSetupWorker | None = None
        self._send_in_progress = False
        self._send_thread: QThread | None = None
        self._send_worker: MailSendWorker | None = None
        self._column_panels: dict[str, QWidget] = {}
        self._column_handles: dict[str, ColumnDragHandle] = {}
        self._drag_original_order: tuple[str, ...] = DEFAULT_COLUMN_ORDER
        self._drag_source_column: str | None = None
        self._drop_preview: tuple[str, bool] | None = None
        self._message_toolbar_mode = "compact"
        self._refreshing_theme = False

        self.setWindowTitle("mcpMail")
        self.resize(1200, 750)
        self.setStyleSheet(_MAIN_WINDOW_STYLESHEET)

        self._editing_account = False
        self._build_menu()
        self.setCentralWidget(self._build_central_widget())
        self.folder_list.currentItemChanged.connect(self._load_selected_folder)
        self.account_list.currentItemChanged.connect(self._load_selected_account)
        self.message_list.currentItemChanged.connect(self._show_selected_message)
        self._load_accounts()
        self.bridge_status_label = QLabel("Tuta: not checked")
        self.bridge_status_label.setVisible(
            any(
                account.provider == "tuta"
                for account in self._mail_store.list_accounts()
            )
        )
        self.statusBar().addPermanentWidget(self.bridge_status_label)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(180000)
        self._sync_timer.timeout.connect(self._queue_auto_sync)
        if self.auto_sync_action.isChecked():
            self._sync_timer.start()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self._update_message_toolbar_layout()

    def closeEvent(self, event) -> None:
        if (
            self._task_runner.busy
            or self._manual_sync_batch
            or self._sync_queue
            or any(
                thread is not None and thread.isRunning()
                for thread in (
                    self._tuta_setup_thread,
                    self._sync_thread,
                    self._send_thread,
                    self._bridge_thread,
                )
            )
        ):
            self.sync_status_label.setText(
                "Wait for the current operation to finish before closing."
            )
            event.ignore()
            return
        self._attachment_files.close()
        self.attachment_dialog.close()
        if hasattr(self._mail_send_service, "close"):
            self._mail_send_service.close()
        super().closeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type()
            in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}
            and hasattr(self, "message_view")
            and not self._refreshing_theme
        ):
            # Qt resolves palette() rules when polishing the stylesheet.
            self._refreshing_theme = True
            try:
                self.setStyleSheet(_MAIN_WINDOW_STYLESHEET)
            finally:
                self._refreshing_theme = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            hasattr(self, "message_toolbar")
            and watched is self.message_toolbar
            and event.type() == QEvent.Type.Resize
        ):
            QTimer.singleShot(0, self._update_message_toolbar_layout)
        return super().eventFilter(watched, event)

    def _build_menu(self) -> None:
        account_menu = self.menuBar().addMenu("Account")
        self.account_menu = account_menu
        account_menu.setObjectName("account_menu")

        self.add_account_action = QAction("Add account", self)
        self.add_account_action.setObjectName("add_account_action")
        self.add_account_action.triggered.connect(self._open_add_account_dialog)

        self.edit_account_action = QAction("Edit account", self)
        self.edit_account_action.setObjectName("edit_account_action")
        self.edit_account_action.triggered.connect(self._open_edit_account_dialog)

        self.delete_account_action = QAction("Delete account", self)
        self.delete_account_action.setObjectName("delete_account_action")
        self.delete_account_action.triggered.connect(self._delete_selected_account)

        self.test_imap_action = QAction("Test IMAP", self)
        self.test_imap_action.setObjectName("test_imap_action")
        self.test_imap_action.triggered.connect(self._test_selected_imap_connection)

        self.test_smtp_action = QAction("Test SMTP", self)
        self.test_smtp_action.setObjectName("test_smtp_action")
        self.test_smtp_action.triggered.connect(self._test_selected_smtp_connection)

        self.oauth_login_action = QAction("Sign in with OAuth", self)
        self.oauth_login_action.setObjectName("oauth_login_action")
        self.oauth_login_action.triggered.connect(
            self._authorize_selected_oauth_account
        )

        self.sync_account_action = QAction("Sync account", self)
        self.sync_account_action.setObjectName("sync_account_action")
        self.sync_account_action.triggered.connect(self._sync_selected_account)

        account_menu.addAction(self.sync_account_action)
        self.cancel_sync_action = QAction(
            QIcon.fromTheme("process-stop"), "Cancel sync", self
        )
        self.cancel_sync_action.setObjectName("cancel_sync_action")
        self.cancel_sync_action.setEnabled(False)
        self.cancel_sync_action.triggered.connect(self._cancel_sync)
        account_menu.addAction(self.cancel_sync_action)
        self.fetch_older_action = QAction("Fetch older messages", self)
        self.fetch_older_action.triggered.connect(self._fetch_older_messages)
        account_menu.addAction(self.fetch_older_action)
        self.auto_sync_action = QAction("Automatically sync every 3 minutes", self)
        self.auto_sync_action.setCheckable(True)
        self.auto_sync_action.setChecked(
            self._preferences.value("sync/automatic", True, type=bool)
        )
        self.auto_sync_action.toggled.connect(self._toggle_auto_sync)
        account_menu.addAction(self.auto_sync_action)
        account_menu.addSeparator()
        account_menu.addAction(self.add_account_action)
        account_menu.addAction(self.edit_account_action)
        account_menu.addAction(self.delete_account_action)
        account_menu.addSeparator()
        account_menu.addAction(self.oauth_login_action)
        account_menu.addAction(self.test_imap_action)
        account_menu.addAction(self.test_smtp_action)
        account_menu.addSeparator()
        self.oauth_settings_action = QAction("OAuth app settings", self)
        self.oauth_settings_action.triggered.connect(self._open_oauth_settings)
        account_menu.addAction(self.oauth_settings_action)
        launcher_action = QAction("Add to application menu", self)
        launcher_action.triggered.connect(
            lambda: self._start_task(
                "Creating shortcut...",
                install_launcher,
                lambda _path: self.sync_status_label.setText(
                    "mcpMail added to the application menu."
                ),
            )
        )
        account_menu.addAction(launcher_action)
        self.clear_attachment_cache_action = QAction("Clear attachment cache...", self)
        self.clear_attachment_cache_action.triggered.connect(
            self._clear_attachment_cache
        )
        account_menu.addAction(self.clear_attachment_cache_action)

        bridge_menu = account_menu.addMenu("TutaBridge")
        self.bridge_start_action = QAction(
            QIcon.fromTheme("media-playback-start"), "Start TutaBridge", self
        )
        self.bridge_start_action.triggered.connect(
            lambda: self._run_bridge_check(start=True)
        )
        self.bridge_check_action = QAction(
            QIcon.fromTheme("view-refresh"), "Check status", self
        )
        self.bridge_check_action.triggered.connect(lambda: self._run_bridge_check())
        self.bridge_autostart_action = QAction("Start with mcpMail", self)
        self.bridge_autostart_action.setCheckable(True)
        self.bridge_autostart_action.setChecked(
            self._preferences.value("tuta/autostart", False, type=bool)
        )
        self.bridge_autostart_action.toggled.connect(
            lambda enabled: self._preferences.setValue("tuta/autostart", enabled)
        )
        bridge_menu.addAction(self.bridge_start_action)
        bridge_menu.addAction(self.bridge_check_action)
        bridge_menu.addSeparator()
        bridge_menu.addAction(self.bridge_autostart_action)

        message_menu = self.menuBar().addMenu("Message")
        drafts_action = QAction(QIcon.fromTheme("document-open"), "Drafts", self)
        drafts_action.triggered.connect(self._open_saved_drafts)
        message_menu.addAction(drafts_action)

        self.compose_action = QAction("New email", self)
        self.compose_action.setObjectName("compose_action")
        self.compose_action.triggered.connect(self._open_new_message_dialog)

        self.reply_action = QAction("Reply", self)
        self.reply_action.setObjectName("reply_action")
        self.reply_action.triggered.connect(self._reply_to_selected_message)

        self.forward_action = QAction("Forward", self)
        self.forward_action.setObjectName("forward_action")
        self.forward_action.triggered.connect(self._forward_selected_message)

        self.mark_read_action = QAction("Mark as read", self)
        self.mark_read_action.setObjectName("mark_read_action")
        self.mark_read_action.triggered.connect(self._mark_selected_message_read)

        self.mark_unread_action = QAction("Mark as unread", self)
        self.mark_unread_action.setObjectName("mark_unread_action")
        self.mark_unread_action.triggered.connect(self._mark_selected_message_unread)

        self.archive_action = QAction("Archive", self)
        self.archive_action.setObjectName("archive_action")
        self.archive_action.triggered.connect(self._archive_selected_message)

        self.trash_action = QAction("Move to trash", self)
        self.trash_action.setObjectName("trash_action")
        self.trash_action.triggered.connect(self._move_selected_message_to_trash)

        self.move_action = QAction("Move...", self)
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
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(self._build_column_drag_handle("folders"))

        title = QLabel("Folders")
        title.setObjectName("folder_panel_title")

        account_title = QLabel("Accounts")
        account_title.setObjectName("account_panel_title")

        self.account_list = QListWidget()
        self.account_list.setObjectName("account_list")
        self.account_list.itemDoubleClicked.connect(
            lambda _item: self._open_edit_account_dialog()
        )
        self.account_list.setMaximumHeight(180)
        self.account_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.account_list.setTextElideMode(Qt.TextElideMode.ElideRight)

        self.account_menu_button = QToolButton()
        self.account_menu_button.setObjectName("account_menu_button")
        self.account_menu_button.setToolTip("Account actions")
        self.account_menu_button.setAccessibleName("Account actions")
        self.account_menu_button.setIcon(
            QIcon.fromTheme(
                "application-menu",
                self.style().standardIcon(
                    QStyle.StandardPixmap.SP_FileDialogDetailedView
                ),
            )
        )
        self.account_menu_button.setFixedSize(32, 32)
        self.account_menu_button.setAutoRaise(True)
        self.account_menu_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.account_menu_button.setMenu(self.account_menu)

        self.sync_status_label = QLabel("")
        self.sync_status_label.setObjectName("sync_status_label")
        self.sync_status_label.setWordWrap(True)
        self.sync_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.sync_status_label.setMaximumHeight(48)

        self.folder_list = QListWidget()
        self.folder_list.setObjectName("folder_list")

        account_heading_layout = QHBoxLayout()
        account_heading_layout.addWidget(account_title, 1)
        account_heading_layout.addWidget(self.account_menu_button)
        layout.addLayout(account_heading_layout)
        layout.addWidget(self.account_list)
        layout.addWidget(title)
        layout.addWidget(self.folder_list, 1)
        layout.addWidget(self.sync_status_label)

        return panel

    def _build_message_list(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("message_list_panel")
        panel.setMinimumWidth(320)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(self._build_column_drag_handle("messages"))

        self.message_search_edit = QLineEdit()
        self.message_search_edit.setObjectName("message_search_edit")
        self.message_search_edit.setPlaceholderText("Search")
        self.message_search_edit.setClearButtonEnabled(True)
        self.message_search_edit.setMinimumHeight(30)
        self.message_search_edit.textChanged.connect(self._apply_message_filters)

        self.unread_filter_checkbox = QCheckBox("Unread")
        self.unread_filter_checkbox.setObjectName("unread_filter_checkbox")
        self.unread_filter_checkbox.toggled.connect(self._apply_message_filters)

        self.message_sort_combo = QComboBox()
        self.message_sort_combo.setObjectName("message_sort_combo")
        self.message_sort_combo.addItem("Newest first", "date_desc")
        self.message_sort_combo.addItem("Oldest first", "date_asc")
        self.message_sort_combo.addItem("Sender", "sender")
        self.message_sort_combo.addItem("Subject", "subject")
        self.message_sort_combo.currentIndexChanged.connect(self._apply_message_filters)

        filter_bar = QWidget()
        filter_bar.setObjectName("message_filter_bar")
        filter_bar.setFixedHeight(max(32, self.message_sort_combo.sizeHint().height()))
        filter_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filter_layout = QHBoxLayout(filter_bar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        filter_layout.addWidget(self.unread_filter_checkbox)
        filter_layout.addStretch()
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
        panel.setMinimumWidth(480)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(self._build_column_drag_handle("reader"))

        self.compose_button = _toolbar_button(
            "New",
            "mail-message-new",
            "New email",
            "compose_button",
        )
        self.compose_button.setObjectName("compose_button")
        self.compose_button.clicked.connect(self._open_new_message_dialog)

        self.reply_button = _toolbar_button(
            "Reply",
            "mail-reply-sender",
            "Reply to selected email",
            "reply_button",
        )
        self.reply_button.setObjectName("reply_button")
        self.reply_button.clicked.connect(self._reply_to_selected_message)

        self.forward_button = _toolbar_button(
            "Forward",
            "mail-forward",
            "Forward selected email",
            "forward_button",
        )
        self.forward_button.setObjectName("forward_button")
        self.forward_button.clicked.connect(self._forward_selected_message)

        self.archive_button = _toolbar_button(
            "Archive",
            "archive-insert",
            "Archive selected email",
            "archive_button",
        )
        self.archive_button.setObjectName("archive_button")
        self.archive_button.clicked.connect(self._archive_selected_message)

        self.trash_button = _toolbar_button(
            "Trash",
            "user-trash",
            "Move selected email to trash",
            "trash_button",
        )
        self.trash_button.setObjectName("trash_button")
        self.trash_button.clicked.connect(self._move_selected_message_to_trash)

        self.move_button = _toolbar_button(
            "Move",
            "folder-move",
            "Move selected email",
            "move_button",
        )
        self.move_button.setObjectName("move_button")
        self.move_button.clicked.connect(self._move_selected_message)

        self.mark_read_button = _toolbar_button(
            "Read",
            "mail-mark-read",
            "Mark selected email as read",
            "mark_read_button",
        )
        self.mark_read_button.setObjectName("mark_read_button")
        self.mark_read_button.clicked.connect(self._mark_selected_message_read)

        self.mark_unread_button = _toolbar_button(
            "Unread",
            "mail-mark-unread",
            "Mark selected email as unread",
            "mark_unread_button",
        )
        self.mark_unread_button.setObjectName("mark_unread_button")
        self.mark_unread_button.clicked.connect(self._mark_selected_message_unread)

        self.remote_content_checkbox = QCheckBox("External content")
        self.remote_content_checkbox.setObjectName("remote_content_checkbox")
        self.remote_content_checkbox.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.remote_content_checkbox.setToolTip(
            "Allow external images and media in selected email"
        )
        self.remote_content_checkbox.toggled.connect(self._toggle_remote_content)

        message_toolbar = QWidget()
        self.message_toolbar = message_toolbar
        self.message_toolbar.setObjectName("message_toolbar")
        self.message_toolbar.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.message_toolbar.installEventFilter(self)
        self.message_toolbar_layout = QVBoxLayout(self.message_toolbar)
        self.message_toolbar_layout.setContentsMargins(4, 4, 4, 4)
        self.message_toolbar_layout.setSpacing(4)

        self.message_action_layout = QHBoxLayout()
        self.message_action_layout.setObjectName("message_action_layout")
        self.message_action_layout.setContentsMargins(0, 0, 0, 0)
        self.message_action_layout.setSpacing(2)

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
        self.attachment_bar = QWidget()
        self.attachment_bar.setObjectName("attachment_bar")
        self.attachment_bar.setFixedHeight(42)
        attachment_bar_layout = QHBoxLayout(self.attachment_bar)
        attachment_bar_layout.setContentsMargins(4, 5, 4, 4)
        self.attachments_button = _toolbar_button(
            "Attachments", "mail-attachment", "Show attachments", "attachments_button"
        )
        self.attachments_button.clicked.connect(self._attachment_controller.show_dialog)
        attachment_bar_layout.addWidget(self.attachments_button)
        attachment_bar_layout.addStretch()

        self.attachment_dialog = QDialog(self)
        self.attachment_dialog.setWindowTitle("Attachments")
        self.attachment_dialog.resize(780, 520)
        self.attachment_dialog.setMinimumSize(540, 320)
        self.attachment_dialog.finished.connect(
            self._attachment_controller.preview_closed
        )
        dialog_layout = QVBoxLayout(self.attachment_dialog)
        attachment_splitter = QSplitter(Qt.Orientation.Horizontal)
        attachment_splitter.setChildrenCollapsible(False)
        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("attachment_list")
        self.attachment_list.setMinimumWidth(160)
        self.attachment_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.attachment_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.attachment_list.currentItemChanged.connect(
            lambda _current, _previous: self._preview_selected_attachment()
        )

        self.attachment_preview = QTextBrowser()
        self.attachment_preview.setObjectName("attachment_preview")
        self.attachment_preview.setReadOnly(True)
        self.attachment_preview.setOpenLinks(False)
        self.attachment_preview.setMinimumWidth(240)
        attachment_splitter.addWidget(self.attachment_list)
        attachment_splitter.addWidget(self.attachment_preview)
        attachment_splitter.setStretchFactor(1, 1)
        attachment_splitter.setSizes([230, 530])
        dialog_layout.addWidget(attachment_splitter, 1)

        self.open_attachment_button = QPushButton("Open")
        self.open_attachment_button.setObjectName("open_attachment_button")
        self.open_attachment_button.setIcon(
            QIcon.fromTheme(
                "document-open",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            )
        )
        self.open_attachment_button.clicked.connect(self._open_selected_attachment)

        self.save_attachment_button = QPushButton("Save as")
        self.save_attachment_button.setObjectName("save_attachment_button")
        self.save_attachment_button.setIcon(
            QIcon.fromTheme(
                "document-save-as",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
            )
        )
        self.save_attachment_button.clicked.connect(self._save_selected_attachment)

        self.save_all_attachments_button = _toolbar_button(
            "Save all",
            "document-save-all",
            "Save all attachments",
            "save_all_attachments_button",
        )
        self.save_all_attachments_button.clicked.connect(
            self._save_all_available_attachments
        )

        attachment_button_layout = QHBoxLayout()
        attachment_button_layout.setSpacing(6)
        attachment_button_layout.addWidget(self.open_attachment_button)
        attachment_button_layout.addWidget(self.save_attachment_button)
        attachment_button_layout.addStretch()
        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        close_buttons.rejected.connect(self.attachment_dialog.reject)
        attachment_button_layout.addWidget(close_buttons)
        dialog_layout.addLayout(attachment_button_layout)
        attachment_bar_layout.addWidget(self.save_all_attachments_button)

        layout.addWidget(self.message_toolbar)
        layout.addWidget(self.message_view, 1)
        layout.addWidget(self.attachment_bar)
        self._show_attachments([])

        return panel

    def _build_column_drag_handle(self, column_name: str) -> ColumnDragHandle:
        return self._layout_controller._build_column_drag_handle(column_name)

    def _update_message_toolbar_layout(self) -> None:
        return self._layout_controller._update_message_toolbar_layout()

    def _rebuild_message_toolbar(self, *, mode: str) -> None:
        return self._layout_controller._rebuild_message_toolbar(mode=mode)

    def _begin_column_drag(self, _column_name: str) -> None:
        return self._layout_controller._begin_column_drag(_column_name)

    def _finish_column_drag(
        self, column_name: str, global_position: QPoint, *, commit: bool
    ) -> None:
        return self._layout_controller._finish_column_drag(
            column_name, global_position, commit=commit
        )

    def _preview_column_drop(self, column_name: str, global_position: QPoint) -> None:
        return self._layout_controller._preview_column_drop(
            column_name, global_position
        )

    def _clear_column_drag_state(self) -> None:
        return self._layout_controller._clear_column_drag_state()

    def _clear_column_drop_preview(self) -> None:
        return self._layout_controller._clear_column_drop_preview()

    def _column_order(self) -> tuple[str, ...]:
        return self._layout_controller._column_order()

    def _column_at_global_position(self, global_position: QPoint) -> str | None:
        return self._layout_controller._column_at_global_position(global_position)

    def _apply_column_order(self, order: tuple[str, ...]) -> None:
        return self._layout_controller._apply_column_order(order)

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

        unified_item = QListWidgetItem("All inboxes")
        unified_item.setIcon(
            QIcon.fromTheme(
                "mail-folder-inbox",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon),
            )
        )
        unified_item.setData(Qt.ItemDataRole.UserRole, UNIFIED_INBOX_ROLE)
        self.account_list.addItem(unified_item)

        selected_row = 0
        for account in accounts:
            item = QListWidgetItem(account.display_name)
            item.setToolTip(account.email_address)
            item.setIcon(
                QIcon.fromTheme(
                    "user-identity",
                    self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, account.id)
            self.account_list.addItem(item)

            if select_account_id == account.id:
                selected_row = self.account_list.count() - 1

        row_height = max(34, self.account_list.sizeHintForRow(0))
        self.account_list.setFixedHeight(
            min(self.account_list.count(), 5) * row_height + 2
        )
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
            account_id = None

        for label, role, icon_name, fallback in (
            (
                "Inbox",
                "Innboks",
                "mail-folder-inbox",
                QStyle.StandardPixmap.SP_DirIcon,
            ),
            ("Trash", "Papirkurv", "user-trash", QStyle.StandardPixmap.SP_TrashIcon),
            (
                "Spam",
                "Søppelpost",
                "mail-mark-junk",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            ),
        ):
            item = QListWidgetItem(label)
            item.setIcon(
                QIcon.fromTheme(icon_name, self.style().standardIcon(fallback))
            )
            item.setData(Qt.ItemDataRole.UserRole, (account_id, role))
            self.folder_list.addItem(item)

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

        account_id, role = current.data(Qt.ItemDataRole.UserRole)
        self._current_folder_is_unified = account_id is None
        self._apply_message_filters()

    def _apply_message_filters(self, *_args: object) -> None:
        self.message_list.clear()
        self._messages_by_id.clear()

        current = self.folder_list.currentItem()
        self._current_messages = []
        if current is None:
            return
        account_id, role = current.data(Qt.ItemDataRole.UserRole)
        filters = EmailFilters(
            account_id=account_id, mailbox=role,
            is_read=False if self.unread_filter_checkbox.isChecked() else None,
        )
        messages = []
        offset = 0
        try:
            # Keep the existing full-list UI while the shared API stays paginated.
            while True:
                page = self._mail_reader.search_emails(
                    self.message_search_edit.text(), filters, limit=MAX_PAGE_SIZE,
                    offset=offset, sort_order=self.message_sort_combo.currentData(),
                )
                messages.extend(page.items)
                if page.next_offset is None:
                    break
                offset = page.next_offset
        except MailReadError as error:
            self.sync_status_label.setText(str(error))
            return
        self._current_messages = messages

        for message in messages:
            item = QListWidgetItem()
            item.setData(MESSAGE_TEXT_ROLE, self._message_item_text(message))
            item.setData(Qt.ItemDataRole.UserRole, message.id)
            item.setSizeHint(
                QSize(280, max(102, self.fontMetrics().lineSpacing() * 4 + 30))
            )
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
        sender_label = ElidedLabel(sender)
        sender_label.setObjectName("message_item_sender")

        date_label = QLabel(_short_message_date(message))
        date_label.setObjectName("message_item_date")
        date_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        meta_layout.addWidget(sender_label, 1)
        meta_layout.addWidget(date_label)

        subject_label = ElidedLabel(message.subject or "(no subject)")
        subject_label.setObjectName("message_item_subject")

        preview_label = ElidedLabel(message.body_preview.strip())
        preview_label.setObjectName("message_item_preview")

        account = self._mail_store.get_account(message.account_id)
        if (
            self._current_folder_is_unified
            and account is not None
            and account.email_address.casefold() != sender.casefold()
        ):
            account_label = ElidedLabel(account.email_address)
            account_label.setObjectName("message_item_account")
            layout.addWidget(account_label)

        layout.addLayout(meta_layout)
        layout.addWidget(subject_label)
        if message.body_preview.strip():
            layout.addWidget(preview_label)
        layout.addStretch()

        return card

    def _message_item_text(self, message: Message) -> str:
        subject = message.subject or "(no subject)"
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
        try:
            details = self._mail_reader.get_email(message_id)
        except MailReadError as error:
            self.message_view.set_empty()
            self._show_attachments([])
            self._set_message_actions_enabled(False)
            self.sync_status_label.setText(str(error))
            return
        message = details.message
        account = self._mail_store.get_account(message.account_id)
        account_label = account.email_address if account is not None else ""
        attachments = list(details.attachments)
        self.message_view.show_message(message, account_label, attachments)
        self._show_attachments(attachments)
        self._set_message_actions_enabled(True)

    def _open_oauth_settings(self) -> None:
        if self._task_runner.busy or self._sync_in_progress or self._send_in_progress:
            return
        OAuthSettingsDialog(self).exec()

    def _open_add_account_dialog(self) -> None:
        if self._sync_in_progress or self._task_runner.busy:
            self.sync_status_label.setText(
                "Wait for sync or account setup to finish."
            )
            return
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
            provider=data.provider,
            local_certificate=data.local_certificate,
        )

    def _open_edit_account_dialog(self) -> None:
        if (
            self._sync_in_progress
            or self._send_in_progress
            or self._editing_account
            or self._task_runner.busy
        ):
            self.sync_status_label.setText(
                "Wait for sync or sending to finish."
            )
            return
        account_id = self._selected_account_id()
        account = (
            self._mail_store.get_account(account_id) if account_id is not None else None
        )
        if account is None:
            return
        self._editing_account = True
        try:
            dialog = AccountDialog(self, account=account)
            while dialog.exec() == AccountDialog.DialogCode.Accepted:
                data = dialog.account_data()
                updated = replace(
                    account,
                    display_name=data.display_name,
                    username=data.username,
                    imap_host=data.imap_host,
                    imap_port=data.imap_port,
                    imap_security=data.imap_security,
                    smtp_host=data.smtp_host,
                    smtp_port=data.smtp_port,
                    smtp_security=data.smtp_security,
                    local_certificate=data.local_certificate,
                )
                try:
                    AccountSettingsService(self._mail_store).save(
                        updated, data.password
                    )
                except Exception:
                    QMessageBox.warning(
                        self,
                        "Could not save account",
                        "Check the settings and make sure the system keyring "
                        "is available.",
                    )
                    continue
                self._load_accounts(select_account_id=account.id)
                self.sync_status_label.setText(
                    "Account updated. Sync to refresh folders and messages."
                )
                break
        finally:
            self._editing_account = False

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
        provider: str = "imap",
        local_certificate: str | None = None,
    ) -> bool:
        if provider == "tuta":
            if self._sync_in_progress:
                return False
            self._start_tuta_setup(
                display_name, email_address, password, local_certificate
            )
            return True
        client_id = None
        client_secret = None
        if auth_method == "oauth2":
            if oauth_provider is None:
                QMessageBox.warning(
                    self,
                    "OAuth provider missing",
                    "Select Gmail or Outlook for the OAuth2 account.",
                )
                return False
            try:
                client_id = get_oauth_client_id(oauth_provider)
                client_secret = get_oauth_client_secret(oauth_provider)
            except (KeyError, OAuthClientConfigError) as error:
                QMessageBox.warning(self, "OAuth configuration missing", str(error))
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
                provider=provider,
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
                "Could not add account",
                "An account with this email address already exists.",
            )
            return False
        except Exception as error:
            if "account" in locals():
                self._mail_store.delete_account(account.id)
            title = (
                "OAuth sign-in failed"
                if auth_method == "oauth2"
                else "Could not save password"
            )
            QMessageBox.warning(self, title, str(error))
            self._load_accounts()
            return False

        self._load_accounts(select_account_id=account.id)
        return True

    def _start_tuta_setup(
        self,
        display_name: str,
        email_address: str,
        password: str | None,
        local_certificate: str | None,
    ) -> None:
        self._set_sync_in_progress(True)
        self.sync_status_label.setText("Testing TutaBridge IMAP and SMTP...")
        thread = QThread(self)
        worker = TutaSetupWorker(
            TutaSetupService(self._mail_store),
            self._mail_sync_service,
            display_name,
            email_address,
            password,
            Path(local_certificate) if local_certificate else None,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.account_created.connect(self._handle_tuta_account_created)
        worker.finished.connect(self._handle_sync_finished)
        worker.failed.connect(self._handle_tuta_setup_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_tuta_setup)
        self._tuta_setup_thread = thread
        self._tuta_setup_worker = worker
        thread.start()

    def _handle_tuta_account_created(self, account: Account) -> None:
        self._load_accounts(select_account_id=account.id)
        self.sync_status_label.setText(
            "Tuta account saved. Fetching up to five inbox messages..."
        )

    def _handle_tuta_setup_failed(self, message: str) -> None:
        self.sync_status_label.setText("Tuta setup or initial sync failed")
        QMessageBox.warning(self, "TutaBridge", message)

    def _cleanup_tuta_setup(self) -> None:
        self._tuta_setup_thread = None
        self._tuta_setup_worker = None
        self._set_sync_in_progress(False)

    def _delete_selected_account(self) -> bool:
        if self._task_runner.busy or self._sync_in_progress or self._send_in_progress:
            return False
        current = self.account_list.currentItem()
        if current is None:
            return False

        account_id = current.data(Qt.ItemDataRole.UserRole)
        account_name = current.text()
        answer = QMessageBox.question(
            self,
            "Delete account",
            f"Delete local account '{account_name}'?",
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
                "OAuth unavailable",
                "The selected account does not use OAuth2.",
            )
            return False

        try:
            client_id = get_oauth_client_id(account.oauth_provider)
            client_secret = get_oauth_client_secret(account.oauth_provider)
            return self._authorize_oauth_provider(
                account.email_address,
                account.oauth_provider,
                client_id,
                client_secret,
            )
        except Exception as error:
            self._show_sync_error("OAuth sign-in failed", error)
            return False

    def _authorize_oauth_provider(
        self,
        email_address: str,
        provider: str,
        client_id: str,
        client_secret: str | None,
    ) -> bool:
        def authorize() -> bool:
            tokens = self._oauth_login_service.authorize(
                provider,
                client_id,
                client_secret=client_secret,
            )
            save_oauth_tokens(email_address, tokens)
            return True

        return self._start_task(
            "Waiting for OAuth sign-in...",
            authorize,
            lambda result: self._complete_task(result, "OAuth sign-in successful"),
        )

    def _test_selected_imap_connection(self) -> bool:
        account_id = self._selected_account_id()
        if account_id is None:
            return False
        return self._start_task(
            "Testing IMAP...",
            lambda: self._mail_sync_service.test_imap_connection(account_id),
            lambda result: self._complete_task(result, "IMAP connection successful"),
        )

    def _test_selected_smtp_connection(self) -> bool:
        account_id = self._selected_account_id()
        if account_id is None:
            return False
        return self._start_task(
            "Testing SMTP...",
            lambda: self._mail_sync_service.test_smtp_connection(account_id),
            lambda result: self._complete_task(result, "SMTP connection successful"),
        )

    def _sync_selected_account(self) -> bool:
        if (
            self._sync_in_progress
            or self._sync_queue
            or self._manual_sync_batch
            or self._send_in_progress
            or self._task_runner.busy
            or self._editing_account
        ):
            self.sync_status_label.setText("Wait for the current operation to finish.")
            return False
        account_id = self._selected_account_id()
        if account_id is not None:
            self._sync_queue_failures.clear()
            return self._start_sync_worker(account_id)
        current = self.account_list.currentItem()
        if (
            current is None
            or current.data(Qt.ItemDataRole.UserRole) != UNIFIED_INBOX_ROLE
        ):
            return False
        self._sync_queue = self._syncable_account_ids()
        if not self._sync_queue:
            self.sync_status_label.setText(
                "No accounts with IMAP settings to sync."
            )
            return False
        self._manual_sync_batch = True
        self._sync_queue_failures.clear()
        self._next_queued_sync()
        return self._sync_in_progress

    def _open_new_message_dialog(self) -> bool:
        accounts = self._mail_store.list_accounts()
        if not accounts:
            QMessageBox.warning(self, "Cannot send", "Add an account first.")
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

        if self._mail_store.list_attachments(message_id):
            return self._start_task(
                "Preparing forwarded message...",
                lambda: self._mail_send_service.create_forward_draft(message_id),
                lambda draft: (
                    self._open_compose_dialog(draft)
                    if isinstance(draft, ComposeDraft)
                    else None
                ),
            )
        draft = self._mail_send_service.create_forward_draft(message_id)
        if draft is None:
            return False
        return self._open_compose_dialog(draft)

    def _open_saved_drafts(self) -> None:
        if self._send_in_progress:
            return
        dialog = DraftDialog(self._draft_service, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.selected():
            item = dialog.selected()
            self._open_compose_dialog(
                item.draft, item.id, item.state in {"sending", "uncertain"}
            )

    def _open_compose_dialog(
        self, draft: ComposeDraft, draft_id: int | None = None, uncertain: bool = False
    ) -> bool:
        accounts = self._mail_store.list_accounts()
        if not accounts:
            QMessageBox.warning(self, "Cannot send", "Add an account first.")
            return False

        dialog = ComposeDialog(
            accounts,
            draft,
            self,
            draft_service=self._draft_service,
            draft_id=draft_id,
            uncertain=uncertain,
        )
        if dialog.exec() != ComposeDialog.DialogCode.Accepted:
            return False

        return self._send_draft(dialog.draft(), dialog.draft_id)

    def _send_draft(self, draft: ComposeDraft, draft_id: int | None = None) -> bool:
        if self._send_in_progress or self._task_runner.busy:
            self.sync_status_label.setText("A message is already being sent.")
            return False

        try:
            self._sending_draft_id = self._draft_service.save(draft, draft_id)
            draft = self._draft_service.get(self._sending_draft_id).draft
            self._draft_service.set_state(self._sending_draft_id, "sending")
        except Exception:
            QMessageBox.warning(
                self,
                "Sending cancelled",
                "Could not save the draft safely. No email was sent.",
            )
            return False
        self._start_send_worker(draft)
        return True

    def _start_send_worker(self, draft: ComposeDraft) -> None:
        self._set_send_in_progress(True)
        self.sync_status_label.setText("Sending...")

        thread = QThread(self)
        worker = MailSendWorker(
            self._mail_send_service, draft, self._draft_service, self._sending_draft_id
        )
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

    def initialize_bridge(self) -> None:
        """Check or start the bridge once at application startup, if used."""
        if any(
            account.provider == "tuta" for account in self._mail_store.list_accounts()
        ):
            self._run_bridge_check(start=self.bridge_autostart_action.isChecked())

    def _run_bridge_check(self, *, start: bool = False) -> None:
        if self._bridge_thread is not None:
            return
        account_id = self._selected_account_id()
        account = self._mail_store.get_account(account_id) if account_id else None
        if account is None or account.provider != "tuta":
            account = next(
                (a for a in self._mail_store.list_accounts() if a.provider == "tuta"),
                None,
            )
        certificate = (
            Path(account.local_certificate)
            if account and account.local_certificate
            else None
        )
        thread = QThread(self)
        worker = BridgeWorker(self._bridge_runtime_service, certificate, start=start)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_bridge_status)
        worker.failed.connect(self._handle_bridge_failure)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_bridge_worker)
        self._bridge_thread = thread
        self._bridge_worker = worker
        self.bridge_start_action.setEnabled(False)
        self.bridge_check_action.setEnabled(False)
        self.bridge_status_label.show()
        self.bridge_status_label.setText(
            "Tuta: starting..." if start else "Tuta: checking..."
        )
        thread.start()

    def _handle_bridge_status(self, status: BridgeStatus) -> None:
        self.bridge_status_label.setText(
            "Tuta: TLS available" if status.ready else "Tuta: not ready"
        )
        self.bridge_status_label.setToolTip(status.message)
        self.statusBar().showMessage(status.message, 15000)

    def _handle_bridge_failure(self, message: str) -> None:
        self.bridge_status_label.setText("Tuta: needs attention")
        self.bridge_status_label.setToolTip(message)
        self.statusBar().showMessage(message, 15000)

    def _cleanup_bridge_worker(self) -> None:
        self._bridge_thread = None
        self._bridge_worker = None
        self.bridge_start_action.setEnabled(True)
        self.bridge_check_action.setEnabled(True)

    def _handle_send_finished(self, result: object) -> None:
        if isinstance(result, SendResult):
            if result.sent:
                self.sync_status_label.setText(_send_status_text(result))
                self._reload_current_folder()
                return
        elif result:
            self.sync_status_label.setText("Email sent.")
            self._reload_current_folder()
            return

        self.sync_status_label.setText("Sending failed")
        QMessageBox.warning(
            self,
            "Sending failed",
            "Account, SMTP or sign-in details are missing.",
        )

    def _handle_send_failed(self, error_message: str) -> None:
        self.sync_status_label.setText(
            "Sending could not be confirmed. The draft has been kept."
        )
        QMessageBox.warning(
            self,
            "Sending failed",
            _friendly_error_message(error_message)
            + "\n\nThe draft is available under Message > Drafts. "
            "Check Sent before trying again.",
        )

    def _cleanup_send_worker(self) -> None:
        self._send_thread = None
        self._send_worker = None
        self._sending_draft_id = None
        self._set_send_in_progress(False)
        if self.auto_sync_action.isChecked():
            QTimer.singleShot(0, self._queue_auto_sync)

    def _reload_current_folder(self) -> None:
        selected_id = self._selected_message_id()
        current = self.folder_list.currentItem()
        if current is not None:
            self._load_selected_folder(current, None)
            for row in range(self.message_list.count()):
                if (
                    self.message_list.item(row).data(Qt.ItemDataRole.UserRole)
                    == selected_id
                ):
                    self.message_list.setCurrentRow(row)
                    break

    def _toggle_remote_content(self, allowed: bool) -> None:
        self.message_view.set_remote_content_allowed(allowed)
        self._rerender_selected_message()

    def _rerender_selected_message(self) -> None:
        self._show_selected_message(self.message_list.currentItem(), None)

    def _show_attachments(self, attachments: list[Attachment]) -> None:
        return self._attachment_controller._show_attachments(attachments)

    def _selected_attachment(self) -> Attachment | None:
        return self._attachment_controller._selected_attachment()

    def _update_attachment_actions(self) -> None:
        return self._attachment_controller._update_attachment_actions()

    def _resume_attachment_preview(self) -> None:
        return self._attachment_controller._resume_attachment_preview()

    def _preview_selected_attachment(self) -> None:
        return self._attachment_controller._preview_selected_attachment()

    def _attachment_loaded(self, attachment: Attachment, content: object) -> None:
        return self._attachment_controller._attachment_loaded(attachment, content)

    def _clear_attachment_cache(self) -> None:
        return self._attachment_controller._clear_attachment_cache()

    def _cache_cleared(self) -> None:
        return self._attachment_controller._cache_cleared()

    def _save_selected_attachment(self) -> bool:
        return self._attachment_controller._save_selected_attachment()

    def _save_all_available_attachments(self) -> bool:
        return self._attachment_controller._save_all_available_attachments()

    def _open_selected_attachment(self) -> bool:
        return self._attachment_controller._open_selected_attachment()

    def _mark_selected_message_read(self) -> bool:
        return self._mark_selected_message(is_read=True)

    def _mark_selected_message_unread(self) -> bool:
        return self._mark_selected_message(is_read=False)

    def _mark_selected_message(self, *, is_read: bool) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False
        label = "Message marked as read." if is_read else "Message marked as unread."
        return self._start_task(
            "Updating message...",
            lambda: self._mail_sync_service.mark_message_read(message_id, is_read),
            lambda result: self._complete_task(result, label, reload=True),
        )

    def _move_selected_message_to_trash(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False
        return self._start_task(
            "Moving to trash...",
            lambda: self._mail_sync_service.move_message_to_trash(message_id),
            lambda result: self._complete_task(
                result, "Message moved to trash.", reload=True
            ),
        )

    def _archive_selected_message(self) -> bool:
        message_id = self._selected_message_id()
        if message_id is None:
            return False
        return self._start_task(
            "Archiving message...",
            lambda: self._mail_sync_service.archive_message(message_id),
            lambda result: self._complete_task(
                result, "Message archived.", reload=True
            ),
        )

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

        labels = _folder_labels(folders)
        selected_label, accepted = QInputDialog.getItem(
            self,
            "Move message",
            "Folder",
            labels,
            0,
            False,
        )
        if not accepted:
            return False

        destination = folders[labels.index(selected_label)]
        return self._start_task(
            "Moving message...",
            lambda: self._mail_sync_service.move_message_to_folder(
                message_id, destination.id
            ),
            lambda result: self._complete_task(
                result,
                f"Message moved to {_folder_display_name(destination.name)}.",
                reload=True,
            ),
        )

    def _start_task(
        self,
        label: str,
        operation: Callable[[], object],
        success: Callable[[object], None],
    ) -> bool:
        if (
            self._task_runner.busy
            or self._sync_in_progress
            or self._send_in_progress
            or self._manual_sync_batch
        ):
            self.sync_status_label.setText("Wait for the current operation to finish.")
            return False
        self.sync_status_label.setText(label)
        return self._task_runner.start(operation, success, self._handle_task_error)

    def _handle_task_error(self, error: str) -> None:
        self.sync_status_label.setText("The operation failed.")
        attachment = self._selected_attachment()
        if attachment is not None and not attachment.has_content:
            self.attachment_preview.setHtml("<p>Could not fetch the attachment.</p>")
        QMessageBox.warning(
            self, "Operation failed", _friendly_error_message(error)
        )

    def _complete_task(
        self, result: object, label: str, *, reload: bool = False
    ) -> None:
        self.sync_status_label.setText(
            label if result else "The operation could not be completed."
        )
        if reload:
            self._reload_current_folder()

    def _refresh_busy_actions(self, *_args: object) -> None:
        self._set_account_actions_enabled(self._selected_account_id() is not None)
        self._set_message_actions_enabled(self._selected_message_id() is not None)
        self._set_compose_actions_enabled(bool(self._mail_store.list_accounts()))
        self._update_attachment_actions()
        if self._sync_queue and not self._task_runner.busy:
            QTimer.singleShot(0, self._next_queued_sync)

    def _toggle_auto_sync(self, enabled: bool) -> None:
        self._preferences.setValue("sync/automatic", enabled)
        if enabled:
            self._sync_timer.start()
        else:
            self._sync_timer.stop()
            if not self._manual_sync_batch:
                self._sync_queue.clear()

    def _syncable_account_ids(self) -> list[int]:
        return [
            account.id
            for account in self._mail_store.list_accounts()
            if account.imap_host and account.provider != "tuta"
        ]

    def _queue_auto_sync(self) -> None:
        if (
            self._sync_in_progress
            or self._send_in_progress
            or self._sync_queue
            or self._manual_sync_batch
            or self._editing_account
            or self._task_runner.busy
        ):
            return
        self._sync_queue = self._syncable_account_ids()
        self._sync_queue_failures.clear()
        self._next_queued_sync()

    def _next_queued_sync(self) -> None:
        if self._sync_in_progress:
            return
        if self._send_in_progress or self._editing_account or self._task_runner.busy:
            if not self._manual_sync_batch:
                self._sync_queue.clear()
            return
        if not self._manual_sync_batch and not self.auto_sync_action.isChecked():
            self._sync_queue.clear()
            return
        while self._sync_queue:
            account_id = self._sync_queue.pop(0)
            if self._mail_store.get_account(account_id) is not None:
                self._start_sync_worker(
                    account_id, background=not self._manual_sync_batch
                )
                return
        if self._manual_sync_batch:
            self._manual_sync_batch = False
            failed_accounts = len(self._sync_queue_failures)
            self.sync_status_label.setText(
                "Sync completed with errors for "
                f"{failed_accounts} account{'s' if failed_accounts != 1 else ''}."
                if self._sync_queue_failures
                else "All configured accounts have been synced."
            )
            self.sync_status_label.setToolTip("\n".join(self._sync_queue_failures))
            self._refresh_busy_actions()
            if self._sync_queue_failures:
                QMessageBox.warning(
                    self, "Sync completed with errors", "\n".join(self._sync_queue_failures)
                )

    def _fetch_older_messages(self) -> None:
        account_id = self._selected_account_id()
        if account_id is not None and not self._sync_in_progress:
            self._start_sync_worker(account_id, fetch_older=True)

    def _start_sync_worker(
        self, account_id: int, *, fetch_older: bool = False, background: bool = False
    ) -> bool:
        if (
            self._sync_in_progress
            or self._task_runner.busy
            or self._send_in_progress
            or self._editing_account
        ):
            return False
        account = self._mail_store.get_account(account_id)
        if account is None:
            return False
        if account is not None and account.provider == "tuta":
            worker = MailSyncWorker(
                self._mail_sync_service,
                account_id,
                limit_per_folder=5,
                message_folder_names=("INBOX",),
                parent=self,
            )
        else:
            worker = MailSyncWorker(
                self._mail_sync_service,
                account_id,
                fetch_older=fetch_older,
                parent=self,
            )
        worker.synced.connect(self._handle_sync_finished)
        worker.failed.connect(self._handle_sync_failed)
        worker.cancelled.connect(self._handle_sync_cancelled)
        worker.progress.connect(self._handle_sync_progress)
        worker.finished.connect(self._cleanup_sync_worker)

        self._sync_thread = worker
        self._sync_worker = worker
        self._sync_account_id = account_id
        self._sync_account_label = account.display_name
        self._background_sync = background
        self._set_sync_in_progress(True)
        self.sync_status_label.setText(f"Syncing {account.display_name}...")
        self.sync_status_label.setToolTip("")
        worker.start()
        return True

    def _handle_sync_progress(self, account_id: int, message: str) -> None:
        if (
            self._sync_worker is None
            or self._sync_worker.isInterruptionRequested()
            or account_id != self._sync_account_id
        ):
            return
        status = f"{self._sync_account_label}: {message}"
        self.sync_status_label.setText(status)
        self.sync_status_label.setToolTip(status)

    def _cancel_sync(self) -> None:
        if self._sync_worker is None:
            return
        self._sync_queue.clear()
        self._manual_sync_batch = False
        self._sync_worker.requestInterruption()
        self.cancel_sync_action.setEnabled(False)
        self.sync_status_label.setText("Cancelling sync...")

    def _handle_sync_cancelled(self, _account_id: int) -> None:
        self._reload_current_folder()
        self.sync_status_label.setText(
            "Sync cancelled. Previously saved messages are kept."
        )
        self.sync_status_label.setToolTip("")

    def _handle_sync_finished(self, account_id: int, result: object) -> None:
        if not isinstance(result, HeaderSyncResult):
            self.sync_status_label.setText("Sync complete.")
            return
        if self._background_sync or self._manual_sync_batch:
            self._reload_current_folder()
        else:
            self._load_accounts(select_account_id=account_id)
        folder_label = "folder" if result.folders_seen == 1 else "folders"
        message_label = "message" if result.messages_seen == 1 else "messages"
        self.sync_status_label.setText(
            f"Synced {result.folders_seen} {folder_label} and "
            f"{result.messages_seen} {message_label}."
        )
        if result.messages_failed:
            failure_text = (
                f"{result.messages_failed} "
                f"message{'s' if result.messages_failed != 1 else ''} "
                "could not be parsed."
            )
            if self._manual_sync_batch:
                account = self._mail_store.get_account(account_id)
                label = account.display_name if account is not None else "Account"
                self._sync_queue_failures.append(f"{label}: {failure_text}")
            self.sync_status_label.setText(
                self.sync_status_label.text() + f" {failure_text}"
            )

    def _handle_sync_failed(self, account_id: int, error_message: str) -> None:
        self.sync_status_label.setText("Sync failed")
        detail = _friendly_error_message(error_message)
        account = self._mail_store.get_account(account_id)
        account_label = account.display_name if account is not None else "Account"
        self._sync_queue_failures.append(f"{account_label}: {detail}")
        self.sync_status_label.setToolTip(detail)
        if self._background_sync or self._manual_sync_batch:
            return
        QMessageBox.warning(
            self,
            "Sync failed",
            f"{account_label}: {detail}",
        )

    def _cleanup_sync_worker(self) -> None:
        thread = self._sync_thread
        if thread is not None:
            # Retain the wrapper until native thread-local cleanup has finished.
            thread.wait()
            thread.deleteLater()
        self._sync_thread = None
        self._sync_worker = None
        self._sync_account_id = None
        self._sync_account_label = ""
        self._set_sync_in_progress(False)
        self._background_sync = False
        QTimer.singleShot(0, self._next_queued_sync)

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
        busy = (
            self._sync_in_progress
            or self._send_in_progress
            or self._task_runner.busy
            or self._manual_sync_batch
        )
        current = self.account_list.currentItem()
        unified = (
            current is not None
            and current.data(Qt.ItemDataRole.UserRole) == UNIFIED_INBOX_ROLE
        )
        self.sync_account_action.setText(
            "Syncing..."
            if self._sync_in_progress
            else "Sync all accounts"
            if unified
            else "Sync account"
        )
        self.sync_account_action.setEnabled(
            not busy and (enabled or (unified and bool(self._syncable_account_ids())))
        )
        self.cancel_sync_action.setEnabled(
            self._sync_worker is not None
            and self._sync_in_progress
            and not self._sync_worker.isInterruptionRequested()
        )
        self.add_account_action.setEnabled(not busy)
        self.oauth_settings_action.setEnabled(not busy)
        self.clear_attachment_cache_action.setEnabled(not busy)
        for action in (
            self.edit_account_action,
            self.delete_account_action,
            self.test_imap_action,
            self.test_smtp_action,
            self.oauth_login_action,
            self.fetch_older_action,
        ):
            action.setEnabled(enabled and not busy)

    def _set_message_actions_enabled(self, enabled: bool) -> None:
        enabled = (
            enabled
            and not self._sync_in_progress
            and not self._task_runner.busy
            and not self._manual_sync_batch
        )
        effective_enabled = enabled and not self._send_in_progress
        message_id = self._selected_message_id()
        message = (
            self._messages_by_id.get(message_id) if message_id is not None else None
        )
        account = self._mail_store.get_account(message.account_id) if message else None
        can_archive = effective_enabled and (
            account is None or account.provider != "tuta"
        )
        self.reply_button.setEnabled(effective_enabled)
        self.forward_button.setEnabled(effective_enabled)
        self.reply_action.setEnabled(effective_enabled)
        self.forward_action.setEnabled(effective_enabled)
        self.mark_read_button.setEnabled(enabled)
        self.mark_unread_button.setEnabled(enabled)
        self.mark_read_action.setEnabled(enabled)
        self.mark_unread_action.setEnabled(enabled)
        self.archive_button.setEnabled(bool(can_archive))
        self.trash_button.setEnabled(effective_enabled)
        self.move_button.setEnabled(effective_enabled)
        self.archive_action.setEnabled(bool(can_archive))
        self.trash_action.setEnabled(effective_enabled)
        self.move_action.setEnabled(effective_enabled)

    def _set_compose_actions_enabled(self, enabled: bool) -> None:
        effective_enabled = (
            enabled
            and not self._send_in_progress
            and not self._task_runner.busy
            and not self._manual_sync_batch
        )
        self.compose_button.setEnabled(effective_enabled)
        self.compose_action.setEnabled(effective_enabled)

    def _set_send_in_progress(self, in_progress: bool) -> None:
        self._send_in_progress = in_progress
        self._set_account_actions_enabled(self._selected_account_id() is not None)
        self.compose_button.setText("Sending..." if in_progress else "New")
        self._set_compose_actions_enabled(bool(self._mail_store.list_accounts()))
        self._set_message_actions_enabled(self._selected_message_id() is not None)
        self._update_attachment_actions()

    def _set_sync_in_progress(self, in_progress: bool) -> None:
        self._sync_in_progress = in_progress
        self._set_account_actions_enabled(self._selected_account_id() is not None)
        self._set_message_actions_enabled(self._selected_message_id() is not None)
        self._update_attachment_actions()

    def _show_sync_error(self, title: str, error: Exception) -> None:
        self.sync_status_label.setText(title)
        QMessageBox.warning(self, title, _friendly_error_message(error))
