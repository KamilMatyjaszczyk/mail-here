"""Application service for local mail cache operations."""

from __future__ import annotations

from pathlib import Path

from mailklient.database import (
    connect,
    create_account,
    create_folder,
    create_message,
    delete_account,
    get_account,
    get_folder_by_name,
    initialize_database,
    list_accounts,
    list_folders,
    list_messages_for_folder,
    mark_message_read,
    upsert_message,
)
from mailklient.database.connection import DatabasePath
from mailklient.domain import Account, Folder, Message

DEFAULT_FOLDERS = ("Innboks", "Sendt", "Arkiv")


class MailStore:
    """Small service facade for the local SQLite cache."""

    def __init__(self, database_path: DatabasePath) -> None:
        self.database_path = Path(database_path)
        initialize_database(self.database_path)

    def add_account_with_default_folders(
        self,
        display_name: str,
        email_address: str,
        *,
        auth_method: str = "password",
        username: str | None = None,
        imap_host: str | None = None,
        imap_port: int | None = None,
        imap_security: str = "ssl",
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_security: str = "starttls",
        oauth_provider: str | None = None,
    ) -> Account:
        """Add an account and create the standard local folders."""
        account = self.add_account(
            display_name,
            email_address,
            auth_method=auth_method,
            username=username,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_security=imap_security,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_security=smtp_security,
            oauth_provider=oauth_provider,
        )

        for folder_name in DEFAULT_FOLDERS:
            self.add_folder(account.id, folder_name)

        return account

    def add_account(
        self,
        display_name: str,
        email_address: str,
        *,
        auth_method: str = "password",
        username: str | None = None,
        imap_host: str | None = None,
        imap_port: int | None = None,
        imap_security: str = "ssl",
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_security: str = "starttls",
        oauth_provider: str | None = None,
    ) -> Account:
        """Add an account to the local cache."""
        with connect(self.database_path) as connection:
            return create_account(
                connection,
                display_name,
                email_address,
                auth_method=auth_method,
                username=username,
                imap_host=imap_host,
                imap_port=imap_port,
                imap_security=imap_security,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_security=smtp_security,
                oauth_provider=oauth_provider,
            )

    def list_accounts(self) -> list[Account]:
        """List accounts from the local cache."""
        with connect(self.database_path) as connection:
            return list_accounts(connection)

    def get_account(self, account_id: int) -> Account | None:
        """Return one account from the local cache."""
        with connect(self.database_path) as connection:
            return get_account(connection, account_id)

    def delete_account(self, account_id: int) -> bool:
        """Delete an account and its local cached data."""
        with connect(self.database_path) as connection:
            return delete_account(connection, account_id)

    def add_folder(
        self,
        account_id: int,
        name: str,
        remote_id: str | None = None,
    ) -> Folder:
        """Add a folder to the local cache."""
        with connect(self.database_path) as connection:
            return create_folder(connection, account_id, name, remote_id)

    def get_or_add_folder(
        self,
        account_id: int,
        name: str,
        remote_id: str | None = None,
    ) -> Folder:
        """Return a folder, creating it if needed."""
        with connect(self.database_path) as connection:
            folder = get_folder_by_name(connection, account_id, name)
            if folder is not None:
                return folder
            return create_folder(connection, account_id, name, remote_id)

    def list_folders(self, account_id: int) -> list[Folder]:
        """List folders for one account."""
        with connect(self.database_path) as connection:
            return list_folders(connection, account_id)

    def add_message(
        self,
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
        """Add message metadata to the local cache."""
        with connect(self.database_path) as connection:
            return create_message(
                connection,
                account_id,
                folder_id,
                imap_uid=imap_uid,
                flags=flags,
                message_id=message_id,
                subject=subject,
                sender=sender,
                recipients=recipients,
                sent_at=sent_at,
                received_at=received_at,
                is_read=is_read,
                body_preview=body_preview,
            )

    def save_message_metadata(
        self,
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
        """Create or update message metadata in the local cache."""
        with connect(self.database_path) as connection:
            return upsert_message(
                connection,
                account_id,
                folder_id,
                imap_uid=imap_uid,
                flags=flags,
                message_id=message_id,
                subject=subject,
                sender=sender,
                recipients=recipients,
                sent_at=sent_at,
                received_at=received_at,
                is_read=is_read,
                body_preview=body_preview,
            )

    def list_messages(self, account_id: int, folder_id: int) -> list[Message]:
        """List message metadata for one folder."""
        with connect(self.database_path) as connection:
            return list_messages_for_folder(connection, account_id, folder_id)

    def mark_message_read(self, message_id: int, is_read: bool = True) -> None:
        """Update the read state for one cached message."""
        with connect(self.database_path) as connection:
            mark_message_read(connection, message_id, is_read)
