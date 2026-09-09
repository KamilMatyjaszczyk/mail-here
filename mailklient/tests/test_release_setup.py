from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from mailklient import main as entry
from mailklient.install_launcher import install_launcher, remove_launcher
from mailklient.security import oauth_client_config
from mailklient.services import MailStore
from mailklient.ui.main_window import MainWindow


def test_new_install_has_no_default_oauth_registration(monkeypatch):
    for name in (
        *oauth_client_config.CLIENT_ID_ENV_VARS.values(),
        *oauth_client_config.CLIENT_SECRET_ENV_VARS.values(),
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(oauth_client_config.keyring, "get_password", lambda *_: None)
    for provider in ("gmail", "outlook"):
        with pytest.raises(
            oauth_client_config.OAuthClientConfigError, match="client ID is missing"
        ):
            oauth_client_config.get_oauth_client_id(provider)
        assert oauth_client_config.get_oauth_client_secret(provider) is None


def test_launcher_removal_does_not_touch_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    data = tmp_path / "mailklient.sqlite3"
    data.write_bytes(b"retained")
    launcher = install_launcher()
    assert remove_launcher()
    assert not launcher.exists() and data.read_bytes() == b"retained"
    assert not remove_launcher()
    launcher.symlink_to(data)
    with pytest.raises(ValueError):
        remove_launcher()
    assert data.read_bytes() == b"retained"


def test_rename_reuses_existing_cache_and_keyring_names(tmp_path, monkeypatch):
    from mailklient.config import default_database_path
    from mailklient.security import credentials

    monkeypatch.delenv("MAILKLIENT_DATABASE_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old_path = tmp_path / "mailklient/mailklient.sqlite3"
    old_store = MailStore(old_path)
    account = old_store.add_account("Existing", "existing@example.com")
    assert default_database_path() == old_path
    assert MailStore(default_database_path()).list_accounts() == [account]
    stored = {("mailklient", account.email_address): "synthetic-password"}
    monkeypatch.setattr(
        credentials.keyring, "get_password", lambda service, name: stored[(service, name)]
    )
    assert credentials.get_password(account.email_address) == "synthetic-password"


@pytest.mark.parametrize("demo", [False, True])
@pytest.mark.parametrize("desktop_installed", [False, True])
def test_production_startup_does_not_insert_demo_data(
    tmp_path, monkeypatch, demo, desktop_installed
):
    store = MailStore(tmp_path / "cache.sqlite3")
    identity = {}
    monkeypatch.setattr(
        entry,
        "QApplication",
        lambda _: SimpleNamespace(
            exec=lambda: 0,
            setApplicationDisplayName=lambda value: identity.update(name=value),
            setDesktopFileName=lambda value: identity.update(desktop=value),
        ),
    )
    monkeypatch.setattr(
        entry,
        "MainWindow",
        lambda _: SimpleNamespace(show=lambda: None, initialize_bridge=lambda: None),
    )
    monkeypatch.setattr(entry, "QTimer", SimpleNamespace(singleShot=lambda *args: None))

    def locate(location, filename):
        assert location == entry.QStandardPaths.StandardLocation.GenericDataLocation
        assert filename == "applications/mailklient.desktop"
        return "/usr/share/applications/mailklient.desktop" if desktop_installed else ""

    monkeypatch.setattr(entry.QStandardPaths, "locate", locate)
    monkeypatch.setattr(entry, "default_database_path", lambda: store.database_path)
    assert entry.main(["--demo"] if demo else []) == 0
    expected_identity = {"name": "mcpMail"}
    if desktop_installed:
        expected_identity["desktop"] = "mailklient"
    assert identity == expected_identity
    assert bool(store.list_accounts()) is demo


def test_cache_dialog_has_a_real_widget_parent_and_cancel_is_safe(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "cache.sqlite3")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=settings)
    parents = []

    def cancel(parent, *args):
        assert isinstance(parent, MainWindow)
        parents.append(parent)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", cancel)
    window._clear_attachment_cache()
    assert parents == [window] and not window._task_runner.busy
    window.close()
    app.processEvents()


def test_resizing_rejoins_toolbar_after_controller_refactor(tmp_path):
    from itertools import pairwise

    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "cache.sqlite3")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=settings)
    # A wider system font/translation must still allow the controls to rejoin.
    window.remote_content_checkbox.setMinimumWidth(220)
    window.show()
    try:
        for width, mode in [(2200, "single"), (1040, "compact"), (2200, "single")]:
            window.resize(width, 800)
            for _ in range(5):
                app.processEvents()
                QTest.qWait(10)
            assert window._message_toolbar_mode == mode
            checkbox_height = window.remote_content_checkbox.sizeHint().height()
            expected_height = (
                max(32, checkbox_height) + 8
                if mode == "single"
                else 44 + checkbox_height
            )
            assert window.message_toolbar.height() == expected_height
            buttons = [
                window.compose_button,
                window.reply_button,
                window.forward_button,
                window.archive_button,
                window.trash_button,
                window.move_button,
                window.mark_read_button,
                window.mark_unread_button,
            ]
            assert len({button.y() for button in buttons}) == 1
            for left, right in pairwise(buttons):
                assert left.geometry().right() < right.geometry().left()
            for control in [*buttons, window.remote_content_checkbox]:
                assert control.isVisible()
                assert window.message_toolbar.rect().contains(control.geometry())
    finally:
        window.close()
