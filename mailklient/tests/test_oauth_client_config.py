from __future__ import annotations

import pytest

from mailklient.security import (
    OAuthClientConfigError,
    get_oauth_client_id,
    get_oauth_client_secret,
)


def test_get_oauth_client_id_reads_provider_env_var() -> None:
    assert (
        get_oauth_client_id(
            "gmail",
            environ={"MAILKLIENT_GMAIL_CLIENT_ID": " client-id "},
        )
        == "client-id"
    )


def test_get_oauth_client_id_requires_value() -> None:
    with pytest.raises(OAuthClientConfigError):
        get_oauth_client_id("outlook", environ={})


def test_get_oauth_client_secret_reads_provider_env_var() -> None:
    assert (
        get_oauth_client_secret(
            "gmail",
            environ={"MAILKLIENT_GMAIL_CLIENT_SECRET": " client-secret "},
        )
        == "client-secret"
    )


def test_get_oauth_client_secret_is_optional() -> None:
    assert get_oauth_client_secret("outlook", environ={}) is None
