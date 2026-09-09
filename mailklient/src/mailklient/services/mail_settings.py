"""Build mail settings from local account data and credentials."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mailklient.domain import Account
from mailklient.mail.config import (
    MailAccountSettings,
    build_mail_account_settings,
    is_personal_outlook_account,
)
from mailklient.mail.tuta_bridge import build_tuta_bridge_settings
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
    if account.provider == "tuta":
        if not secret:
            raise ValueError("Tuta bridge password is missing from keyring.")
        if (
            account.auth_method != "password"
            or account.imap_host != "127.0.0.1"
            or account.smtp_host != "127.0.0.1"
        ):
            raise ValueError("Tuta accounts require a local bridge and password login.")
        imap, smtp = build_tuta_bridge_settings(
            account.email_address,
            secret,
            Path(account.local_certificate) if account.local_certificate else None,
            imap_auth="plain",
        )
        return MailAccountSettings(account.id, account.email_address, imap, smtp)
    settings = build_mail_account_settings(account, secret)
    if (
        settings is not None
        and is_personal_outlook_account(account)
        and settings.smtp.host.casefold() == "smtp.office365.com"
    ):
        settings = replace(
            settings, smtp=replace(settings.smtp, host="smtp-mail.outlook.com")
        )
    return settings


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
