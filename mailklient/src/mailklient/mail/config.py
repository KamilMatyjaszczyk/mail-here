"""Mail protocol configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mailklient.domain import Account

AuthMethod = Literal["password", "oauth2"]
SecurityMode = Literal["ssl", "starttls"]


@dataclass(frozen=True, slots=True)
class ImapSettings:
    """Settings needed to connect to an IMAP server later."""

    host: str
    port: int
    username: str
    password: str
    security: SecurityMode = "ssl"
    auth_method: AuthMethod = "password"


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """Settings needed to connect to an SMTP server later."""

    host: str
    port: int
    username: str
    password: str
    security: SecurityMode = "starttls"
    auth_method: AuthMethod = "password"


@dataclass(frozen=True, slots=True)
class MailAccountSettings:
    """Complete mail settings for one account."""

    account_id: int
    email_address: str
    imap: ImapSettings
    smtp: SmtpSettings


@dataclass(frozen=True, slots=True)
class MailProviderDefaults:
    """Default IMAP/SMTP settings for a well-known provider."""

    imap_host: str
    imap_port: int
    imap_security: SecurityMode
    smtp_host: str
    smtp_port: int
    smtp_security: SecurityMode


@dataclass(frozen=True, slots=True)
class ImapFolder:
    """A folder reported by an IMAP server."""

    name: str


@dataclass(frozen=True, slots=True)
class ImapMessageHeader:
    """Message metadata fetched from an IMAP server."""

    uid: str
    flags: tuple[str, ...] = ()
    message_id: str | None = None
    subject: str = ""
    sender: str = ""
    recipients: str = ""
    date: str | None = None


MAIL_PROVIDER_DEFAULTS = {
    "gmail": MailProviderDefaults(
        imap_host="imap.gmail.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_security="starttls",
    ),
    "outlook": MailProviderDefaults(
        imap_host="outlook.office365.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.office365.com",
        smtp_port=587,
        smtp_security="starttls",
    ),
}


def get_mail_provider_defaults(provider: str) -> MailProviderDefaults:
    """Return default IMAP/SMTP settings for a known provider."""
    return MAIL_PROVIDER_DEFAULTS[provider]


def build_mail_account_settings(
    account: Account,
    password: str | None,
) -> MailAccountSettings | None:
    """Build mail settings when all required local values are available."""
    username = account.username or account.email_address

    if not (
        password
        and account.imap_host
        and account.imap_port
        and account.smtp_host
        and account.smtp_port
    ):
        return None

    return MailAccountSettings(
        account_id=account.id,
        email_address=account.email_address,
        imap=ImapSettings(
            host=account.imap_host,
            port=account.imap_port,
            username=username,
            password=password,
            security=account.imap_security,
            auth_method=account.auth_method,
        ),
        smtp=SmtpSettings(
            host=account.smtp_host,
            port=account.smtp_port,
            username=username,
            password=password,
            security=account.smtp_security,
            auth_method=account.auth_method,
        ),
    )
