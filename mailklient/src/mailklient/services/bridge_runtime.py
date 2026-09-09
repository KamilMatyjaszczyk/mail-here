"""Start the installed TutaBridge and check its local TLS endpoints."""

from __future__ import annotations

import os
import shutil
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mailklient.mail.tuta_bridge import default_certificate_path
from mailklient.security.tls import create_mail_ssl_context

EndpointState = Literal["ready", "offline", "certificate", "unavailable"]


@dataclass(frozen=True, slots=True)
class BridgeStatus:
    imap: EndpointState
    smtp: EndpointState

    @property
    def ready(self) -> bool:
        return self.imap == self.smtp == "ready"

    @property
    def offline(self) -> bool:
        return self.imap == self.smtp == "offline"

    @property
    def message(self) -> str:
        if self.ready:
            return "TutaBridge: IMAP and SMTP available (TLS)."
        if self.offline:
            return "TutaBridge: not started."
        if "certificate" in (self.imap, self.smtp):
            return "TutaBridge: the certificate is missing or could not be verified."
        return "TutaBridge: IMAP or SMTP is unavailable. Check the bridge window."


class BridgeStartError(Exception):
    """A safe, user-facing bridge startup error."""


class BridgeRuntimeService:
    """No credentials, config secrets or mail are read by these checks."""

    def check(self, certificate_path: Path | None = None) -> BridgeStatus:
        certificate = certificate_path or default_certificate_path()
        return BridgeStatus(
            self._check_endpoint(1143, b"* OK", certificate),
            self._check_endpoint(1025, b"220", certificate),
        )

    def ensure_started(self, certificate_path: Path | None = None) -> BridgeStatus:
        status = self.check(certificate_path)
        if not status.offline:
            # Do not start another instance on partial readiness or TLS errors.
            return status
        if not self._is_running():
            executable = shutil.which("tutabridge-gui")
            if executable is None:
                raise BridgeStartError(
                    "Could not find tutabridge-gui. Install TutaBridge first."
                )
            if not self._launch(executable):
                raise BridgeStartError("Could not start TutaBridge.")

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            time.sleep(0.5)
            status = self.check(certificate_path)
            if status.ready or "certificate" in (status.imap, status.smtp):
                return status
        raise BridgeStartError(
            "TutaBridge has started but did not respond in time. "
            "Check sign-in and Running status in the bridge window, then check status again."
        )

    @staticmethod
    def _is_running() -> bool:
        # Inspect only process names owned by this user, never command-line secrets.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid == os.getuid() and (
                    (entry / "comm").read_text().strip()
                    in {"tutabridge-gui", "tutabridge"}
                ):
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _launch(executable: str) -> bool:
        from PySide6.QtCore import QProcess

        process = QProcess()
        process.setProgram(executable)
        process.setWorkingDirectory(str(Path.home()))
        process.setStandardOutputFile(os.devnull)
        process.setStandardErrorFile(os.devnull)
        # A detached bridge remains available to other clients when ours closes.
        result = process.startDetached()
        # PySide exposes bool or (started, pid), depending on binding/overload.
        return result[0] if isinstance(result, tuple) else bool(result)

    @staticmethod
    def _check_endpoint(port: int, greeting: bytes, certificate: Path) -> EndpointState:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0) as sock:
                try:
                    context = create_mail_ssl_context("127.0.0.1", str(certificate))
                except (OSError, ssl.SSLError, ValueError):
                    return "certificate"
                with context.wrap_socket(sock, server_hostname="127.0.0.1") as secured:
                    with secured.makefile("rb") as stream:
                        line = stream.readline(512)
                    return (
                        "ready" if line.startswith(greeting + b" ") else "unavailable"
                    )
        except ConnectionRefusedError:
            return "offline"
        except ssl.SSLCertVerificationError:
            return "certificate"
        except (OSError, ValueError):
            return "unavailable"
