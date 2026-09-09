"""Helpers for starting OAuth2 authorization code flows."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request
from urllib.request import urlopen

from mailklient.security.oauth_tokens import OAuthTokens

OAuthProvider = str


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    """Static OAuth2 settings for one mail provider."""

    name: OAuthProvider
    authorization_endpoint: str
    token_endpoint: str
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OAuthAuthorizationRequest:
    """Data needed to open a browser and later verify the callback."""

    authorization_url: str
    state: str
    code_verifier: str


@dataclass(frozen=True, slots=True)
class OAuthCallbackResult:
    """Authorization code data returned to the local callback server."""

    code: str
    state: str


class OAuthCallbackError(RuntimeError):
    """Raised when the OAuth2 callback fails or times out."""


class UrlOpen(Protocol):
    """Callable shape used by urllib.request.urlopen."""

    def __call__(self, request: Request, timeout: float | None = None): ...


GMAIL_OAUTH_CONFIG = OAuthProviderConfig(
    name="gmail",
    authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    scopes=("https://mail.google.com/",),
)

OUTLOOK_OAUTH_CONFIG = OAuthProviderConfig(
    name="outlook",
    authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    scopes=(
        "offline_access",
        "https://outlook.office.com/IMAP.AccessAsUser.All",
        "https://outlook.office.com/SMTP.Send",
    ),
)

OAUTH_PROVIDER_CONFIGS = {
    GMAIL_OAUTH_CONFIG.name: GMAIL_OAUTH_CONFIG,
    OUTLOOK_OAUTH_CONFIG.name: OUTLOOK_OAUTH_CONFIG,
}


def get_oauth_provider_config(provider: OAuthProvider) -> OAuthProviderConfig:
    """Return OAuth2 settings for a known provider."""
    return OAUTH_PROVIDER_CONFIGS[provider]


def build_authorization_request(
    provider_config: OAuthProviderConfig,
    client_id: str,
    redirect_uri: str,
) -> OAuthAuthorizationRequest:
    """Build a PKCE authorization request for a desktop OAuth2 flow."""
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _build_code_challenge(code_verifier)

    query_parameters = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider_config.scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    if provider_config.name == "gmail":
        query_parameters["access_type"] = "offline"
        query_parameters["prompt"] = "consent"

    return OAuthAuthorizationRequest(
        authorization_url=(
            f"{provider_config.authorization_endpoint}?"
            f"{urlencode(query_parameters)}"
        ),
        state=state,
        code_verifier=code_verifier,
    )


class OAuthCallbackServer:
    """Small loopback HTTP server for receiving one OAuth2 callback."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/oauth/callback",
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self._server: _OAuthHTTPServer | None = None

    @property
    def redirect_uri(self) -> str:
        """Return the redirect URI registered for this callback server."""
        if self._server is None:
            raise RuntimeError("OAuth callback server has not started")

        _host, port = self._server.server_address
        return f"http://localhost:{port}{self.path}"

    def __enter__(self) -> OAuthCallbackServer:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start(self) -> None:
        """Start listening for the provider callback."""
        self._server = _OAuthHTTPServer(
            (self.host, self.port),
            _OAuthCallbackHandler,
            callback_path=self.path,
        )

    def wait_for_callback(self, timeout_seconds: float = 120) -> OAuthCallbackResult:
        """Wait for one OAuth2 callback request."""
        if self._server is None:
            raise RuntimeError("OAuth callback server has not started")

        self._server.timeout = timeout_seconds
        self._server.handle_request()

        if self._server.callback_error is not None:
            raise self._server.callback_error
        if self._server.callback_result is None:
            raise OAuthCallbackError("OAuth callback timed out")

        return self._server.callback_result

    def close(self) -> None:
        """Close the callback server."""
        if self._server is not None:
            self._server.server_close()
            self._server = None


def exchange_authorization_code(
    provider_config: OAuthProviderConfig,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    *,
    client_secret: str | None = None,
    timeout_seconds: float = 30,
    opener: UrlOpen = urlopen,
) -> OAuthTokens:
    """Exchange an authorization code for OAuth2 tokens."""
    form_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
    }
    if client_secret is not None:
        form_data["client_secret"] = client_secret

    return _post_token_request(
        provider_config,
        form_data,
        timeout_seconds=timeout_seconds,
        opener=opener,
    )


def refresh_access_token(
    provider_config: OAuthProviderConfig,
    client_id: str,
    refresh_token: str,
    *,
    client_secret: str | None = None,
    timeout_seconds: float = 30,
    opener: UrlOpen = urlopen,
) -> OAuthTokens:
    """Refresh an expired access token."""
    form_data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": " ".join(provider_config.scopes),
    }
    if client_secret is not None:
        form_data["client_secret"] = client_secret

    return _post_token_request(
        provider_config,
        form_data,
        timeout_seconds=timeout_seconds,
        opener=opener,
        fallback_refresh_token=refresh_token,
    )


def _build_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _post_token_request(
    provider_config: OAuthProviderConfig,
    form_data: dict[str, str],
    *,
    timeout_seconds: float,
    opener: UrlOpen,
    fallback_refresh_token: str | None = None,
) -> OAuthTokens:
    request = Request(
        provider_config.token_endpoint,
        data=urlencode(form_data).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with opener(request, timeout=timeout_seconds) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise OAuthCallbackError(_read_http_error(error)) from error

    return _tokens_from_response(token_data, fallback_refresh_token)


def _tokens_from_response(
    token_data: dict[str, object],
    fallback_refresh_token: str | None,
) -> OAuthTokens:
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthCallbackError("OAuth token response did not include access_token")

    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str):
        refresh_token = fallback_refresh_token

    token_type = token_data.get("token_type")
    if not isinstance(token_type, str):
        token_type = "Bearer"

    expires_at = None
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, (int, float)):
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
        ).isoformat()

    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        token_type=token_type,
    )


def _read_http_error(error: HTTPError) -> str:
    raw_body = error.read().decode("utf-8", errors="replace")
    if not raw_body:
        return str(error)

    try:
        error_data = json.loads(raw_body)
    except json.JSONDecodeError:
        return raw_body

    description = error_data.get("error_description")
    if isinstance(description, str):
        return description

    message = error_data.get("error")
    return message if isinstance(message, str) else raw_body


class _OAuthHTTPServer(HTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        callback_path: str,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.callback_path = callback_path
        self.callback_result: OAuthCallbackResult | None = None
        self.callback_error: OAuthCallbackError | None = None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: _OAuthHTTPServer

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        if parsed_url.path != self.server.callback_path:
            self.send_error(404)
            return

        query = parse_qs(parsed_url.query)
        error = _first_query_value(query, "error")
        if error is not None:
            description = _first_query_value(query, "error_description") or error
            self.server.callback_error = OAuthCallbackError(description)
            self._send_text_response(400, "OAuth sign-in failed.")
            return

        code = _first_query_value(query, "code")
        state = _first_query_value(query, "state")
        if code is None or state is None:
            self.server.callback_error = OAuthCallbackError(
                "OAuth callback was missing code or state"
            )
            self._send_text_response(400, "OAuth callback data was missing.")
            return

        self.server.callback_result = OAuthCallbackResult(code=code, state=state)
        self._send_text_response(200, "OAuth sign-in complete.")

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_text_response(self, status_code: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _first_query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    return values[0]
