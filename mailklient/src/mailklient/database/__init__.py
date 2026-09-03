"""Database package for local storage."""

from mailklient.database.connection import connect, initialize_database
from mailklient.database.repositories import (
    create_account,
    create_folder,
    create_message,
    delete_account,
    get_account,
    get_folder_by_name,
    list_accounts,
    list_folders,
    list_messages_for_folder,
    mark_message_read,
    upsert_message,
)

__all__ = [
    "connect",
    "create_account",
    "create_folder",
    "create_message",
    "delete_account",
    "get_account",
    "get_folder_by_name",
    "initialize_database",
    "list_accounts",
    "list_folders",
    "list_messages_for_folder",
    "mark_message_read",
    "upsert_message",
]
