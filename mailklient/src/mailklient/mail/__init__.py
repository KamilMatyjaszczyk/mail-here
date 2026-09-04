"""Mail protocol integration package."""

from mailklient.mail.config import (
    AuthMethod,
    ImapAttachment,
    ImapFolder,
    ImapMessageFlags,
    ImapMessageHeader,
    ImapSettings,
    MailAccountSettings,
    MailProviderDefaults,
    SecurityMode,
    SmtpSettings,
    get_mail_provider_defaults,
)
from mailklient.mail.imap_client import ImapClient
from mailklient.mail.smtp_client import SmtpClient, build_email_message

__all__ = [
    "AuthMethod",
    "ImapClient",
    "ImapAttachment",
    "ImapFolder",
    "ImapMessageFlags",
    "ImapMessageHeader",
    "ImapSettings",
    "MailAccountSettings",
    "MailProviderDefaults",
    "SecurityMode",
    "SmtpClient",
    "SmtpSettings",
    "build_email_message",
    "get_mail_provider_defaults",
]
