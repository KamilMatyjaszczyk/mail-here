"""Index explicit Message-ID/References links; never group by subject."""

import re
import sqlite3


def index_message_references(
    connection: sqlite3.Connection,
    message_id: int,
    rfc_message_id: str | None,
    in_reply_to: str,
    references: str,
) -> None:
    identifiers = set(
        re.findall(
            r"<[^<>\r\n]+>", " ".join((rfc_message_id or "", in_reply_to, references))
        )
    )
    connection.execute(
        "DELETE FROM message_references WHERE message_id = ?", (message_id,)
    )
    connection.executemany(
        "INSERT INTO message_references (message_id, reference_id) VALUES (?, ?)",
        [(message_id, identifier) for identifier in identifiers],
    )


THREAD_CTE = """
WITH RECURSIVE thread(id) AS (
    SELECT id FROM messages WHERE id = :thread_id
    UNION
    SELECT related.message_id
    FROM thread
    JOIN messages current ON current.id = thread.id
    JOIN message_references link ON link.message_id = thread.id
    JOIN message_references related ON related.reference_id = link.reference_id
    JOIN messages neighbor ON neighbor.id = related.message_id
        AND neighbor.account_id = current.account_id
)
"""
