"""Set up a local TutaBridge account after checking both connections."""

from __future__ import annotations

import sqlite3
import ssl
from pathlib import Path

from mailklient.domain import Account
from mailklient.mail.imap_client import ImapClient
from mailklient.mail.smtp_client import SmtpClient
from mailklient.mail.tuta_bridge import build_tuta_bridge_settings
from mailklient.security.credentials import get_password, save_password
from mailklient.services.mail_store import MailStore


class TutaSetupError(Exception):
    """A setup failure with a message suitable for display."""


class TutaSetupService:
    def __init__(
        self,
        store: MailStore,
        imap_client_class=ImapClient,
        smtp_client_class=SmtpClient,
    ) -> None:
        self._store = store
        self._imap_client_class = imap_client_class
        self._smtp_client_class = smtp_client_class

    def add_account(
        self,
        display_name: str,
        email_address: str,
        password: str | None = None,
        certificate_path: Path | None = None,
    ) -> Account:
        address = email_address.strip()
        if any(
            a.email_address.casefold() == address.casefold()
            for a in self._store.list_accounts()
        ):
            raise TutaSetupError("An account with this address already exists.")
        try:
            secret = password if password is not None else get_password(address)
        except Exception:  # noqa: BLE001 - Hide credential backend details.
            raise TutaSetupError(
                "Could not read the bridge password from the keyring."
            ) from None
        if not secret:
            raise TutaSetupError(
                "No bridge password is stored. Enter it in the account dialog."
            )
        try:
            imap, smtp = build_tuta_bridge_settings(
                address, secret, certificate_path, imap_auth="plain"
            )
        except (OSError, ValueError):
            raise TutaSetupError(
                "Check the Tuta address and the path to cert.pem."
            ) from None

        for name, client in (
            ("IMAP", self._imap_client_class(imap)),
            ("SMTP", self._smtp_client_class(smtp)),
        ):
            try:
                if not client.test_connection():
                    raise TutaSetupError(f"{name}: Connection test failed.")
            except ssl.SSLCertVerificationError:
                raise TutaSetupError(
                    f"{name}: The bridge certificate was rejected."
                ) from None
            except (ConnectionRefusedError, TimeoutError):
                raise TutaSetupError(
                    f"{name}: TutaBridge is not responding. Start the bridge."
                ) from None
            except Exception:  # noqa: BLE001 - Server errors can contain credentials.
                raise TutaSetupError(
                    f"{name}: Connection or sign-in failed. Check the bridge password."
                ) from None

        try:
            account = self._store.add_account_with_default_folders(
                display_name.strip() or address,
                address,
                provider="tuta",
                local_certificate=imap.local_certificate,
                username=address,
                imap_host=imap.host,
                imap_port=imap.port,
                imap_security=imap.security,
                smtp_host=smtp.host,
                smtp_port=smtp.port,
                smtp_security=smtp.security,
            )
        except sqlite3.IntegrityError:
            raise TutaSetupError(
                "An account with this address already exists."
            ) from None
        try:
            if password is not None:
                save_password(address, secret)
        except Exception:  # noqa: BLE001 - Roll back without exposing credentials.
            self._store.delete_account(account.id)
            raise TutaSetupError(
                "Could not save the bridge password in the keyring."
            ) from None
        return account
