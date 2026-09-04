"""Application service for local mail cache operations."""

from __future__ import annotations

from pathlib import Path

from mailklient.database import (
    connect,
    count_messages_for_folder,
    create_account,
    create_folder,
    create_message,
    delete_account,
    delete_message,
    delete_messages_missing_from_folder,
    get_account,
    get_folder,
    get_folder_by_name,
    get_folder_last_seen_uid,
    get_attachment_content,
    get_message,
    list_attachments_for_message,
    initialize_database,
    list_accounts,
    list_folders,
    list_messages_for_folder,
    list_unified_inbox_messages,
    mark_message_read,
    move_message_to_folder,
    replace_message_attachments,
    set_attachment_content,
    update_folder_last_seen_uid,
    update_folder_remote_id,
    update_message_flags,
    upsert_message,
)
from mailklient.database.connection import DatabasePath
from mailklient.domain import Account, Attachment, Folder, Message

DEFAULT_FOLDERS = ("Innboks", "Sendt", "Søppelpost", "Papirkurv")


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
                if remote_id and folder.remote_id != remote_id:
                    return update_folder_remote_id(connection, folder.id, remote_id)
                return folder
            for folder in list_folders(connection, account_id):
                if _folder_names_match(folder.name, name, remote_id):
                    if remote_id and folder.remote_id != remote_id:
                        return update_folder_remote_id(
                            connection,
                            folder.id,
                            remote_id,
                        )
                    return folder
            return create_folder(connection, account_id, name, remote_id)

    def list_folders(self, account_id: int) -> list[Folder]:
        """List folders for one account."""
        with connect(self.database_path) as connection:
            return list_folders(connection, account_id)

    def get_folder(self, folder_id: int) -> Folder | None:
        """Return one folder from the local cache."""
        with connect(self.database_path) as connection:
            return get_folder(connection, folder_id)

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
        body_text: str = "",
        body_html: str = "",
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
                body_text=body_text,
                body_html=body_html,
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
        body_text: str = "",
        body_html: str = "",
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
                body_text=body_text,
                body_html=body_html,
            )

    def list_messages(self, account_id: int, folder_id: int) -> list[Message]:
        """List message metadata for one folder."""
        with connect(self.database_path) as connection:
            return list_messages_for_folder(connection, account_id, folder_id)

    def get_message(self, message_id: int) -> Message | None:
        """Return one cached message."""
        with connect(self.database_path) as connection:
            return get_message(connection, message_id)

    def list_attachments(self, message_id: int) -> list[Attachment]:
        """List attachment metadata for one cached message."""
        with connect(self.database_path) as connection:
            return list_attachments_for_message(connection, message_id)

    def get_attachment_content(self, attachment_id: int) -> bytes | None:
        """Return cached bytes for one attachment."""
        with connect(self.database_path) as connection:
            return get_attachment_content(connection, attachment_id)

    def set_attachment_content(self, attachment_id: int, content: bytes) -> None:
        """Store cached bytes for one attachment."""
        with connect(self.database_path) as connection:
            set_attachment_content(connection, attachment_id, content)

    def replace_message_attachments(
        self,
        message_id: int,
        attachments: list[Attachment],
    ) -> None:
        """Replace attachment metadata for one cached message."""
        with connect(self.database_path) as connection:
            replace_message_attachments(connection, message_id, attachments)

    def list_unified_inbox_messages(self, limit: int = 100) -> list[Message]:
        """List inbox message metadata across all accounts."""
        with connect(self.database_path) as connection:
            return list_unified_inbox_messages(connection, limit)

    def count_messages(self, account_id: int, folder_id: int) -> int:
        """Return how many messages are cached in one folder."""
        with connect(self.database_path) as connection:
            return count_messages_for_folder(connection, account_id, folder_id)

    def get_folder_last_seen_uid(self, account_id: int, folder_id: int) -> int:
        """Return the highest IMAP UID synced for a folder."""
        with connect(self.database_path) as connection:
            return get_folder_last_seen_uid(connection, account_id, folder_id)

    def update_folder_last_seen_uid(
        self,
        account_id: int,
        folder_id: int,
        last_seen_uid: int,
    ) -> None:
        """Persist the highest IMAP UID synced for a folder."""
        with connect(self.database_path) as connection:
            update_folder_last_seen_uid(
                connection,
                account_id,
                folder_id,
                last_seen_uid,
            )

    def mark_message_read(self, message_id: int, is_read: bool = True) -> None:
        """Update the read state for one cached message."""
        with connect(self.database_path) as connection:
            mark_message_read(connection, message_id, is_read)

    def update_message_flags(
        self,
        account_id: int,
        folder_id: int,
        imap_uid: str,
        flags: str,
        is_read: bool,
    ) -> bool:
        """Update cached IMAP flags for one message."""
        with connect(self.database_path) as connection:
            return update_message_flags(
                connection,
                account_id,
                folder_id,
                imap_uid,
                flags,
                is_read,
            )

    def move_message_to_folder(
        self,
        message_id: int,
        destination_folder_id: int,
    ) -> bool:
        """Move one cached message to another local folder."""
        with connect(self.database_path) as connection:
            return move_message_to_folder(
                connection,
                message_id,
                destination_folder_id,
            )

    def delete_message(self, message_id: int) -> bool:
        """Delete one cached message."""
        with connect(self.database_path) as connection:
            return delete_message(connection, message_id)

    def delete_messages_missing_from_folder(
        self,
        account_id: int,
        folder_id: int,
        remote_uids: list[str],
    ) -> int:
        """Delete local messages missing from the remote folder."""
        with connect(self.database_path) as connection:
            return delete_messages_missing_from_folder(
                connection,
                account_id,
                folder_id,
                remote_uids,
            )


def _folder_names_match(
    existing_name: str,
    requested_name: str,
    requested_remote_id: str | None,
) -> bool:
    existing_aliases = _folder_aliases(existing_name)
    requested_aliases = _folder_aliases(requested_name)
    if requested_remote_id:
        requested_aliases.update(_folder_aliases(requested_remote_id))
    return bool(existing_aliases & requested_aliases)


def _folder_aliases(name: str) -> set[str]:
    normalized = name.rsplit("/", maxsplit=1)[-1].casefold()
    aliases = {name.casefold(), normalized}
    if normalized in {"inbox", "innboks"}:
        aliases.update({"inbox", "innboks"})
    elif normalized in {"sent", "sent mail", "sendt"}:
        aliases.update({"sent", "sent mail", "sendt"})
    elif normalized in {"trash", "papirkurv", "deleted items"}:
        aliases.update({"trash", "papirkurv", "deleted items"})
    elif normalized in {"all mail", "all e-post", "alle e-poster"}:
        aliases.update({"all mail", "all e-post", "alle e-poster"})
    return aliases
