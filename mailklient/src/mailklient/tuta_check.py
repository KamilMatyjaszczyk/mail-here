"""Interactive connection check: python -m mailklient.tuta_check."""

from __future__ import annotations

import argparse
import errno
import getpass
import imaplib
import smtplib
import ssl
import sys
import warnings
from pathlib import Path

from mailklient.mail.imap_client import ImapAuthenticationError, ImapClient
from mailklient.mail.smtp_client import SmtpClient
from mailklient.mail.tuta_bridge import build_tuta_bridge_settings
from mailklient.security.credentials import save_password


def main(argv: list[str] | None = None) -> int:
    """Check both logins before saving the password; never fetch or send mail."""
    parser = argparse.ArgumentParser(
        description=(
            "Test TutaBridge IMAP/SMTP and save the bridge password in the keyring "
            "after successful sign-in. No email is fetched or sent."
        )
    )
    parser.add_argument("email_address", nargs="?", help="Full Tuta email address")
    parser.add_argument(
        "--certificate", type=Path, help="Path to the bridge's public cert.pem"
    )
    parser.add_argument(
        "--imap-auth",
        choices=("login", "plain"),
        default="login",
        help="IMAP sign-in over TLS (default: login)",
    )
    args = parser.parse_args(argv)
    stage = "Setup"
    try:
        address = args.email_address or input("Tuta address: ")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Bridge password (not your Tuta password): ")
        imap, smtp = build_tuta_bridge_settings(
            address, password, args.certificate, imap_auth=args.imap_auth
        )
        stage = "IMAP"
        print(f"Testing IMAP ({args.imap_auth}) with certificate verification...", flush=True)
        if not ImapClient(imap).test_connection():
            raise imaplib.IMAP4.error("Login failed")
        print("IMAP sign-in successful.", flush=True)
        stage = "SMTP"
        print("Testing SMTP with certificate verification...", flush=True)
        if not SmtpClient(smtp).test_connection():
            raise smtplib.SMTPAuthenticationError(535, b"Login failed")
        print("SMTP sign-in successful.", flush=True)
        stage = "Keyring"
        save_password(imap.username, password)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No email was fetched or sent.", file=sys.stderr)
        return 130
    except Exception as error:  # noqa: BLE001 - Never expose server/backend secrets.
        print(f"{stage}: {_error_message(error, stage)}", file=sys.stderr)
        return 1
    print("Bridge password saved in the keyring. Step 1 is complete; the account has not been added.")
    return 0


def _error_message(error: Exception, stage: str) -> str:
    # Server replies can contain sensitive data; expose only controlled messages.
    if stage == "Keyring":
        return "Sign-in works, but the password could not be saved in the system keyring."
    if isinstance(error, getpass.GetPassWarning):
        return "Run the test in a terminal that can hide the password."
    if isinstance(error, FileNotFoundError):
        return "Could not find cert.pem. Start TutaBridge or use --certificate."
    if isinstance(error, ssl.SSLCertVerificationError):
        return (
            "The certificate was rejected. Check that cert.pem belongs to this bridge."
        )
    if isinstance(error, ssl.SSLError):
        return "TLS failed. Check the bridge certificate and SSL/TLS on both ports."
    if isinstance(error, ConnectionRefusedError):
        return "TutaBridge is not responding on this port. Check that it is running."
    if isinstance(error, ConnectionResetError):
        return "TutaBridge reset the connection during the test."
    if isinstance(error, BrokenPipeError):
        return "The connection to TutaBridge was closed during the test."
    if isinstance(error, TimeoutError):
        return "TutaBridge did not respond before the timeout."
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "Sign-in rejected. Check the full Tuta address and bridge password."
    if isinstance(error, ImapAuthenticationError):
        return (
            "The bridge rejected sign-in after a successful TLS connection. "
            "Copy the bridge password again from Connection. If the password was recently "
            "changed, restart TutaBridge before trying again."
        )
    if isinstance(error, imaplib.IMAP4.abort):
        return "TutaBridge closed the connection. Try again."
    if isinstance(error, imaplib.IMAP4.error):
        return "IMAP protocol error during startup or shutdown; a password error has not been confirmed."
    if isinstance(error, ValueError):
        return (
            "Check the full email address, bridge password and certificate settings."
        )
    if isinstance(error, OSError):
        if error.errno == errno.EBADF:
            return "Local connection handling error (EBADF); not a certificate rejection."
        code = errno.errorcode.get(error.errno or 0, "unknown")
        return (
            f"System error during the connection test ({code}); no password details are shown."
        )
    return "Connection test failed. Check TutaBridge and try again."


if __name__ == "__main__":
    raise SystemExit(main())
