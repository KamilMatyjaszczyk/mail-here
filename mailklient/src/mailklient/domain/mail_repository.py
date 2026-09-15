"""Storage contract for cache-only mail reads, independent of Qt and SQLite."""

from typing import Protocol

from mailklient.domain.mail_queries import EmailDetails, EmailFilters, EmailPage, SortOrder
from mailklient.domain.models import Attachment


class MailRepository(Protocol):
    """Read normalized requests without writes, authentication or network calls.

    Implementations own ordering, account/folder scope and thread resolution.
    Search returns at most limit items and a strictly increasing next_offset,
    or None at the end. Translate storage failures to MailStoreUnavailable;
    missing details/attachments raise EmailNotFound and a required missing
    thread raises ThreadNotFound. Ordinary searches may return empty pages.
    """

    def search(
        self,
        query: str,
        filters: EmailFilters,
        *,
        limit: int,
        offset: int,
        sort_order: SortOrder,
        require_thread: bool = False,
    ) -> EmailPage: ...

    def get_email(self, email_id: int) -> EmailDetails: ...

    def get_attachments(self, email_id: int) -> tuple[Attachment, ...]: ...
