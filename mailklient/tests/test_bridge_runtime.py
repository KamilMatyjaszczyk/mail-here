from __future__ import annotations

import os
import ssl
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mailklient.services import MailStore, bridge_runtime
from mailklient.services.bridge_runtime import (
    BridgeRuntimeService,
    BridgeStartError,
    BridgeStatus,
)
from mailklient.ui.main_window import MainWindow
from mailklient.workers.bridge_worker import BridgeWorker
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture
def runtime(monkeypatch):
    service = BridgeRuntimeService()
    launches = []
    monkeypatch.setattr(service, "_is_running", lambda: False)
    monkeypatch.setattr(service, "_launch", lambda exe: launches.append(exe) or True)
    monkeypatch.setattr(
        bridge_runtime.shutil, "which", lambda _: "/usr/bin/tutabridge-gui"
    )
    monkeypatch.setattr(bridge_runtime.time, "sleep", lambda _: None)
    return service, launches


@pytest.mark.parametrize(
    "status",
    [
        BridgeStatus("ready", "ready"),
        BridgeStatus("certificate", "certificate"),
        BridgeStatus("ready", "offline"),
        BridgeStatus("unavailable", "offline"),
    ],
)
def test_existing_endpoint_never_launches_duplicate(runtime, monkeypatch, status):
    service, launches = runtime
    monkeypatch.setattr(service, "check", lambda _: status)
    assert service.ensure_started() == status
    assert launches == []


@pytest.mark.parametrize("already_running", [False, True])
def test_start_waits_for_both_endpoints(runtime, monkeypatch, already_running):
    service, launches = runtime
    monkeypatch.setattr(service, "_is_running", lambda: already_running)
    states = iter(
        [
            BridgeStatus("offline", "offline"),
            BridgeStatus("ready", "offline"),
            BridgeStatus("ready", "ready"),
        ]
    )
    monkeypatch.setattr(service, "check", lambda _: next(states))
    assert service.ensure_started().ready
    assert len(launches) == (0 if already_running else 1)


def test_missing_executable(runtime, monkeypatch):
    service, launches = runtime
    monkeypatch.setattr(service, "check", lambda _: BridgeStatus("offline", "offline"))
    monkeypatch.setattr(bridge_runtime.shutil, "which", lambda _: None)
    with pytest.raises(BridgeStartError, match="Install"):
        service.ensure_started()
    assert launches == []


def test_start_timeout_is_bounded(runtime, monkeypatch):
    service, launches = runtime
    monkeypatch.setattr(service, "check", lambda _: BridgeStatus("offline", "offline"))
    clock = iter([0, 1, 21])
    monkeypatch.setattr(bridge_runtime.time, "monotonic", lambda: next(clock))
    with pytest.raises(BridgeStartError, match="did not respond in time"):
        service.ensure_started()
    assert len(launches) == 1


@pytest.mark.parametrize(
    "error, expected",
    [
        (ConnectionRefusedError(), "offline"),
        (ssl.SSLCertVerificationError(), "certificate"),
        (TimeoutError(), "unavailable"),
    ],
)
def test_endpoint_failure_types(monkeypatch, error, expected):
    def connect(*args, **kwargs):
        assert args == (("127.0.0.1", 1143),)
        assert kwargs["timeout"] == 1
        raise error

    monkeypatch.setattr(bridge_runtime.socket, "create_connection", connect)
    assert BridgeRuntimeService._check_endpoint(1143, b"* OK", Path("cert")) == expected


def test_worker_hides_unexpected_errors_and_finishes():
    class Broken:
        def check(self, _cert):
            raise RuntimeError("secret")

    worker = BridgeWorker(Broken(), None)
    failures, done = [], []
    worker.failed.connect(failures.append)
    worker.done.connect(lambda: done.append(True))
    worker.run()
    assert len(failures) == 1 and "secret" not in failures[0]
    assert done == [True]


def test_detached_launcher_uses_qt_instance_return_value():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    assert BridgeRuntimeService._launch("/usr/bin/true") is True
    assert BridgeRuntimeService._launch("/nonexistent/mailklient-test-bridge") is False


def test_gui_bridge_preferences_and_background_start(tmp_path):
    app = QApplication.instance() or QApplication([])
    preferences = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store = MailStore(tmp_path / "cache.sqlite3")
    store.add_account_with_default_folders("Tuta", "user@tuta.io", provider="tuta")
    calls = []

    class Runtime:
        def check(self, _cert):
            calls.append((False, threading.get_ident()))
            return BridgeStatus("ready", "ready")

        def ensure_started(self, _cert):
            calls.append((True, threading.get_ident()))
            return BridgeStatus("ready", "ready")

    window = MainWindow(
        store, bridge_runtime_service=Runtime(), preferences=preferences
    )

    def drain():
        deadline = time.monotonic() + 5
        while window._bridge_thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window._bridge_thread is None

    try:
        assert not window.bridge_autostart_action.isChecked()
        window.initialize_bridge()
        drain()
        assert calls[0][0] is False
        window.bridge_autostart_action.setChecked(True)
        preferences.sync()
        restored = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        assert restored.value("tuta/autostart", False, type=bool)
        window.initialize_bridge()
        drain()
        assert calls[-1][0] is True
        assert all(thread != threading.get_ident() for _, thread in calls)
        assert "TLS available" in window.bridge_status_label.text()
        assert window.bridge_start_action.isEnabled()
        window.bridge_check_action.trigger()
        drain()
        assert calls[-1][0] is False
    finally:
        if window._bridge_thread is not None:
            window._bridge_thread.quit()
            window._bridge_thread.wait(5000)
            app.processEvents()
        window.close()
