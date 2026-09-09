"""SQLite connection helpers."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

from mailklient.database.thread_index import index_message_references

DatabasePath = str | Path


def connect(database_path: DatabasePath) -> sqlite3.Connection:
    """Open a SQLite connection with project defaults."""
    path_value = str(database_path)

    if path_value != ":memory:":
        Path(path_value).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path_value)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: DatabasePath) -> None:
    """Create the initial database schema if it does not already exist."""
    with connect(database_path) as connection:
        connection.executescript(_load_schema())
        _ensure_account_server_columns(connection)
        _ensure_message_imap_columns(connection)
        _ensure_message_body_columns(connection)
        _ensure_folder_sync_state_table(connection)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(folder_sync_state)")}
        if "uidvalidity" not in columns:
            connection.execute("ALTER TABLE folder_sync_state ADD COLUMN uidvalidity INTEGER")
        _ensure_attachments_table(connection)
        _ensure_thread_metadata(connection)


def _ensure_thread_metadata(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
    for name in ("in_reply_to", "references"):
        if name not in columns:
            connection.execute(
                f'ALTER TABLE messages ADD COLUMN "{name}" TEXT NOT NULL DEFAULT \'\''
            )
    if connection.execute("SELECT 1 FROM schema_version WHERE version = 2").fetchone():
        return
    for row in connection.execute(
        'SELECT id, message_id, in_reply_to, "references" FROM messages'
    ):
        index_message_references(
            connection, row["id"], row["message_id"], row["in_reply_to"], row["references"]
        )
    connection.execute("INSERT INTO schema_version (version) VALUES (2)")


def _load_schema() -> str:
    return files("mailklient.database").joinpath("schema.sql").read_text(
        encoding="utf-8"
    )


def _ensure_account_server_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
    }
    columns = {
        "provider": "TEXT NOT NULL DEFAULT 'imap'",
        "local_certificate": "TEXT",
        "auth_method": "TEXT NOT NULL DEFAULT 'password' CHECK (auth_method IN ('password', 'oauth2'))",
        "oauth_provider": "TEXT CHECK (oauth_provider IS NULL OR oauth_provider IN ('gmail', 'outlook'))",
        "username": "TEXT",
        "imap_host": "TEXT",
        "imap_port": "INTEGER CHECK (imap_port IS NULL OR imap_port BETWEEN 1 AND 65535)",
        "imap_security": "TEXT NOT NULL DEFAULT 'ssl' CHECK (imap_security IN ('ssl', 'starttls'))",
        "smtp_host": "TEXT",
        "smtp_port": "INTEGER CHECK (smtp_port IS NULL OR smtp_port BETWEEN 1 AND 65535)",
        "smtp_security": "TEXT NOT NULL DEFAULT 'starttls' CHECK (smtp_security IN ('ssl', 'starttls'))",
    }

    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE accounts ADD COLUMN {column_name} {column_definition}"
            )


def _ensure_message_imap_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(messages)")
    }
    columns = {
        "imap_uid": "TEXT",
        "message_id": "TEXT",
        "reply_to": "TEXT NOT NULL DEFAULT ''",
        "flags": "TEXT NOT NULL DEFAULT ''",
    }

    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE messages ADD COLUMN {column_name} {column_definition}"
            )


def _ensure_message_body_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(messages)")
    }
    columns = {
        "body_text": "TEXT NOT NULL DEFAULT ''",
        "body_html": "TEXT NOT NULL DEFAULT ''",
        "body_fetch_failed": "INTEGER NOT NULL DEFAULT 0 CHECK (body_fetch_failed IN (0, 1))",
    }

    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE messages ADD COLUMN {column_name} {column_definition}"
            )

    if "body_fetch_failed" not in existing_columns:
        # Older versions stored these placeholders without remembering the failure.
        connection.execute(
            "UPDATE messages SET body_fetch_failed = 1 "
            "WHERE body_html = '' AND body_text IN (?, ?)",
            (
                "Meldingsinnholdet kunne ikke hentes. Prøv i webmail.",
                "Denne meldingen har ugyldig innhold. Åpne den i webmail.",
            ),
        )


def _ensure_folder_sync_state_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS folder_sync_state (
            account_id INTEGER NOT NULL,
            folder_id INTEGER NOT NULL,
            last_seen_uid INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (account_id, folder_id),
            FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id) REFERENCES folders (id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id, account_id) REFERENCES folders (id, account_id)
                ON DELETE CASCADE
        )
        """
    )


def _ensure_attachments_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0 CHECK (size >= 0),
            content_id TEXT,
            is_inline INTEGER NOT NULL DEFAULT 0 CHECK (is_inline IN (0, 1)),
            content BLOB,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE CASCADE
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(attachments)")
    }
    if "content" not in existing_columns:
        connection.execute("ALTER TABLE attachments ADD COLUMN content BLOB")
    if "imap_section" not in existing_columns:
        connection.execute("ALTER TABLE attachments ADD COLUMN imap_section TEXT")
    if "accessed_at" not in existing_columns:
        connection.execute("ALTER TABLE attachments ADD COLUMN accessed_at TEXT")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attachments_message_id
        ON attachments (message_id)
        """
    )
