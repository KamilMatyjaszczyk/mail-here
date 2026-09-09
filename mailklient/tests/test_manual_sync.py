"""Manual sync from the real menu, with local stand-ins for IMAP."""

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, QThread, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from mailklient.services import HeaderSyncResult, MailStore, MailSyncService, mail_sync
from mailklient.ui.main_window import MainWindow


def wait_for(check):
    deadline = time.monotonic() + 5
    while not check():
        QApplication.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < deadline, "Sync did not finish"


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "cache.sqlite3")
    for name in ("One", "Two"):
        store.add_account_with_default_folders(
            name, f"{name.lower()}@example.com", imap_host="imap.example.com"
        )
    store.add_account(
        "Parked", "parked@example.com", provider="tuta", imap_host="127.0.0.1"
    )
    store.add_account("Unconfigured", "empty@example.com")
    preferences = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    preferences.setValue("sync/automatic", False)
    result = MainWindow(store, preferences=preferences)
    result.show()
    app.processEvents()
    try:
        yield result
    finally:
        wait_for(
            lambda: (
                not result._sync_in_progress
                and not result._task_runner.busy
                and not result._manual_sync_batch
            )
        )
        result.close()
        result.deleteLater()
        app.processEvents()


def test_unified_menu_syncs_each_configured_account_and_refreshes_messages(window):
    calls = []
    thread_ids = []

    def fetch(account_id, **kwargs):
        calls.append(account_id)
        thread_ids.append(threading.get_ident())
        folder = window._mail_store.list_folders(account_id)[0]
        window._mail_store.add_message(account_id, folder.id, subject="New mail")
        return HeaderSyncResult(1, 1)

    window._mail_sync_service = SimpleNamespace(fetch_imap_headers=fetch)
    assert window._selected_account_id() is None
    assert window.sync_account_action.isEnabled()
    assert window.sync_account_action.text() == "Sync all accounts"
    assert not window.auto_sync_action.isChecked()
    window.sync_account_action.trigger()
    assert not window.sync_account_action.isEnabled()
    assert not window._sync_selected_account()
    wait_for(lambda: not window._manual_sync_batch)
    assert calls == window._syncable_account_ids()
    assert all(thread_id != threading.get_ident() for thread_id in thread_ids)
    assert window._selected_account_id() is None
    assert window.message_list.count() == 2
    assert window.sync_account_action.isEnabled()
    assert not window._sync_queue and window._sync_thread is None
    assert (
        window.sync_status_label.text() == "All configured accounts have been synced."
    )


def test_failed_account_does_not_block_other_accounts_or_hide_failure(
    window, monkeypatch
):
    calls = []
    warnings = []
    first, second = window._syncable_account_ids()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    def fetch(account_id, **kwargs):
        calls.append(account_id)
        if account_id == first:
            raise OSError("Test server unavailable")
        return HeaderSyncResult(1, 0)

    window._mail_sync_service = SimpleNamespace(fetch_imap_headers=fetch)
    window.sync_account_action.trigger()
    wait_for(lambda: not window._manual_sync_batch)
    assert calls == [first, second]
    assert warnings == ["One: Test server unavailable"]
    assert "errors for 1" in window.sync_status_label.text()
    assert "One: Test server unavailable" in window.sync_status_label.toolTip()
    assert window.sync_account_action.isEnabled()


def test_selected_account_sync_does_not_sync_other_accounts(window):
    calls = []
    window._mail_sync_service = SimpleNamespace(
        fetch_imap_headers=lambda account_id, **kwargs: (
            calls.append(account_id) or HeaderSyncResult(1, 0)
        )
    )
    window.account_list.setCurrentRow(1)
    selected = window._selected_account_id()
    assert window.sync_account_action.text() == "Sync account"
    window.sync_account_action.trigger()
    wait_for(lambda: not window._sync_in_progress)
    assert calls == [selected]
    assert window._selected_account_id() == selected


def test_missing_login_is_an_error_not_a_successful_empty_sync(window, monkeypatch):
    account_id = window._syncable_account_ids()[0]
    folder = window._mail_store.list_folders(account_id)[0]
    existing = window._mail_store.add_message(
        account_id, folder.id, subject="Cached mail"
    )
    monkeypatch.setattr(mail_sync, "get_mail_account_settings", lambda *_: None)
    with pytest.raises(ValueError, match="Valid credentials"):
        MailSyncService(window._mail_store).fetch_imap_headers(account_id)
    assert window._mail_store.get_message(existing.id) is not None

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window.account_list.setCurrentRow(1)
    window.sync_account_action.trigger()
    wait_for(lambda: not window._sync_in_progress)
    assert window.sync_status_label.text() == "Sync failed"
    assert len(warnings) == 1 and "Valid credentials" in warnings[0]
    assert window.sync_account_action.isEnabled()


def test_disabling_automatic_sync_does_not_cancel_manual_batch(window):
    calls = []
    release = threading.Event()

    def fetch(account_id, **kwargs):
        calls.append(account_id)
        release.wait(2)
        return HeaderSyncResult(1, 0)

    window._mail_sync_service = SimpleNamespace(fetch_imap_headers=fetch)
    window.auto_sync_action.setChecked(True)
    try:
        window.sync_account_action.trigger()
        wait_for(lambda: bool(calls))
        window.auto_sync_action.setChecked(False)
        assert window._sync_queue
    finally:
        release.set()
    wait_for(lambda: not window._manual_sync_batch)
    assert calls == window._syncable_account_ids()


@pytest.mark.parametrize("account_row", [0, 1])
def test_busy_task_cannot_report_that_a_sync_started(window, account_row):
    window.account_list.setCurrentRow(account_row)
    release = threading.Event()
    try:
        assert window._start_task("Test", lambda: release.wait(2), lambda _: None)
        assert not window.sync_account_action.isEnabled()
        assert not window._sync_selected_account()
        assert window._sync_thread is None and not window._sync_queue
    finally:
        release.set()
        wait_for(lambda: not window._task_runner.busy)
    assert window.sync_account_action.isEnabled()


def test_cancel_keeps_ui_responsive_and_stops_remaining_accounts(window):
    calls, ticks = [], []
    release = threading.Event()

    def fetch(account_id, *, progress, **kwargs):
        calls.append(account_id)
        assert QThread.currentThread() != window.thread()
        progress("Henter testmeldinger...")
        release.wait(2)
        progress("Neste steg")
        return HeaderSyncResult(1, 0)

    window._mail_sync_service = SimpleNamespace(fetch_imap_headers=fetch)
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        window.sync_account_action.trigger()
        wait_for(
            lambda: "Henter testmeldinger" in window.sync_status_label.text() and ticks
        )
        assert window.cancel_sync_action.isEnabled()
        assert not window.close()
        assert window.isVisible()
        window.cancel_sync_action.trigger()
        assert window.sync_status_label.text() == "Cancelling sync..."
        assert not window.cancel_sync_action.isEnabled()
    finally:
        release.set()
        wait_for(lambda: not window._sync_in_progress)
        timer.stop()
    assert calls == window._syncable_account_ids()[:1]
    assert not window._sync_queue and window._sync_thread is None
    assert window.sync_status_label.text().startswith("Sync cancelled.")
    assert window.sync_account_action.isEnabled()


def test_repeated_worker_cleanup_keeps_sync_reusable(window):
    window.account_list.setCurrentRow(1)
    calls = []
    window._mail_sync_service = SimpleNamespace(
        fetch_imap_headers=lambda account_id, **kw: (
            calls.append(account_id) or HeaderSyncResult(1, 0)
        )
    )
    for _ in range(30):
        window.sync_account_action.trigger()
        wait_for(lambda: not window._sync_in_progress)
        assert window._sync_thread is None and window._sync_worker is None
        assert window.sync_account_action.isEnabled()
    assert len(calls) == 30
