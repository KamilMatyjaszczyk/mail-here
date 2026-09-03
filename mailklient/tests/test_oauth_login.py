from __future__ import annotations

import json
from threading import Thread
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from mailklient.security import OAuthCallbackError
from mailklient.services import OAuthLoginService


def test_oauth_login_service_runs_browser_callback_and_token_exchange() -> None:
    browser_threads: list[Thread] = []
    captured_token_request: dict[str, object] = {}

    def fake_browser_opener(authorization_url: str) -> bool:
        query = parse_qs(urlparse(authorization_url).query)
        redirect_uri = query["redirect_uri"][0]
        state = query["state"][0]

        thread = Thread(
            target=lambda: urlopen(
                f"{redirect_uri}?code=auth-code&state={state}",
                timeout=2,
            ).read(),
            daemon=True,
        )
        browser_threads.append(thread)
        thread.start()
        return True

    def fake_token_opener(request, timeout=None):
        captured_token_request["url"] = request.full_url
        captured_token_request["data"] = request.data
        return FakeResponse(
            {
                "access_token": "access",
                "refresh_token": "refresh",
            }
        )

    service = OAuthLoginService(
        browser_opener=fake_browser_opener,
        token_opener=fake_token_opener,
    )

    tokens = service.authorize(
        "gmail",
        "client-id",
        client_secret="client-secret",
        timeout_seconds=2,
    )

    for thread in browser_threads:
        thread.join(timeout=2)

    posted_form = parse_qs(captured_token_request["data"].decode("utf-8"))

    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert posted_form["client_id"] == ["client-id"]
    assert posted_form["client_secret"] == ["client-secret"]
    assert posted_form["code"] == ["auth-code"]
    assert posted_form["code_verifier"][0]


def test_oauth_login_service_rejects_wrong_state() -> None:
    def fake_browser_opener(authorization_url: str) -> bool:
        query = parse_qs(urlparse(authorization_url).query)
        redirect_uri = query["redirect_uri"][0]

        Thread(
            target=lambda: urlopen(
                f"{redirect_uri}?code=auth-code&state=wrong-state",
                timeout=2,
            ).read(),
            daemon=True,
        ).start()
        return True

    service = OAuthLoginService(browser_opener=fake_browser_opener)

    with pytest.raises(OAuthCallbackError):
        service.authorize("gmail", "client-id", timeout_seconds=2)


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")
