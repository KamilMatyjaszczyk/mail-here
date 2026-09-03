"""OAuth client configuration from environment variables."""

from __future__ import annotations

from collections.abc import Mapping
import os

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


def get_oauth_client_id(
    provider: str,
    environ: Mapping[str, str] = os.environ,
) -> str:
    """Return the configured OAuth client ID for a provider."""
    variable_name = CLIENT_ID_ENV_VARS[provider]
    client_id = environ.get(variable_name, "").strip()
    if not client_id:
        raise OAuthClientConfigError(
            f"Miljovariabelen {variable_name} mangler OAuth client ID."
        )
    return client_id


def get_oauth_client_secret(
    provider: str,
    environ: Mapping[str, str] = os.environ,
) -> str | None:
    """Return the configured OAuth client secret for a provider if set."""
    variable_name = CLIENT_SECRET_ENV_VARS[provider]
    client_secret = environ.get(variable_name, "").strip()
    return client_secret or None
