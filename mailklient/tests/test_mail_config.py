from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mailklient.domain import Account
from mailklient.mail import ImapSettings, MailAccountSettings, SmtpSettings
from mailklient.mail.config import build_mail_account_settings, get_mail_provider_defaults
from mailklient.security import OAuthTokens
from mailklient.services import MailStore
from mailklient.services import mail_settings


def test_build_mail_account_settings_from_account_and_password() -> None:
    account = Account(
        id=1,
        display_name="Privat",
        email_address="privat@example.com",
        username="privatbruker",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
    )

    settings = build_mail_account_settings(account, "hemmelig")

    assert settings == MailAccountSettings(
        account_id=1,
        email_address="privat@example.com",
        imap=ImapSettings(
            host="imap.example.com",
            port=993,
            username="privatbruker",
            password="hemmelig",
            security="ssl",
        ),
        smtp=SmtpSettings(
            host="smtp.example.com",
            port=587,
            username="privatbruker",
            password="hemmelig",
            security="starttls",
        ),
    )


def test_build_mail_account_settings_uses_email_when_username_is_missing() -> None:
    account = Account(
        id=1,
        display_name="Privat",
        email_address="privat@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
    )

    settings = build_mail_account_settings(account, "hemmelig")

    assert settings is not None
    assert settings.imap.username == "privat@example.com"
    assert settings.smtp.username == "privat@example.com"


def test_build_mail_account_settings_returns_none_when_incomplete() -> None:
    account = Account(
        id=1,
        display_name="Privat",
        email_address="privat@example.com",
        imap_host="imap.example.com",
        imap_port=993,
    )

    assert build_mail_account_settings(account, "hemmelig") is None
    assert build_mail_account_settings(account, None) is None


def test_get_mail_provider_defaults_for_gmail_and_outlook() -> None:
    gmail = get_mail_provider_defaults("gmail")
    outlook = get_mail_provider_defaults("outlook")

    assert gmail.imap_host == "imap.gmail.com"
    assert gmail.smtp_host == "smtp.gmail.com"
    assert outlook.imap_host == "outlook.office365.com"
    assert outlook.smtp_host == "smtp.office365.com"


def test_get_mail_account_settings_reads_password_from_keyring(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        username="privatbruker",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
    )

    monkeypatch.setattr(mail_settings, "get_password", lambda _email: "hemmelig")

    settings = mail_settings.get_mail_account_settings(store, account.id)

    assert settings is not None
    assert settings.imap.password == "hemmelig"
    assert settings.smtp.password == "hemmelig"


def test_get_mail_account_settings_reads_oauth_access_token(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
    )

    monkeypatch.setattr(
        mail_settings,
        "get_oauth_tokens",
        lambda _email: OAuthTokens(access_token="access"),
    )

    settings = mail_settings.get_mail_account_settings(store, account.id)

    assert settings is not None
    assert settings.imap.auth_method == "oauth2"
    assert settings.smtp.auth_method == "oauth2"
    assert settings.imap.password == "access"
    assert settings.smtp.password == "access"


def test_get_mail_account_settings_refreshes_expired_oauth_token(
    tmp_path,
    monkeypatch,
) -> None:
    saved_tokens: list[tuple[str, OAuthTokens]] = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
    )
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    monkeypatch.setattr(
        mail_settings,
        "get_oauth_tokens",
        lambda _email: OAuthTokens(
            access_token="old-access",
            refresh_token="refresh",
            expires_at=expired_at,
        ),
    )
    monkeypatch.setattr(
        mail_settings,
        "get_oauth_client_id",
        lambda _provider: "client-id",
    )
    monkeypatch.setattr(
        mail_settings,
        "get_oauth_client_secret",
        lambda _provider: "client-secret",
    )
    monkeypatch.setattr(
        mail_settings,
        "refresh_access_token",
        lambda _config, client_id, refresh_token, client_secret=None: OAuthTokens(
            access_token=f"new-{client_id}-{client_secret}",
            refresh_token=refresh_token,
        ),
    )
    monkeypatch.setattr(
        mail_settings,
        "save_oauth_tokens",
        lambda email, tokens: saved_tokens.append((email, tokens)),
    )

    settings = mail_settings.get_mail_account_settings(store, account.id)

    assert settings is not None
    assert settings.imap.password == "new-client-id-client-secret"
    assert saved_tokens == [
        (
            "privat@example.com",
            OAuthTokens(
                access_token="new-client-id-client-secret",
                refresh_token="refresh",
            ),
        )
    ]


def test_get_mail_account_settings_uses_valid_token_when_refresh_margin_fails(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
    )
    almost_expired_at = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()

    monkeypatch.setattr(
        mail_settings,
        "get_oauth_tokens",
        lambda _email: OAuthTokens(
            access_token="still-valid",
            refresh_token="refresh",
            expires_at=almost_expired_at,
        ),
    )
    monkeypatch.setattr(
        mail_settings,
        "get_oauth_client_id",
        lambda _provider: "client-id",
    )
    monkeypatch.setattr(
        mail_settings,
        "refresh_access_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    settings = mail_settings.get_mail_account_settings(store, account.id)

    assert settings is not None
    assert settings.imap.password == "still-valid"


def test_get_mail_account_settings_returns_none_for_oauth_without_tokens(
    tmp_path,
    monkeypatch,
) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
    )

    monkeypatch.setattr(mail_settings, "get_oauth_tokens", lambda _email: None)

    assert mail_settings.get_mail_account_settings(store, account.id) is None


def test_get_mail_account_settings_returns_none_for_missing_account(tmp_path) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")

    assert mail_settings.get_mail_account_settings(store, 999) is None
