"""Credential storage using the system keyring."""

from __future__ import annotations

import keyring

SERVICE_NAME = "mailklient"


def save_password(email_address: str, password: str) -> None:
    """Save an account password in the user's keyring."""
    keyring.set_password(SERVICE_NAME, _credential_name(email_address), password)


def get_password(email_address: str) -> str | None:
    """Return an account password from the user's keyring."""
    return keyring.get_password(SERVICE_NAME, _credential_name(email_address))


def delete_password(email_address: str) -> None:
    """Delete an account password from the user's keyring if it exists."""
    try:
        keyring.delete_password(SERVICE_NAME, _credential_name(email_address))
    except keyring.errors.PasswordDeleteError:
        return


def _credential_name(email_address: str) -> str:
    return email_address.strip().casefold()
