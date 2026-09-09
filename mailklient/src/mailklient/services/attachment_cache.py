"""Bound regenerable attachment data without deleting local-only mail or drafts."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass

from mailklient.database import connect
from mailklient.services.mail_store import MailStore

MAX_ATTACHMENT_CACHE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class CacheUsage:
    total_bytes: int
    removable_bytes: int


class AttachmentCache:
    def __init__(self, store: MailStore) -> None:
        self._path = store.database_path

    def usage(self) -> CacheUsage:
        with closing(connect(self._path)) as connection:
            total = connection.execute(
                "SELECT COALESCE(SUM(length(content)), 0) FROM attachments"
            ).fetchone()[0]
            removable = sum(row["bytes"] for row in connection.execute(_REMOVABLE))
        return CacheUsage(total, removable)

    def trim(
        self, max_bytes: int = MAX_ATTACHMENT_CACHE_BYTES, *, compact: bool = False
    ) -> int:
        if max_bytes < 0:
            raise ValueError("The cache limit cannot be negative.")
        with closing(connect(self._path)) as connection:
            rows = connection.execute(_REMOVABLE).fetchall()
            remaining = sum(row["bytes"] for row in rows)
            removed = 0
            with connection:
                for row in rows:
                    if remaining <= max_bytes:
                        break
                    connection.execute(
                        "UPDATE attachments SET content = NULL WHERE id = ?",
                        (row["id"],),
                    )
                    remaining -= row["bytes"]
                    removed += row["bytes"]
            if compact:
                connection.execute("VACUUM")
        return removed


_REMOVABLE = """
SELECT a.id, length(a.content) AS bytes
FROM attachments a
JOIN messages m ON m.id = a.message_id
JOIN folders f ON f.id = m.folder_id AND f.account_id = m.account_id
JOIN folder_sync_state s ON s.folder_id = f.id AND s.account_id = f.account_id
WHERE a.content IS NOT NULL AND m.imap_uid IS NOT NULL AND m.imap_uid != ''
  AND f.remote_id IS NOT NULL AND f.remote_id != '' AND s.uidvalidity IS NOT NULL
ORDER BY COALESCE(a.accessed_at, a.created_at), a.id
"""
