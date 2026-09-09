"""Connection settings for an independently installed TutaBridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from mailklient.mail.config import ImapSettings, SmtpSettings


def default_certificate_path() -> Path:
    """Locate the public bridge certificate using the Linux XDG convention."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "tutabridge" / "cert.pem"


def build_tuta_bridge_settings(
    email_address: str,
    bridge_password: str,
    certificate_path: Path | None = None,
    *,
    imap_auth: Literal["login", "plain"] = "login",
) -> tuple[ImapSettings, SmtpSettings]:
    """Use implicit TLS and a bridge password on IPv4 loopback only."""
    address = email_address.strip()
    if (
        address.count("@") != 1
        or not all(address.split("@"))
        or any(character.isspace() for character in address)
    ):
        raise ValueError("Enter the full Tuta email address.")
    if not bridge_password:
        raise ValueError("Enter the local bridge password.")
    certificate = certificate_path or default_certificate_path()
    if not certificate.is_file():
        raise FileNotFoundError("TutaBridge certificate not found.")
    return (
        ImapSettings(
            host="127.0.0.1",
            port=1143,
            username=address,
            password=bridge_password,
            local_certificate=str(certificate),
            timeout=10.0,
            password_mechanism=imap_auth,
        ),
        SmtpSettings(
            host="127.0.0.1",
            port=1025,
            username=address,
            password=bridge_password,
            security="ssl",
            local_certificate=str(certificate),
            timeout=10.0,
        ),
    )
