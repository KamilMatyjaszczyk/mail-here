"""Build mail settings from local account data and credentials."""

from __future__ import annotations

from mailklient.domain import Account
from mailklient.mail.config import MailAccountSettings, build_mail_account_settings
from mailklient.security import (
    OAuthCallbackError,
    OAuthClientConfigError,
    get_oauth_client_id,
    get_oauth_client_secret,
    get_oauth_provider_config,
    get_oauth_tokens,
    get_password,
    is_oauth_token_expired,
    refresh_access_token,
    save_oauth_tokens,
)
from mailklient.services.mail_store import MailStore


def get_mail_account_settings(
    store: MailStore,
    account_id: int,
) -> MailAccountSettings | None:
    """Return complete mail settings if metadata and credentials exist."""
    account = store.get_account(account_id)
    if account is None:
        return None

    secret = _get_account_secret(account)
    return build_mail_account_settings(account, secret)


def _get_account_secret(account: Account) -> str | None:
    if account.auth_method == "oauth2":
        return _get_oauth_access_token(account)

    return get_password(account.email_address)


def _get_oauth_access_token(account: Account) -> str | None:
    if account.oauth_provider is None:
        return None

    tokens = get_oauth_tokens(account.email_address)
    if tokens is None:
        return None

    if not is_oauth_token_expired(tokens):
        return tokens.access_token

    if tokens.refresh_token is None:
        return None

    try:
        refreshed_tokens = refresh_access_token(
            get_oauth_provider_config(account.oauth_provider),
            client_id=get_oauth_client_id(account.oauth_provider),
            refresh_token=tokens.refresh_token,
            client_secret=get_oauth_client_secret(account.oauth_provider),
        )
    except (KeyError, OAuthCallbackError, OAuthClientConfigError, OSError, ValueError):
        if not is_oauth_token_expired(tokens, refresh_margin_seconds=0):
            return tokens.access_token
        return None

    save_oauth_tokens(account.email_address, refreshed_tokens)
    return refreshed_tokens.access_token
