from __future__ import annotations

import sqlite3

from mailklient.database import connect, initialize_database


def test_initialize_database_creates_expected_tables(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"

    initialize_database(database_path)

    with connect(database_path) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

    assert {
        "schema_version",
        "accounts",
        "folders",
        "messages",
        "folder_sync_state",
        "attachments",
    }.issubset(tables)


def test_connect_enables_foreign_keys(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"

    with connect(database_path) as connection:
        foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys_enabled == 1


def test_schema_rejects_folder_without_account(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        try:
            connection.execute(
                "INSERT INTO folders (account_id, name) VALUES (?, ?)",
                (999, "Inbox"),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Expected foreign key constraint to fail")


def test_initialize_database_adds_account_server_columns_to_existing_database(
    tmp_path,
) -> None:
    database_path = tmp_path / "mailklient.sqlite3"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                email_address TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    initialize_database(database_path)

    with connect(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }

    assert {
        "auth_method",
        "oauth_provider",
        "username",
        "imap_host",
        "imap_port",
        "imap_security",
        "smtp_host",
        "smtp_port",
        "smtp_security",
    }.issubset(columns)


def test_accounts_table_does_not_store_passwords(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }

    assert "password" not in columns
    assert "access_token" not in columns
    assert "refresh_token" not in columns


def test_messages_table_has_imap_uid_and_flags(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(messages)")
        }

    assert {"imap_uid", "flags"}.issubset(columns)


def test_messages_table_has_body_columns(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(messages)")
        }

    assert {"body_text", "body_html"}.issubset(columns)


def test_attachments_table_has_content_column(tmp_path) -> None:
    database_path = tmp_path / "mailklient.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(attachments)")
        }

    assert "content" in columns


def test_initialize_database_adds_folder_sync_state_to_existing_database(
    tmp_path,
) -> None:
    database_path = tmp_path / "mailklient.sqlite3"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                email_address TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                remote_id TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                folder_id INTEGER NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                recipients TEXT NOT NULL DEFAULT '',
                sent_at TEXT,
                received_at TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                body_preview TEXT NOT NULL DEFAULT ''
            )
            """
        )

    initialize_database(database_path)

    with connect(database_path) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

    assert "folder_sync_state" in tables
