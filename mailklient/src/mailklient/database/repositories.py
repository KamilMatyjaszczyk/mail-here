"""Repository functions for the local SQLite cache."""

from __future__ import annotations

import sqlite3

from mailklient.domain import Account, Attachment, Folder, Message


def create_account(
    connection: sqlite3.Connection,
    display_name: str,
    email_address: str,
    username: str | None = None,
    imap_host: str | None = None,
    imap_port: int | None = None,
    imap_security: str = "ssl",
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_security: str = "starttls",
    auth_method: str = "password",
    oauth_provider: str | None = None,
) -> Account:
    """Create and return an account."""
    cursor = connection.execute(
        """
        INSERT INTO accounts (
            display_name,
            email_address,
            auth_method,
            oauth_provider,
            username,
            imap_host,
            imap_port,
            imap_security,
            smtp_host,
            smtp_port,
            smtp_security
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            display_name,
            email_address,
            auth_method,
            oauth_provider,
            username,
            imap_host,
            imap_port,
            imap_security,
            smtp_host,
            smtp_port,
            smtp_security,
        ),
    )
    connection.commit()

    row = connection.execute(
        """
        SELECT
            id,
            display_name,
            email_address,
            auth_method,
            oauth_provider,
            username,
            imap_host,
            imap_port,
            imap_security,
            smtp_host,
            smtp_port,
            smtp_security
        FROM accounts
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    return _account_from_row(row)


def list_accounts(connection: sqlite3.Connection) -> list[Account]:
    """Return all accounts ordered by display name."""
    rows = connection.execute(
        """
        SELECT
            id,
            display_name,
            email_address,
            auth_method,
            oauth_provider,
            username,
            imap_host,
            imap_port,
            imap_security,
            smtp_host,
            smtp_port,
            smtp_security
        FROM accounts
        ORDER BY display_name COLLATE NOCASE, email_address COLLATE NOCASE
        """
    ).fetchall()

    return [_account_from_row(row) for row in rows]


def get_account(connection: sqlite3.Connection, account_id: int) -> Account | None:
    """Return one account by id, or None if it does not exist."""
    row = connection.execute(
        """
        SELECT
            id,
            display_name,
            email_address,
            auth_method,
            oauth_provider,
            username,
            imap_host,
            imap_port,
            imap_security,
            smtp_host,
            smtp_port,
            smtp_security
        FROM accounts
        WHERE id = ?
        """,
        (account_id,),
    ).fetchone()

    if row is None:
        return None

    return _account_from_row(row)


def delete_account(connection: sqlite3.Connection, account_id: int) -> bool:
    """Delete an account and its cached folders/messages."""
    cursor = connection.execute(
        """
        DELETE FROM accounts
        WHERE id = ?
        """,
        (account_id,),
    )
    connection.commit()
    return cursor.rowcount > 0


def create_folder(
    connection: sqlite3.Connection,
    account_id: int,
    name: str,
    remote_id: str | None = None,
) -> Folder:
    """Create and return a folder for an account."""
    cursor = connection.execute(
        """
        INSERT INTO folders (account_id, name, remote_id)
        VALUES (?, ?, ?)
        """,
        (account_id, name, remote_id),
    )
    connection.commit()

    row = connection.execute(
        """
        SELECT id, account_id, name, remote_id
        FROM folders
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    return _folder_from_row(row)


def update_folder_remote_id(
    connection: sqlite3.Connection,
    folder_id: int,
    remote_id: str,
) -> Folder:
    """Update the remote IMAP folder id for one local folder."""
    connection.execute(
        """
        UPDATE folders
        SET remote_id = ?
        WHERE id = ?
        """,
        (remote_id, folder_id),
    )
    connection.commit()

    row = connection.execute(
        """
        SELECT id, account_id, name, remote_id
        FROM folders
        WHERE id = ?
        """,
        (folder_id,),
    ).fetchone()

    return _folder_from_row(row)


def get_folder_by_name(
    connection: sqlite3.Connection,
    account_id: int,
    name: str,
) -> Folder | None:
    """Return one folder by account and name."""
    row = connection.execute(
        """
        SELECT id, account_id, name, remote_id
        FROM folders
        WHERE account_id = ? AND name = ?
        """,
        (account_id, name),
    ).fetchone()

    if row is None:
        return None

    return _folder_from_row(row)


def get_folder(
    connection: sqlite3.Connection,
    folder_id: int,
) -> Folder | None:
    """Return one folder by id, or None if it does not exist."""
    row = connection.execute(
        """
        SELECT id, account_id, name, remote_id
        FROM folders
        WHERE id = ?
        """,
        (folder_id,),
    ).fetchone()

    if row is None:
        return None

    return _folder_from_row(row)


def list_folders(connection: sqlite3.Connection, account_id: int) -> list[Folder]:
    """Return folders for one account ordered by name."""
    rows = connection.execute(
        """
        SELECT id, account_id, name, remote_id
        FROM folders
        WHERE account_id = ?
        ORDER BY name COLLATE NOCASE
        """,
        (account_id,),
    ).fetchall()

    return [_folder_from_row(row) for row in rows]


def upsert_message(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
    *,
    imap_uid: str | None = None,
    flags: str = "",
    message_id: str | None = None,
    subject: str = "",
    sender: str = "",
    recipients: str = "",
    sent_at: str | None = None,
    received_at: str | None = None,
    is_read: bool = False,
    body_preview: str = "",
    body_text: str = "",
    body_html: str = "",
) -> Message:
    """Create or update message metadata by IMAP UID or message id."""
    existing_message = _find_existing_message(
        connection,
        account_id,
        folder_id,
        imap_uid=imap_uid,
        message_id=message_id,
    )

    if existing_message is not None:
        connection.execute(
            """
            UPDATE messages
            SET
                imap_uid = ?,
                flags = ?,
                message_id = ?,
                subject = ?,
                sender = ?,
                recipients = ?,
                sent_at = ?,
                received_at = ?,
                is_read = ?,
                body_preview = ?,
                body_text = ?,
                body_html = ?
            WHERE id = ?
            """,
            (
                imap_uid,
                flags,
                message_id,
                subject,
                sender,
                recipients,
                sent_at,
                received_at,
                int(is_read),
                body_preview,
                body_text,
                body_html,
                existing_message.id,
            ),
        )
        connection.commit()
        return _get_message(connection, existing_message.id)

    connection.execute(
        """
        INSERT INTO messages (
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            int(is_read),
            body_preview,
            body_text,
            body_html,
        ),
    )
    connection.commit()

    row = connection.execute(
        """
        SELECT
            id,
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        FROM messages
        WHERE id = last_insert_rowid()
        """,
    ).fetchone()

    return _message_from_row(row)


def get_message(
    connection: sqlite3.Connection,
    message_id: int,
) -> Message | None:
    """Return one cached message by id, or None if it does not exist."""
    row = connection.execute(
        """
        SELECT
            id,
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        FROM messages
        WHERE id = ?
        """,
        (message_id,),
    ).fetchone()

    if row is None:
        return None

    return _message_from_row(row)


def list_attachments_for_message(
    connection: sqlite3.Connection,
    message_id: int,
) -> list[Attachment]:
    """Return attachment metadata for one message."""
    rows = connection.execute(
        """
        SELECT
            id,
            message_id,
            filename,
            content_type,
            size,
            content_id,
            is_inline,
            content IS NOT NULL AS has_content
        FROM attachments
        WHERE message_id = ?
        ORDER BY id
        """,
        (message_id,),
    ).fetchall()

    return [_attachment_from_row(row) for row in rows]


def replace_message_attachments(
    connection: sqlite3.Connection,
    message_id: int,
    attachments: list[Attachment],
) -> None:
    """Replace all cached attachment metadata for one message."""
    existing_content = {
        attachment.id: get_attachment_content(connection, attachment.id)
        for attachment in list_attachments_for_message(connection, message_id)
    }
    connection.execute(
        """
        DELETE FROM attachments
        WHERE message_id = ?
        """,
        (message_id,),
    )
    connection.executemany(
        """
        INSERT INTO attachments (
            message_id,
            filename,
            content_type,
            size,
            content_id,
            is_inline,
            content
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                message_id,
                attachment.filename,
                attachment.content_type,
                attachment.size,
                attachment.content_id,
                int(attachment.is_inline),
                attachment.content
                if attachment.content is not None
                else existing_content.get(attachment.id),
            )
            for attachment in attachments
        ],
    )
    connection.commit()


def get_attachment_content(
    connection: sqlite3.Connection,
    attachment_id: int,
) -> bytes | None:
    """Return attachment bytes from the local cache."""
    row = connection.execute(
        """
        SELECT content
        FROM attachments
        WHERE id = ?
        """,
        (attachment_id,),
    ).fetchone()

    if row is None:
        return None
    return row["content"]


def set_attachment_content(
    connection: sqlite3.Connection,
    attachment_id: int,
    content: bytes,
) -> None:
    """Store attachment bytes in the local cache."""
    connection.execute(
        """
        UPDATE attachments
        SET content = ?, size = ?
        WHERE id = ?
        """,
        (content, len(content), attachment_id),
    )
    connection.commit()


def create_message(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
    *,
    imap_uid: str | None = None,
    flags: str = "",
    message_id: str | None = None,
    subject: str = "",
    sender: str = "",
    recipients: str = "",
    sent_at: str | None = None,
    received_at: str | None = None,
    is_read: bool = False,
    body_preview: str = "",
    body_text: str = "",
    body_html: str = "",
) -> Message:
    """Create and return message metadata."""
    cursor = connection.execute(
        """
        INSERT INTO messages (
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            int(is_read),
            body_preview,
            body_text,
            body_html,
        ),
    )
    connection.commit()

    row = connection.execute(
        """
        SELECT
            id,
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        FROM messages
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    return _message_from_row(row)


def list_messages_for_folder(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
) -> list[Message]:
    """Return message metadata for one folder, newest first."""
    rows = connection.execute(
        """
        SELECT
            id,
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        FROM messages
        WHERE account_id = ? AND folder_id = ?
        ORDER BY received_at DESC, sent_at DESC, id DESC
        """,
        (account_id, folder_id),
    ).fetchall()

    return [_message_from_row(row) for row in rows]


def list_unified_inbox_messages(
    connection: sqlite3.Connection,
    limit: int = 100,
) -> list[Message]:
    """Return inbox messages across all accounts, newest first."""
    rows = connection.execute(
        """
        SELECT
            messages.id,
            messages.account_id,
            messages.folder_id,
            messages.imap_uid,
            messages.flags,
            messages.message_id,
            messages.subject,
            messages.sender,
            messages.recipients,
            messages.sent_at,
            messages.received_at,
            messages.is_read,
            messages.body_preview,
            messages.body_text,
            messages.body_html
        FROM messages
        JOIN folders ON folders.id = messages.folder_id
        WHERE folders.name COLLATE NOCASE IN ('INBOX', 'Innboks')
            OR folders.remote_id COLLATE NOCASE = 'INBOX'
        ORDER BY messages.received_at DESC, messages.sent_at DESC, messages.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [_message_from_row(row) for row in rows]


def count_messages_for_folder(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
) -> int:
    """Return how many messages are cached for one folder."""
    row = connection.execute(
        """
        SELECT COUNT(*) AS message_count
        FROM messages
        WHERE account_id = ? AND folder_id = ?
        """,
        (account_id, folder_id),
    ).fetchone()
    return int(row["message_count"]) if row is not None else 0


def update_message_flags(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
    imap_uid: str,
    flags: str,
    is_read: bool,
) -> bool:
    """Update cached flags for one IMAP message."""
    cursor = connection.execute(
        """
        UPDATE messages
        SET flags = ?, is_read = ?
        WHERE account_id = ?
            AND folder_id = ?
            AND imap_uid = ?
        """,
        (flags, int(is_read), account_id, folder_id, imap_uid),
    )
    connection.commit()
    return cursor.rowcount > 0


def get_folder_last_seen_uid(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
) -> int:
    """Return the highest IMAP UID synced for a folder."""
    row = connection.execute(
        """
        SELECT last_seen_uid
        FROM folder_sync_state
        WHERE account_id = ? AND folder_id = ?
        """,
        (account_id, folder_id),
    ).fetchone()

    if row is not None:
        return int(row["last_seen_uid"])

    row = connection.execute(
        """
        SELECT MAX(CAST(imap_uid AS INTEGER)) AS last_seen_uid
        FROM messages
        WHERE account_id = ?
            AND folder_id = ?
            AND imap_uid != ''
            AND imap_uid NOT GLOB '*[^0-9]*'
        """,
        (account_id, folder_id),
    ).fetchone()

    if row is None or row["last_seen_uid"] is None:
        return 0
    return int(row["last_seen_uid"])


def update_folder_last_seen_uid(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
    last_seen_uid: int,
) -> None:
    """Persist the highest IMAP UID synced for a folder."""
    connection.execute(
        """
        INSERT INTO folder_sync_state (
            account_id,
            folder_id,
            last_seen_uid
        )
        VALUES (?, ?, ?)
        ON CONFLICT(account_id, folder_id) DO UPDATE SET
            last_seen_uid = MAX(folder_sync_state.last_seen_uid, excluded.last_seen_uid),
            updated_at = CURRENT_TIMESTAMP
        """,
        (account_id, folder_id, last_seen_uid),
    )
    connection.commit()


def mark_message_read(
    connection: sqlite3.Connection,
    message_id: int,
    is_read: bool = True,
) -> None:
    """Update the read state for one cached message."""
    connection.execute(
        """
        UPDATE messages
        SET is_read = ?
        WHERE id = ?
        """,
        (int(is_read), message_id),
    )
    connection.commit()


def move_message_to_folder(
    connection: sqlite3.Connection,
    message_id: int,
    destination_folder_id: int,
) -> bool:
    """Move one cached message to another local folder."""
    cursor = connection.execute(
        """
        UPDATE messages
        SET folder_id = ?
        WHERE id = ?
        """,
        (destination_folder_id, message_id),
    )
    connection.commit()
    return cursor.rowcount > 0


def delete_message(connection: sqlite3.Connection, message_id: int) -> bool:
    """Delete one cached message."""
    cursor = connection.execute(
        """
        DELETE FROM messages
        WHERE id = ?
        """,
        (message_id,),
    )
    connection.commit()
    return cursor.rowcount > 0


def delete_messages_missing_from_folder(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
    remote_uids: list[str],
) -> int:
    """Delete cached IMAP messages that no longer exist in a folder."""
    if remote_uids:
        placeholders = ", ".join("?" for _uid in remote_uids)
        cursor = connection.execute(
            f"""
            DELETE FROM messages
            WHERE account_id = ?
                AND folder_id = ?
                AND imap_uid IS NOT NULL
                AND imap_uid != ''
                AND imap_uid NOT IN ({placeholders})
            """,
            (account_id, folder_id, *remote_uids),
        )
    else:
        cursor = connection.execute(
            """
            DELETE FROM messages
            WHERE account_id = ?
                AND folder_id = ?
                AND imap_uid IS NOT NULL
                AND imap_uid != ''
            """,
            (account_id, folder_id),
        )

    connection.commit()
    return cursor.rowcount


def _find_existing_message(
    connection: sqlite3.Connection,
    account_id: int,
    folder_id: int,
    *,
    imap_uid: str | None,
    message_id: str | None,
) -> Message | None:
    if imap_uid:
        row = connection.execute(
            """
            SELECT
                id,
                account_id,
                folder_id,
                imap_uid,
                flags,
                message_id,
                subject,
                sender,
                recipients,
                sent_at,
                received_at,
                is_read,
                body_preview,
                body_text,
                body_html
            FROM messages
            WHERE account_id = ? AND folder_id = ? AND imap_uid = ?
            """,
            (account_id, folder_id, imap_uid),
        ).fetchone()
        if row is not None:
            return _message_from_row(row)

    if message_id:
        row = connection.execute(
            """
            SELECT
                id,
                account_id,
                folder_id,
                imap_uid,
                flags,
                message_id,
                subject,
                sender,
                recipients,
                sent_at,
                received_at,
                is_read,
                body_preview,
                body_text,
                body_html
            FROM messages
            WHERE account_id = ? AND folder_id = ? AND message_id = ?
            """,
            (account_id, folder_id, message_id),
        ).fetchone()
        if row is not None:
            return _message_from_row(row)

    return None


def _get_message(connection: sqlite3.Connection, message_id: int) -> Message:
    row = connection.execute(
        """
        SELECT
            id,
            account_id,
            folder_id,
            imap_uid,
            flags,
            message_id,
            subject,
            sender,
            recipients,
            sent_at,
            received_at,
            is_read,
            body_preview,
            body_text,
            body_html
        FROM messages
        WHERE id = ?
        """,
        (message_id,),
    ).fetchone()

    return _message_from_row(row)


def _account_from_row(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        display_name=row["display_name"],
        email_address=row["email_address"],
        username=row["username"],
        imap_host=row["imap_host"],
        imap_port=row["imap_port"],
        imap_security=row["imap_security"],
        smtp_host=row["smtp_host"],
        smtp_port=row["smtp_port"],
        smtp_security=row["smtp_security"],
        auth_method=row["auth_method"],
        oauth_provider=row["oauth_provider"],
    )


def _folder_from_row(row: sqlite3.Row) -> Folder:
    return Folder(
        id=row["id"],
        account_id=row["account_id"],
        name=row["name"],
        remote_id=row["remote_id"],
    )


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        account_id=row["account_id"],
        folder_id=row["folder_id"],
        imap_uid=row["imap_uid"],
        flags=row["flags"],
        message_id=row["message_id"],
        subject=row["subject"],
        sender=row["sender"],
        recipients=row["recipients"],
        sent_at=row["sent_at"],
        received_at=row["received_at"],
        is_read=bool(row["is_read"]),
        body_preview=row["body_preview"],
        body_text=row["body_text"],
        body_html=row["body_html"],
    )


def _attachment_from_row(row: sqlite3.Row) -> Attachment:
    return Attachment(
        id=row["id"],
        message_id=row["message_id"],
        filename=row["filename"],
        content_type=row["content_type"],
        size=row["size"],
        content_id=row["content_id"],
        is_inline=bool(row["is_inline"]),
        has_content=bool(row["has_content"]),
    )
