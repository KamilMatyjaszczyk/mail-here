"""SQLite connection helpers."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

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


def _load_schema() -> str:
    return files("mailklient.database").joinpath("schema.sql").read_text(
        encoding="utf-8"
    )


def _ensure_account_server_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
    }
    columns = {
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
        "flags": "TEXT NOT NULL DEFAULT ''",
    }

    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE messages ADD COLUMN {column_name} {column_definition}"
            )
