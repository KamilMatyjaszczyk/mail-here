from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

from mailklient.services import MailStore
from mailklient.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "cache.sqlite3")
    account = store.add_account_with_default_folders("Test", "test@example.com")
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    preferences.setValue("sync/automatic", False)
    result = MainWindow(store, preferences=preferences)
    result._load_accounts(select_account_id=account.id)
    yield result
    result.account_menu.close()
    result.close()
    app.processEvents()


def test_account_functions_live_in_one_shared_popup(window):
    button = window.account_menu_button
    assert button.menu() is window.account_menu
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    actions = window.account_menu.actions()
    for action in (
        window.add_account_action,
        window.edit_account_action,
        window.delete_account_action,
        window.sync_account_action,
        window.fetch_older_action,
        window.auto_sync_action,
        window.oauth_login_action,
        window.test_imap_action,
        window.test_smtp_action,
    ):
        assert action in actions
    assert window.account_menu.menuAction() in window.menuBar().actions()
    for name in (
        "account_tools_panel",
        "add_account_button",
        "delete_account_button",
        "test_imap_button",
        "test_smtp_button",
        "oauth_login_button",
        "sync_account_button",
    ):
        assert window.findChild(QWidget, name) is None


def test_popup_opens_on_click_and_has_fixed_dimensions(window):
    window.show()
    QApplication.processEvents()
    shown = []
    window.account_menu.aboutToShow.connect(lambda: shown.append(True))
    QTimer.singleShot(50, window.account_menu.close)
    QTest.mouseClick(window.account_menu_button, Qt.MouseButton.LeftButton)
    assert shown == [True]
    assert window.account_menu_button.width() == 32
    assert window.account_menu_button.height() == 32


def test_actions_follow_account_selection_and_worker_state(window):
    scoped_actions = (
        window.edit_account_action,
        window.delete_account_action,
        window.sync_account_action,
        window.fetch_older_action,
        window.test_imap_action,
        window.test_smtp_action,
        window.oauth_login_action,
    )
    assert all(action.isEnabled() for action in scoped_actions)
    window.account_list.setCurrentRow(0)
    assert not any(action.isEnabled() for action in scoped_actions)
    assert window.account_menu_button.isEnabled()
    assert window.add_account_action.isEnabled()
    window.account_list.setCurrentRow(1)
    for set_busy in (window._set_sync_in_progress, window._set_send_in_progress):
        set_busy(True)
        assert not any(action.isEnabled() for action in scoped_actions)
        assert not window.add_account_action.isEnabled()
        assert window.account_menu_button.isEnabled()
        set_busy(False)
        assert all(action.isEnabled() for action in scoped_actions)
        assert window.add_account_action.isEnabled()


def test_popup_action_uses_existing_imap_handler(window):
    calls = []
    window._mail_sync_service.test_imap_connection = lambda account_id: (
        calls.append(account_id) or True
    )
    window.test_imap_action.trigger()
    for _ in range(200):
        if not window._task_runner.busy:
            break
        QTest.qWait(10)
    assert not window._task_runner.busy
    assert calls == [window._selected_account_id()]


def test_automatic_sync_toggle_is_preserved(window):
    assert not window._sync_timer.isActive()
    window.auto_sync_action.trigger()
    assert window._sync_timer.isActive()
    assert window._preferences.value("sync/automatic", type=bool)
    window.auto_sync_action.trigger()
    assert not window._sync_timer.isActive()
