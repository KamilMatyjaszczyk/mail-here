"""Repository functions for the local SQLite cache."""

from __future__ import annotations

import sqlite3

from mailklient.domain import Account, Folder, Message


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
                body_preview = ?
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
            body_preview
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            body_preview
        FROM messages
        WHERE id = last_insert_rowid()
        """,
    ).fetchone()

    return _message_from_row(row)


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
            body_preview
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            body_preview
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
            body_preview
        FROM messages
        WHERE account_id = ? AND folder_id = ?
        ORDER BY received_at DESC, sent_at DESC, id DESC
        """,
        (account_id, folder_id),
    ).fetchall()

    return [_message_from_row(row) for row in rows]


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
                body_preview
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
                body_preview
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
            body_preview
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
    )
