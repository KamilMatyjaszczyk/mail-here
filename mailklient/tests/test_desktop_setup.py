from __future__ import annotations

import configparser
import os
import shlex
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import keyring
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from mailklient.install_launcher import install_launcher
from mailklient.security import oauth_client_config as config
from mailklient.ui.oauth_settings_dialog import OAuthSettingsDialog


@pytest.fixture
def stored_config(monkeypatch):
    values = {}
    monkeypatch.setattr(
        keyring, "get_password", lambda service, name: values.get((service, name))
    )
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, name, value: values.__setitem__((service, name), value),
    )
    for name in (
        *config.CLIENT_ID_ENV_VARS.values(),
        *config.CLIENT_SECRET_ENV_VARS.values(),
    ):
        monkeypatch.delenv(name, raising=False)
    return values


def test_oauth_config_persists_without_environment_and_keeps_overrides_separate(
    stored_config, monkeypatch
):
    config.save_oauth_client_config("gmail", " saved-client ", "saved-secret")
    assert config.get_oauth_client_id("gmail") == "saved-client"
    assert config.get_oauth_client_secret("gmail") == "saved-secret"
    monkeypatch.setenv("MAILKLIENT_GMAIL_CLIENT_ID", "overridden-client")
    assert config.get_oauth_client_id("gmail") == "overridden-client"
    assert config.get_oauth_client_secret("gmail") is None
    monkeypatch.setenv("MAILKLIENT_GMAIL_CLIENT_SECRET", "overridden-secret")
    assert config.get_oauth_client_secret("gmail") == "overridden-secret"
    assert config.load_oauth_client_config("outlook") == ("", None)


@pytest.mark.parametrize(
    "raw",
    ["[]", '{"client_id": 1}', '{"client_id": "id", "client_secret": []}', "not json"],
)
def test_invalid_keyring_config_is_rejected(stored_config, raw):
    stored_config[("mailklient-oauth-clients", "gmail")] = raw
    with pytest.raises(config.OAuthClientConfigError):
        config.get_oauth_client_id("gmail")


def test_oauth_dialog_preserves_hidden_secret_and_can_clear_it(stored_config):
    app = QApplication.instance() or QApplication([])
    config.save_oauth_client_config("gmail", "saved-id", "saved-secret")
    dialog = OAuthSettingsDialog()
    assert dialog.client_id.text() == "saved-id"
    assert dialog.client_secret.text() == ""
    assert dialog.client_secret.echoMode() == QLineEdit.EchoMode.Password
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert config.load_oauth_client_config("gmail") == ("saved-id", "saved-secret")
    dialog = OAuthSettingsDialog()
    dialog.clear_secret.setChecked(True)
    dialog.accept()
    assert config.load_oauth_client_config("gmail") == ("saved-id", None)
    app.processEvents()


def test_dialog_does_not_pair_old_secret_with_new_id(stored_config):
    app = QApplication.instance() or QApplication([])
    config.save_oauth_client_config("gmail", "old-id", "old-secret")
    dialog = OAuthSettingsDialog()
    dialog.client_id.setText("new-id")
    dialog.accept()
    assert config.load_oauth_client_config("gmail") == ("new-id", None)
    app.processEvents()


def test_keyring_failure_keeps_dialog_open_without_disclosing_secret(
    stored_config, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    dialog = OAuthSettingsDialog()
    dialog.client_id.setText("id")
    dialog.client_secret.setText("private-value")

    def fail(*args):
        raise keyring.errors.KeyringError("private-value")

    monkeypatch.setattr(keyring, "set_password", fail)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.status.text() and "private-value" not in dialog.status.text()
    app.processEvents()


@pytest.mark.parametrize(
    "executable", ["/tmp/test app/.venv/bin/python", '/tmp/a\\b"c$d`e%/python']
)
def test_launcher_keeps_venv_path_and_quotes_exec_without_credentials(
    tmp_path, monkeypatch, executable
):
    monkeypatch.setattr(sys, "executable", executable)
    destination = install_launcher(tmp_path)
    assert destination == tmp_path / "applications" / "mailklient.desktop"
    text = destination.read_text()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    entry = parser["Desktop Entry"]
    assert entry["Name"] == "mcpMail"
    # Undo desktop string escaping before parsing the Exec argument quoting.
    command = entry["Exec"].replace("\\\\", "\\")
    args = shlex.split(command)
    decoded = args[0].replace("\\$", "$").replace("\\`", "`").replace("%%", "%")
    assert decoded == executable
    assert args[1:] == ["-m", "mailklient.main"]
    assert entry["Terminal"] == "false"
    assert "SECRET" not in text and "CLIENT_ID" not in text
    assert destination.stat().st_mode & 0o777 == 0o600


def test_launcher_rejects_invalid_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/tmp/bad\nExec=other/python")
    with pytest.raises(ValueError):
        install_launcher(tmp_path)
    assert not (tmp_path / "applications" / "mailklient.desktop").exists()
