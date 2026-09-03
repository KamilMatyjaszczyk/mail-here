"""Application service for OAuth2 login."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable

from mailklient.security import (
    OAuthCallbackError,
    OAuthCallbackServer,
    OAuthTokens,
    build_authorization_request,
    exchange_authorization_code,
    get_oauth_provider_config,
)
from mailklient.security.oauth_flow import UrlOpen


class OAuthLoginService:
    """Run the browser-based OAuth2 authorization code flow."""

    def __init__(
        self,
        *,
        browser_opener: Callable[[str], bool] = webbrowser.open,
        token_opener: UrlOpen | None = None,
    ) -> None:
        self._browser_opener = browser_opener
        self._token_opener = token_opener

    def authorize(
        self,
        provider: str,
        client_id: str,
        *,
        client_secret: str | None = None,
        timeout_seconds: float = 120,
    ) -> OAuthTokens:
        """Authorize against a provider and return OAuth2 tokens."""
        provider_config = get_oauth_provider_config(provider)

        with OAuthCallbackServer() as callback_server:
            redirect_uri = callback_server.redirect_uri
            authorization_request = build_authorization_request(
                provider_config,
                client_id=client_id,
                redirect_uri=redirect_uri,
            )
            self._browser_opener(authorization_request.authorization_url)
            callback = callback_server.wait_for_callback(timeout_seconds)

        if callback.state != authorization_request.state:
            raise OAuthCallbackError("OAuth callback state did not match")

        opener_kwargs = {}
        if self._token_opener is not None:
            opener_kwargs["opener"] = self._token_opener

        return exchange_authorization_code(
            provider_config,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code=callback.code,
            code_verifier=authorization_request.code_verifier,
            client_secret=client_secret,
            **opener_kwargs,
        )
