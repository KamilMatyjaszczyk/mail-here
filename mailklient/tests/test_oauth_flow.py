from __future__ import annotations

import json
from threading import Thread
from urllib.request import urlopen
from urllib.parse import parse_qs, urlparse

import pytest

from mailklient.security import (
    GMAIL_OAUTH_CONFIG,
    OUTLOOK_OAUTH_CONFIG,
    OAuthCallbackError,
    OAuthCallbackResult,
    OAuthCallbackServer,
    OAuthTokens,
    build_authorization_request,
    exchange_authorization_code,
    get_oauth_provider_config,
    refresh_access_token,
)


def test_get_oauth_provider_config_returns_known_provider() -> None:
    assert get_oauth_provider_config("gmail") is GMAIL_OAUTH_CONFIG
    assert get_oauth_provider_config("outlook") is OUTLOOK_OAUTH_CONFIG


def test_build_gmail_authorization_request() -> None:
    request = build_authorization_request(
        GMAIL_OAUTH_CONFIG,
        client_id="client-id",
        redirect_uri="http://localhost:8765/oauth/callback",
    )
    parsed_url = urlparse(request.authorization_url)
    query = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "accounts.google.com"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8765/oauth/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["https://mail.google.com/"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"] == [request.state]
    assert request.code_verifier


def test_build_outlook_authorization_request() -> None:
    request = build_authorization_request(
        OUTLOOK_OAUTH_CONFIG,
        client_id="client-id",
        redirect_uri="http://localhost:8765/oauth/callback",
    )
    parsed_url = urlparse(request.authorization_url)
    query = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "login.microsoftonline.com"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8765/oauth/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == [
        "offline_access "
        "https://outlook.office.com/IMAP.AccessAsUser.All "
        "https://outlook.office.com/SMTP.Send"
    ]
    assert query["code_challenge_method"] == ["S256"]
    assert "access_type" not in query
    assert "prompt" not in query
    assert query["state"] == [request.state]
    assert request.code_verifier


def test_oauth_callback_server_receives_code_and_state() -> None:
    result: dict[str, OAuthCallbackResult | Exception] = {}

    with OAuthCallbackServer() as server:
        thread = Thread(
            target=lambda: _capture_callback_result(server, result),
            daemon=True,
        )
        thread.start()

        with urlopen(
            f"{server.redirect_uri}?code=auth-code&state=state-value",
            timeout=2,
        ) as response:
            assert response.status == 200

        thread.join(timeout=2)

    assert result["callback"] == OAuthCallbackResult(
        code="auth-code",
        state="state-value",
    )


def test_oauth_callback_server_reports_provider_error() -> None:
    result: dict[str, OAuthCallbackResult | Exception] = {}

    with OAuthCallbackServer() as server:
        thread = Thread(
            target=lambda: _capture_callback_result(server, result),
            daemon=True,
        )
        thread.start()

        with pytest.raises(Exception):
            urlopen(
                f"{server.redirect_uri}?error=access_denied"
                "&error_description=Nei",
                timeout=2,
            )

        thread.join(timeout=2)

    assert isinstance(result["callback"], OAuthCallbackError)
    assert str(result["callback"]) == "Nei"


def test_exchange_authorization_code_posts_pkce_form() -> None:
    captured_request: dict[str, object] = {}

    def fake_opener(request, timeout=None):
        captured_request["url"] = request.full_url
        captured_request["timeout"] = timeout
        captured_request["data"] = request.data
        return FakeResponse(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        )

    tokens = exchange_authorization_code(
        GMAIL_OAUTH_CONFIG,
        client_id="client-id",
        redirect_uri="http://localhost:8765/oauth/callback",
        code="auth-code",
        code_verifier="code-verifier",
        client_secret="client-secret",
        timeout_seconds=4,
        opener=fake_opener,
    )

    posted_form = parse_qs(captured_request["data"].decode("utf-8"))

    assert captured_request["url"] == GMAIL_OAUTH_CONFIG.token_endpoint
    assert captured_request["timeout"] == 4
    assert posted_form["client_id"] == ["client-id"]
    assert posted_form["redirect_uri"] == ["http://localhost:8765/oauth/callback"]
    assert posted_form["grant_type"] == ["authorization_code"]
    assert posted_form["code"] == ["auth-code"]
    assert posted_form["code_verifier"] == ["code-verifier"]
    assert posted_form["client_secret"] == ["client-secret"]
    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.expires_at is not None


def test_refresh_access_token_keeps_existing_refresh_token_when_missing() -> None:
    def fake_opener(_request, timeout=None):
        return FakeResponse({"access_token": "new-access"})

    tokens = refresh_access_token(
        OUTLOOK_OAUTH_CONFIG,
        client_id="client-id",
        refresh_token="old-refresh",
        client_secret="client-secret",
        opener=fake_opener,
    )

    assert tokens == OAuthTokens(
        access_token="new-access",
        refresh_token="old-refresh",
    )


def test_exchange_authorization_code_requires_access_token() -> None:
    def fake_opener(_request, timeout=None):
        return FakeResponse({"refresh_token": "refresh"})

    with pytest.raises(OAuthCallbackError):
        exchange_authorization_code(
            GMAIL_OAUTH_CONFIG,
            client_id="client-id",
            redirect_uri="http://localhost:8765/oauth/callback",
            code="auth-code",
            code_verifier="code-verifier",
            opener=fake_opener,
        )


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


def _capture_callback_result(
    server: OAuthCallbackServer,
    result: dict[str, OAuthCallbackResult | Exception],
) -> None:
    try:
        result["callback"] = server.wait_for_callback(timeout_seconds=2)
    except Exception as error:
        result["callback"] = error
