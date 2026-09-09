"""OAuth client configuration from environment overrides or the system keyring."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

import keyring

CLIENT_ID_ENV_VARS = {
    "gmail": "MAILKLIENT_GMAIL_CLIENT_ID",
    "outlook": "MAILKLIENT_OUTLOOK_CLIENT_ID",
}

CLIENT_SECRET_ENV_VARS = {
    "gmail": "MAILKLIENT_GMAIL_CLIENT_SECRET",
    "outlook": "MAILKLIENT_OUTLOOK_CLIENT_SECRET",
}


class OAuthClientConfigError(RuntimeError):
    """Raised when OAuth client configuration is missing."""


def load_oauth_client_config(provider: str) -> tuple[str, str | None]:
    _validate_provider(provider)
    try:
        raw = keyring.get_password("mailklient-oauth-clients", provider)
        if raw is None:
            return "", None
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("Invalid configuration")
        client_id, secret = data["client_id"], data.get("client_secret")
        if not isinstance(client_id, str) or (
            secret is not None and not isinstance(secret, str)
        ):
            raise TypeError("Invalid configuration")
        return client_id.strip(), secret
    except (keyring.errors.KeyringError, ValueError, KeyError, TypeError) as error:
        raise OAuthClientConfigError(
            "Could not read OAuth app settings from the keyring."
        ) from error


def save_oauth_client_config(
    provider: str, client_id: str, client_secret: str | None
) -> None:
    _validate_provider(provider)
    if not client_id.strip():
        raise OAuthClientConfigError("Client ID cannot be empty.")
    try:
        keyring.set_password(
            "mailklient-oauth-clients",
            provider,
            json.dumps(
                {
                    "client_id": client_id.strip(),
                    "client_secret": client_secret or None,
                }
            ),
        )
    except keyring.errors.KeyringError as error:
        raise OAuthClientConfigError(
            "Could not save OAuth app settings in the keyring."
        ) from error


def _validate_provider(provider: str) -> None:
    if provider not in CLIENT_ID_ENV_VARS:
        raise OAuthClientConfigError("Unknown OAuth provider.")


def get_oauth_client_id(
    provider: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the configured OAuth client ID for a provider."""
    _validate_provider(provider)
    variable_name = CLIENT_ID_ENV_VARS[provider]
    values = os.environ if environ is None else environ
    client_id = values.get(variable_name, "").strip()
    if not client_id and environ is None:
        client_id, _secret = load_oauth_client_config(provider)
    if not client_id:
        raise OAuthClientConfigError(
            f"OAuth client ID is missing. Use Account > OAuth app settings, or set {variable_name}."
        )
    return client_id


def get_oauth_client_secret(
    provider: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the configured OAuth client secret for a provider if set."""
    _validate_provider(provider)
    variable_name = CLIENT_SECRET_ENV_VARS[provider]
    values = os.environ if environ is None else environ
    client_secret: str | None = values.get(variable_name, "").strip()
    if (
        not client_secret
        and environ is None
        and not values.get(CLIENT_ID_ENV_VARS[provider], "").strip()
    ):
        _client_id, client_secret = load_oauth_client_config(provider)
    return client_secret or None
