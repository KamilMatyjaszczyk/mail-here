"""Security package for credential handling."""

from mailklient.security.credentials import (
    delete_password,
    get_password,
    save_password,
)
from mailklient.security.oauth_client_config import (
    CLIENT_ID_ENV_VARS,
    CLIENT_SECRET_ENV_VARS,
    OAuthClientConfigError,
    get_oauth_client_id,
    get_oauth_client_secret,
)
from mailklient.security.oauth_tokens import (
    OAuthTokens,
    build_xoauth2_payload,
    build_xoauth2_string,
    delete_oauth_tokens,
    get_oauth_tokens,
    is_oauth_token_expired,
    save_oauth_tokens,
)
from mailklient.security.oauth_flow import (
    GMAIL_OAUTH_CONFIG,
    OUTLOOK_OAUTH_CONFIG,
    OAUTH_PROVIDER_CONFIGS,
    OAuthAuthorizationRequest,
    OAuthCallbackError,
    OAuthCallbackResult,
    OAuthCallbackServer,
    OAuthProviderConfig,
    build_authorization_request,
    exchange_authorization_code,
    get_oauth_provider_config,
    refresh_access_token,
)

__all__ = [
    "CLIENT_ID_ENV_VARS",
    "CLIENT_SECRET_ENV_VARS",
    "GMAIL_OAUTH_CONFIG",
    "OAUTH_PROVIDER_CONFIGS",
    "OUTLOOK_OAUTH_CONFIG",
    "OAuthAuthorizationRequest",
    "OAuthCallbackError",
    "OAuthCallbackResult",
    "OAuthCallbackServer",
    "OAuthClientConfigError",
    "OAuthProviderConfig",
    "OAuthTokens",
    "build_authorization_request",
    "build_xoauth2_payload",
    "build_xoauth2_string",
    "delete_oauth_tokens",
    "delete_password",
    "exchange_authorization_code",
    "get_oauth_client_id",
    "get_oauth_client_secret",
    "get_oauth_provider_config",
    "get_oauth_tokens",
    "get_password",
    "is_oauth_token_expired",
    "refresh_access_token",
    "save_oauth_tokens",
    "save_password",
]
