"""UI-independent read contracts and mail header parsing helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import Literal

from mailklient.domain.models import Attachment, Message

SortOrder = Literal["date_desc", "date_asc", "sender", "subject"]


@dataclass(frozen=True, slots=True)
class EmailFilters:
    account_id: int | None = None
    folder_id: int | None = None
    mailbox: str | None = None
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    is_read: bool | None = None
    after: str | None = None
    before: str | None = None
    has_attachments: bool | None = None
    thread_id: int | None = None


@dataclass(frozen=True, slots=True)
class EmailPage:
    """One bounded page from the local cache, not the entire remote mailbox."""

    items: tuple[Message, ...]
    limit: int
    offset: int
    next_offset: int | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EmailDetails:
    message: Message
    thread_id: int
    attachments: tuple[Attachment, ...]

    def to_dict(self) -> dict:
        return {**asdict(self), "has_attachments": bool(self.attachments)}


def mailbox_addresses(header: str) -> tuple[str, ...]:
    """Parse exact mailbox addresses, never match a display name as an address."""
    return tuple(
        address.casefold()
        for _name, address in getaddresses([header])
        if address and "@" in address
    )


def cached_timestamp(received_at: str | None, sent_at: str | None) -> float | None:
    """Normalize ISO/RFC mail dates; old timezone-less cache dates mean UTC."""
    for value in (received_at, sent_at):
        if not value:
            continue
        try:
            try:
                date = datetime.fromisoformat(value)
            except ValueError:
                date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return date.timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
    return None
