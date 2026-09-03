"""OAuth2 token storage using the system keyring."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import keyring

from mailklient.security.credentials import SERVICE_NAME

OAUTH_TOKEN_PREFIX = "oauth2:"


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """OAuth2 token data kept outside the SQLite cache."""

    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None
    token_type: str = "Bearer"


def save_oauth_tokens(email_address: str, tokens: OAuthTokens) -> None:
    """Save OAuth2 tokens for an account in the user's keyring."""
    keyring.set_password(
        SERVICE_NAME,
        _token_name(email_address),
        json.dumps(asdict(tokens)),
    )


def get_oauth_tokens(email_address: str) -> OAuthTokens | None:
    """Return OAuth2 tokens for an account from the user's keyring."""
    raw_tokens = keyring.get_password(SERVICE_NAME, _token_name(email_address))
    if raw_tokens is None:
        return None

    token_data = json.loads(raw_tokens)
    return OAuthTokens(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=token_data.get("expires_at"),
        token_type=token_data.get("token_type", "Bearer"),
    )


def delete_oauth_tokens(email_address: str) -> None:
    """Delete OAuth2 tokens for an account from keyring if they exist."""
    try:
        keyring.delete_password(SERVICE_NAME, _token_name(email_address))
    except keyring.errors.PasswordDeleteError:
        return


def build_xoauth2_string(email_address: str, access_token: str) -> str:
    """Build an XOAUTH2 initial client response for IMAP/SMTP later."""
    return base64.b64encode(
        build_xoauth2_payload(email_address, access_token).encode("utf-8")
    ).decode("ascii")


def build_xoauth2_payload(email_address: str, access_token: str) -> str:
    """Build the raw XOAUTH2 payload used by Python IMAP/SMTP helpers."""
    return f"user={email_address}\x01auth=Bearer {access_token}\x01\x01"


def is_oauth_token_expired(
    tokens: OAuthTokens,
    *,
    refresh_margin_seconds: int = 60,
) -> bool:
    """Return True when a token is expired or too close to expiry."""
    if tokens.expires_at is None:
        return False

    try:
        expires_at = datetime.fromisoformat(tokens.expires_at)
    except ValueError:
        return True

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    return datetime.now(UTC) >= expires_at - timedelta(seconds=refresh_margin_seconds)


def _token_name(email_address: str) -> str:
    return f"{OAUTH_TOKEN_PREFIX}{email_address.strip().casefold()}"
