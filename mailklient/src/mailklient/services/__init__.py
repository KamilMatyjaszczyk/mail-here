"""Application services for the mail client."""

from mailklient.services.demo_data import seed_demo_data
from mailklient.services.mail_sync import HeaderSyncResult, MailSyncService
from mailklient.services.mail_store import MailStore
from mailklient.services.mail_settings import get_mail_account_settings
from mailklient.services.oauth_login import OAuthLoginService

__all__ = [
    "HeaderSyncResult",
    "MailStore",
    "MailSyncService",
    "OAuthLoginService",
    "get_mail_account_settings",
    "seed_demo_data",
]
