"""Mail protocol integration package."""

from mailklient.mail.config import (
    AuthMethod,
    ImapFolder,
    ImapMessageHeader,
    ImapSettings,
    MailAccountSettings,
    MailProviderDefaults,
    SecurityMode,
    SmtpSettings,
    get_mail_provider_defaults,
)
from mailklient.mail.imap_client import ImapClient
from mailklient.mail.smtp_client import SmtpClient

__all__ = [
    "AuthMethod",
    "ImapClient",
    "ImapFolder",
    "ImapMessageHeader",
    "ImapSettings",
    "MailAccountSettings",
    "MailProviderDefaults",
    "SecurityMode",
    "SmtpClient",
    "SmtpSettings",
    "get_mail_provider_defaults",
]
