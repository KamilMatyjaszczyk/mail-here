"""Application services for the mail client."""

from mailklient.services.demo_data import seed_demo_data
from mailklient.services.mail_read import MailReadService
from mailklient.services.mail_service import MailService
from mailklient.services.mail_send import ComposeDraft, MailSendService, SendResult
from mailklient.services.mail_sync import (
    CORE_SYNC_FOLDER_NAMES,
    HeaderSyncResult,
    MailSyncService,
)
from mailklient.services.mail_store import MailStore
from mailklient.services.mail_settings import get_mail_account_settings
from mailklient.services.oauth_login import OAuthLoginService

__all__ = [
    "ComposeDraft",
    "CORE_SYNC_FOLDER_NAMES",
    "HeaderSyncResult",
    "MailSendService",
    "MailReadService",
    "MailService",
    "MailStore",
    "MailSyncService",
    "OAuthLoginService",
    "SendResult",
    "get_mail_account_settings",
    "seed_demo_data",
]
