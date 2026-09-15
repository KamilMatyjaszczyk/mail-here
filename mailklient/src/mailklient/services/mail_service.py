"""Programmatic, cache-only reads shared by UI and future MCP/API adapters."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from mailklient.domain.errors import InvalidSearchQuery, MailReadError
from mailklient.domain.folders import standard_folder_name
from mailklient.domain.mail_queries import (
    EmailDetails,
    EmailFilters,
    EmailPage,
    SortOrder,
    mailbox_addresses,
)
from mailklient.domain.mail_repository import MailRepository
from mailklient.domain.models import Attachment, Message

logger = logging.getLogger(__name__)
T = TypeVar("T")
MAX_PAGE_SIZE = 200


class MailService:
    """No credentials, UI prompts, network calls, downloads or write operations."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        repository: MailRepository | None = None,
    ) -> None:
        """Use an existing SQLite cache or an injected read repository.

        Exactly one source is required. Construction never initializes storage.
        """
        if (database_path is None) == (repository is None):
            raise ValueError("Provide exactly one of database_path or repository.")
        if repository is None:
            from mailklient.database.mail_reader import MailReadRepository

            assert database_path is not None
            repository = MailReadRepository(database_path)
        self._repository = repository

    def search_emails(
        self,
        query: str = "",
        filters: EmailFilters | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_order: SortOrder = "date_desc",
    ) -> EmailPage:
        return self._call(
            "search_emails",
            lambda: self._search(query, filters, limit, offset, sort_order),
        )

    def iter_emails(
        self,
        query: str = "",
        filters: EmailFilters | None = None,
        *,
        page_size: int = MAX_PAGE_SIZE,
        sort_order: SortOrder = "date_desc",
    ) -> Iterator[Message]:
        """Lazily consume search pages, preserving their order and filters.

        Validation happens on iteration. Pages are separate reads; concurrent
        cache changes can shift offsets. Use bounded search_emails for APIs.
        """
        offset = 0
        while True:
            page = self.search_emails(
                query, filters, limit=page_size, offset=offset, sort_order=sort_order
            )
            yield from page.items
            if page.next_offset is None:
                return
            offset = page.next_offset

    def get_recent_emails(
        self,
        *,
        filters: EmailFilters | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EmailPage:
        return self._call(
            "get_recent_emails",
            lambda: self._search("", filters, limit, offset, "date_desc"),
        )

    def get_unread_emails(
        self,
        *,
        filters: EmailFilters | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EmailPage:
        def read() -> EmailPage:
            normalized = _validate_filters(filters)
            if normalized.is_read is True:
                raise InvalidSearchQuery("Unread search conflicts with is_read=True.")
            return self._search(
                "", replace(normalized, is_read=False), limit, offset, "date_desc"
            )

        return self._call("get_unread_emails", read)

    def get_emails_from_sender(
        self,
        sender: str,
        *,
        filters: EmailFilters | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EmailPage:
        def read() -> EmailPage:
            normalized = _validate_filters(filters)
            address = _address(sender, "sender")
            if normalized.sender is not None and normalized.sender != address:
                raise InvalidSearchQuery("Conflicting sender filters.")
            return self._search(
                "", replace(normalized, sender=address), limit, offset, "date_desc"
            )

        return self._call("get_emails_from_sender", read)

    def get_email(self, email_id: int) -> EmailDetails:
        def read() -> EmailDetails:
            _identifier(email_id, "email_id")
            return self._repository.get_email(email_id)

        return self._call("get_email", read)

    def get_thread(
        self, thread_id: int, *, limit: int = 50, offset: int = 0
    ) -> EmailPage:
        def read() -> EmailPage:
            _identifier(thread_id, "thread_id")
            return self._search(
                "",
                EmailFilters(thread_id=thread_id),
                limit,
                offset,
                "date_asc",
                require_thread=True,
            )

        return self._call("get_thread", read)

    def get_attachments(self, email_id: int) -> tuple[Attachment, ...]:
        """Metadata only, including cached-content availability; never fetch bytes."""

        def read() -> tuple[Attachment, ...]:
            _identifier(email_id, "email_id")
            return self._repository.get_attachments(email_id)

        return self._call("get_attachments", read)

    def _search(
        self,
        query: str,
        filters: EmailFilters | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
        *,
        require_thread: bool = False,
    ) -> EmailPage:
        query = _text(query, "query", allow_empty=True)
        normalized = _validate_filters(filters)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidSearchQuery("limit must be an integer between 1 and 200.")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise InvalidSearchQuery("offset must be an integer between 0 and 1000000.")
        if not isinstance(sort_order, str) or sort_order not in (
            "date_desc",
            "date_asc",
            "sender",
            "subject",
        ):
            raise InvalidSearchQuery("Unsupported sort order.")
        return self._repository.search(
            query,
            normalized,
            limit=limit,
            offset=offset,
            sort_order=sort_order,
            require_thread=require_thread,
        )

    def _call(self, operation: str, action: Callable[[], T]) -> T:
        started = time.monotonic()
        success, count, error_type = False, 0, None
        try:
            result = action()
            count = (
                len(result.items)
                if isinstance(result, EmailPage)
                else len(result)
                if isinstance(result, tuple)
                else 1
            )
            success = True
            return result
        except MailReadError as error:
            error_type = type(error).__name__
            raise
        finally:
            # Never include arguments, exception text, identifiers or mail content.
            logger.info(
                "mail_read",
                extra={
                    "operation": operation,
                    "success": success,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "result_count": count,
                    "error_type": error_type,
                },
            )


def _identifier(value: int, field: str) -> None:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise InvalidSearchQuery(f"{field} must be a positive integer.")


def _text(value: str, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 1000 or "\x00" in value:
        raise InvalidSearchQuery(
            f"{field} must be text of at most 1000 characters without NUL."
        )
    value = value.strip()
    if not allow_empty and not value:
        raise InvalidSearchQuery(f"{field} cannot be empty.")
    return value


def _address(value: str, field: str) -> str:
    value = _text(value, field)
    addresses = mailbox_addresses(value)
    if len(addresses) != 1 or any(char in value for char in "\r\n"):
        raise InvalidSearchQuery(f"{field} must contain one email address.")
    return addresses[0]


def _date(value: str, field: str) -> str:
    value = _text(value, field)
    try:
        if len(value) == 10:
            date = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        else:
            date = datetime.fromisoformat(value)
            if date.tzinfo is None:
                raise ValueError
        return date.astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        raise InvalidSearchQuery(
            f"{field} must be an ISO date or timezone-aware timestamp."
        ) from None


def _validate_filters(filters: EmailFilters | None) -> EmailFilters:
    if filters is None:
        return EmailFilters()
    if not isinstance(filters, EmailFilters):
        raise InvalidSearchQuery("filters must be EmailFilters.")
    for field in ("account_id", "folder_id", "thread_id"):
        value = getattr(filters, field)
        if value is not None:
            _identifier(value, field)
    for field in ("is_read", "has_attachments"):
        value = getattr(filters, field)
        if value is not None and type(value) is not bool:
            raise InvalidSearchQuery(f"{field} must be a boolean.")
    changes = {}
    for field in ("sender", "recipient"):
        value = getattr(filters, field)
        if value is not None:
            changes[field] = _address(value, field)
    if filters.subject is not None:
        changes["subject"] = _text(filters.subject, "subject")
    if filters.mailbox is not None:
        role = standard_folder_name(_text(filters.mailbox, "mailbox"))
        if role is None:
            raise InvalidSearchQuery(
                "Unknown standard mailbox; use folder_id for custom folders."
            )
        changes["mailbox"] = role
    for field in ("after", "before"):
        value = getattr(filters, field)
        if value is not None:
            changes[field] = _date(value, field)
    normalized = replace(
        filters,
        sender=changes.get("sender", filters.sender),
        recipient=changes.get("recipient", filters.recipient),
        subject=changes.get("subject", filters.subject),
        mailbox=changes.get("mailbox", filters.mailbox),
        after=changes.get("after", filters.after),
        before=changes.get("before", filters.before),
    )
    if normalized.after and normalized.before and normalized.after >= normalized.before:
        raise InvalidSearchQuery("after must be earlier than before.")
    return normalized
