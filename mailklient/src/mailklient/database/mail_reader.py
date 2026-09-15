"""Read-only queries over the existing cache, with no initialization or sync."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from mailklient.database.repositories import (
    _message_from_row,
    get_message,
    list_attachments_for_message,
)
from mailklient.database.thread_index import THREAD_CTE
from mailklient.domain.errors import EmailNotFound, MailStoreUnavailable, ThreadNotFound
from mailklient.domain.folders import standard_folder_name
from mailklient.domain.mail_queries import (
    EmailDetails,
    EmailFilters,
    EmailPage,
    SortOrder,
    cached_timestamp,
    mailbox_addresses,
)
from mailklient.domain.models import Attachment


class MailReadRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path).expanduser().resolve()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(
                sqlite3.connect(self._path.as_uri() + "?mode=ro", uri=True)
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only = ON")
                connection.create_function("casefold", 1, str.casefold, deterministic=True)
                connection.create_function(
                    "mail_date", 2, cached_timestamp, deterministic=True
                )
                connection.create_function(
                    "mailbox_role",
                    1,
                    lambda value: standard_folder_name(value) if value else None,
                    deterministic=True,
                )
                connection.create_function(
                    "has_address",
                    2,
                    lambda header, address: address in mailbox_addresses(header),
                    deterministic=True,
                )
                connection.execute("BEGIN")
                yield connection
        except (sqlite3.Error, OSError):
            raise MailStoreUnavailable(
                "Local mail cache is unavailable. Check that it exists and is up to date."
            ) from None

    def search(
        self,
        query: str,
        filters: EmailFilters,
        *,
        limit: int,
        offset: int,
        sort_order: SortOrder,
        require_thread: bool = False,
    ) -> EmailPage:
        clauses = []
        params: dict[str, str | int | float | None] = {
            "limit": limit + 1,
            "offset": offset,
        }
        if query:
            clauses.append(
                "instr(casefold(m.subject || ' ' || m.sender || ' ' || m.recipients "
                "|| ' ' || m.body_preview || ' ' || m.body_text), :query) > 0"
            )
            params["query"] = query.casefold()
        for field in ("account_id", "folder_id", "is_read"):
            value = getattr(filters, field)
            if value is not None:
                clauses.append(f"m.{field} = :{field}")
                params[field] = value
        if filters.mailbox:
            clauses.append(
                "COALESCE(mailbox_role(f.remote_id), mailbox_role(f.name)) = :mailbox"
            )
            params["mailbox"] = filters.mailbox
        for field, column in (("sender", "sender"), ("recipient", "recipients")):
            value = getattr(filters, field)
            if value:
                clauses.append(f"has_address(m.{column}, :{field})")
                params[field] = value
        if filters.subject:
            clauses.append("instr(casefold(m.subject), :subject) > 0")
            params["subject"] = filters.subject.casefold()
        if filters.has_attachments is not None:
            clauses.append(
                "EXISTS(SELECT 1 FROM attachments a WHERE a.message_id = m.id) = :attachments"
            )
            params["attachments"] = filters.has_attachments
        for field, operator in (("after", ">="), ("before", "<")):
            value = getattr(filters, field)
            if value:
                clauses.append(
                    f"mail_date(m.received_at, m.sent_at) {operator} :{field}"
                )
                params[field] = cached_timestamp(value, None)
        prefix = ""
        if filters.thread_id is not None:
            prefix = THREAD_CTE
            params["thread_id"] = filters.thread_id
            clauses.append("m.id IN (SELECT id FROM thread)")
        date = "mail_date(m.received_at, m.sent_at)"
        ordering = {
            "date_desc": f"{date} IS NULL, {date} DESC, m.id DESC",
            "date_asc": f"{date} IS NULL, {date} ASC, m.id ASC",
            "sender": "casefold(m.sender), m.id ASC",
            "subject": "casefold(m.subject), m.id ASC",
        }[sort_order]
        where = " AND ".join(clauses) or "1"
        with self._connection() as connection:
            if require_thread:
                assert filters.thread_id is not None
                if (
                    connection.execute(
                        "SELECT 1 FROM messages WHERE id = ?", (filters.thread_id,)
                    ).fetchone()
                    is None
                ):
                    raise ThreadNotFound("Thread is not in the local cache.")
            rows = connection.execute(
                prefix
                + f"SELECT m.* FROM messages m JOIN folders f ON f.id = m.folder_id "
                f"WHERE {where} ORDER BY {ordering} LIMIT :limit OFFSET :offset",
                params,
            ).fetchall()
        return EmailPage(
            tuple(_message_from_row(row) for row in rows[:limit]),
            limit,
            offset,
            offset + limit if len(rows) > limit else None,
        )

    def get_email(self, email_id: int) -> EmailDetails:
        with self._connection() as connection:
            message = get_message(connection, email_id)
            if message is None:
                raise EmailNotFound("Email is not in the local cache.")
            thread_id = connection.execute(
                THREAD_CTE + "SELECT MIN(id) FROM thread", {"thread_id": email_id}
            ).fetchone()[0]
            attachments = tuple(list_attachments_for_message(connection, email_id))
            return EmailDetails(message, thread_id, attachments)

    def get_attachments(self, email_id: int) -> tuple[Attachment, ...]:
        with self._connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM messages WHERE id = ?", (email_id,)
                ).fetchone()
                is None
            ):
                raise EmailNotFound("Email is not in the local cache.")
            return tuple(list_attachments_for_message(connection, email_id))
