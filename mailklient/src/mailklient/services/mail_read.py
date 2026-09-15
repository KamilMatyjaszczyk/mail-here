"""Compatibility imports for the original read-service name."""

from mailklient.services.mail_service import MAX_PAGE_SIZE, MailService

MailReadService = MailService

__all__ = ["MAX_PAGE_SIZE", "MailReadService"]
