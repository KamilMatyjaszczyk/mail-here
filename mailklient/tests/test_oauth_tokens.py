from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import keyring

from mailklient.security import (
    OAuthTokens,
    build_xoauth2_payload,
    build_xoauth2_string,
    delete_oauth_tokens,
    get_oauth_tokens,
    is_oauth_token_expired,
    save_oauth_tokens,
)
from mailklient.security.credentials import SERVICE_NAME
from mailklient.security.oauth_tokens import OAUTH_TOKEN_PREFIX


def test_oauth_tokens_save_get_and_delete(monkeypatch) -> None:
    saved_tokens: dict[tuple[str, str], str] = {}

    def fake_set_password(service_name: str, username: str, password: str) -> None:
        saved_tokens[(service_name, username)] = password

    def fake_get_password(service_name: str, username: str) -> str | None:
        return saved_tokens.get((service_name, username))

    def fake_delete_password(service_name: str, username: str) -> None:
        del saved_tokens[(service_name, username)]

    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    save_oauth_tokens(
        "  Person@Example.COM  ",
        OAuthTokens(
            access_token="access",
            refresh_token="refresh",
            expires_at="2026-09-03T12:00:00+00:00",
        ),
    )

    assert get_oauth_tokens("person@example.com") == OAuthTokens(
        access_token="access",
        refresh_token="refresh",
        expires_at="2026-09-03T12:00:00+00:00",
    )
    assert list(saved_tokens) == [
        (SERVICE_NAME, f"{OAUTH_TOKEN_PREFIX}person@example.com")
    ]

    delete_oauth_tokens("person@example.com")

    assert get_oauth_tokens("person@example.com") is None


def test_oauth_tokens_ignore_missing_token_on_delete(monkeypatch) -> None:
    def fake_delete_password(_service_name: str, _username: str) -> None:
        raise keyring.errors.PasswordDeleteError("missing")

    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    delete_oauth_tokens("missing@example.com")


def test_build_xoauth2_string() -> None:
    xoauth2 = build_xoauth2_string("person@example.com", "access-token")

    assert base64.b64decode(xoauth2).decode("utf-8") == (
        "user=person@example.com\x01auth=Bearer access-token\x01\x01"
    )


def test_build_xoauth2_payload() -> None:
    assert build_xoauth2_payload("person@example.com", "access-token") == (
        "user=person@example.com\x01auth=Bearer access-token\x01\x01"
    )


def test_is_oauth_token_expired() -> None:
    expired = OAuthTokens(
        access_token="access",
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    fresh = OAuthTokens(
        access_token="access",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )

    assert is_oauth_token_expired(expired)
    assert not is_oauth_token_expired(fresh)
    assert not is_oauth_token_expired(OAuthTokens(access_token="access"))
    assert is_oauth_token_expired(
        OAuthTokens(access_token="access", expires_at="ikke-en-dato")
    )
