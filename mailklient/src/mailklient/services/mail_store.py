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
    get_attachment_content,
    get_folder,
    get_folder_last_seen_uid,
    get_message,
    initialize_database,
    list_accounts,
    list_attachments_for_message,
    list_folders,
    list_messages_for_folder,
    list_unified_inbox_messages,
    mark_message_read,
    move_message_to_folder,
    replace_message_attachments,
    set_attachment_content,
    update_account,
    update_folder_last_seen_uid,
    update_folder_remote_id,
    update_message_flags,
    upsert_message,
)
from mailklient.database.connection import DatabasePath
from mailklient.domain import Account, Attachment, Folder, Message
from mailklient.domain.folders import standard_folder_name

DEFAULT_FOLDERS = ("Innboks", "Sendt", "Søppelpost", "Papirkurv")


class MailStore:
    """Small service facade for the local SQLite cache."""

    def __init__(self, database_path: DatabasePath) -> None:
        self.database_path = Path(database_path)
        initialize_database(self.database_path)
        self.remove_empty_folder_placeholders()

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
        provider: str = "imap",
        local_certificate: str | None = None,
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
            provider=provider,
            local_certificate=local_certificate,
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
        provider: str = "imap",
        local_certificate: str | None = None,
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
                provider=provider,
                local_certificate=local_certificate,
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

    def update_account(self, account: Account) -> Account:
        """Persist edited account metadata without replacing cached mail."""
        with connect(self.database_path) as connection:
            return update_account(connection, account)

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
            folders = list_folders(connection, account_id)
            # A remote mailbox owns its UID namespace, even when names are aliases.
            for folder in folders:
                if remote_id and remote_id != "/" and folder.remote_id == remote_id:
                    return folder
            matches = [
                folder
                for folder in folders
                if _folder_names_match(folder.name, name, remote_id)
            ]
            matches.sort(
                key=lambda folder: (folder.remote_id is None, folder.name != name)
            )
            for folder in matches:
                if remote_id is None:
                    return folder
                if folder.remote_id in {None, "/"}:
                    return update_folder_remote_id(connection, folder.id, remote_id)
            occupied_names = {folder.name for folder in folders}
            local_name = name
            if local_name in occupied_names:
                local_name = remote_id or name
                suffix = 2
                while local_name in occupied_names:
                    local_name = f"{remote_id or name} ({suffix})"
                    suffix += 1
            return create_folder(connection, account_id, local_name, remote_id)

    def remove_empty_folder_placeholders(self, account_id: int | None = None) -> int:
        """Remove only empty, unbound local duplicates of known server folders."""
        removed = 0
        with connect(self.database_path) as connection:
            accounts = (
                list_accounts(connection)
                if account_id is None
                else [get_account(connection, account_id)]
            )
            for account in accounts:
                if account is None:
                    continue
                folders = list_folders(connection, account.id)
                remote_roles = {
                    standard_folder_name(folder.name)
                    or standard_folder_name(folder.remote_id)
                    for folder in folders
                    if folder.remote_id and folder.remote_id != "/"
                } - {None}
                for folder in folders:
                    if (
                        folder.remote_id is not None
                        or standard_folder_name(folder.name) not in remote_roles
                    ):
                        continue
                    cursor = connection.execute(
                        "DELETE FROM folders WHERE id = ? AND remote_id IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM messages WHERE folder_id = ?)",
                        (folder.id, folder.id),
                    )
                    removed += cursor.rowcount
        return removed

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
        reply_to: str = "",
        sent_at: str | None = None,
        received_at: str | None = None,
        is_read: bool = False,
        body_preview: str = "",
        body_text: str = "",
        body_html: str = "",
        in_reply_to: str = "",
        references: str = "",
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
                reply_to=reply_to,
                sent_at=sent_at,
                received_at=received_at,
                is_read=is_read,
                body_preview=body_preview,
                body_text=body_text,
                body_html=body_html,
                in_reply_to=in_reply_to,
                references=references,
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
        reply_to: str = "",
        sent_at: str | None = None,
        received_at: str | None = None,
        is_read: bool = False,
        body_preview: str = "",
        body_text: str = "",
        body_html: str = "",
        in_reply_to: str = "",
        references: str = "",
        body_fetch_failed: bool = False,
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
                reply_to=reply_to,
                sent_at=sent_at,
                received_at=received_at,
                is_read=is_read,
                body_preview=body_preview,
                body_text=body_text,
                body_html=body_html,
                in_reply_to=in_reply_to,
                references=references,
                body_fetch_failed=body_fetch_failed,
            )

    def failed_message_uids(
        self, account_id: int, folder_id: int, limit: int = 25
    ) -> tuple[str, ...]:
        """Return a bounded set of failed body fetches for the next sync."""
        if limit <= 0:
            return ()
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT imap_uid FROM messages "
                "WHERE account_id = ? AND folder_id = ? AND body_fetch_failed = 1 "
                "AND imap_uid != '' AND imap_uid NOT GLOB '*[^0-9]*' "
                "AND CAST(imap_uid AS INTEGER) > 0 ORDER BY id DESC LIMIT ?",
                (account_id, folder_id, limit),
            ).fetchall()
        return tuple(row["imap_uid"] for row in rows)

    def list_messages(self, account_id: int, folder_id: int) -> list[Message]:
        """List message metadata for one folder."""
        with connect(self.database_path) as connection:
            return list_messages_for_folder(connection, account_id, folder_id)

    def list_mailbox_messages(
        self, account_id: int | None, folder_name: str
    ) -> list[Message]:
        """Group standard folders for display, retaining each message's source."""
        if folder_name not in {"Innboks", "Papirkurv", "Søppelpost"}:
            raise ValueError("Unknown mailbox view.")
        with connect(self.database_path) as connection:
            account_ids = (
                [account.id for account in list_accounts(connection)]
                if account_id is None
                else [account_id]
            )
            messages = []
            for selected_id in account_ids:
                for folder in list_folders(connection, selected_id):
                    role = (
                        standard_folder_name(folder.remote_id)
                        if folder.remote_id
                        else None
                    ) or standard_folder_name(folder.name)
                    if role == folder_name:
                        messages.extend(
                            list_messages_for_folder(connection, selected_id, folder.id)
                        )
            return messages

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

    def folder_uidvalidity(self, account_id: int, folder_id: int) -> int | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT uidvalidity FROM folder_sync_state WHERE account_id = ? AND folder_id = ?",
                (account_id, folder_id),
            ).fetchone()
            return row["uidvalidity"] if row else None

    def reconcile_uidvalidity(
        self, account_id: int, folder_id: int, validity: int
    ) -> None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT uidvalidity FROM folder_sync_state WHERE account_id = ? AND folder_id = ?",
                (account_id, folder_id),
            ).fetchone()
            if row is not None and row["uidvalidity"] == validity:
                return
            # Only stale remote cache is removed; local sent copies are retained.
            connection.execute(
                "DELETE FROM messages WHERE account_id = ? AND folder_id = ? AND imap_uid IS NOT NULL",
                (account_id, folder_id),
            )
            connection.execute(
                "INSERT INTO folder_sync_state(account_id, folder_id, last_seen_uid, uidvalidity) "
                "VALUES (?, ?, 0, ?) ON CONFLICT(account_id, folder_id) DO UPDATE SET "
                "last_seen_uid = 0, uidvalidity = excluded.uidvalidity",
                (account_id, folder_id, validity),
            )

    def oldest_folder_uid(self, account_id: int, folder_id: int) -> int | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT MIN(CAST(imap_uid AS INTEGER)) FROM messages "
                "WHERE account_id = ? AND folder_id = ? AND CAST(imap_uid AS INTEGER) > 0",
                (account_id, folder_id),
            ).fetchone()
            return row[0]

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
    if existing_name == requested_name:
        return True
    existing_role = standard_folder_name(existing_name)
    requested_role = standard_folder_name(requested_name)
    if requested_role is None and requested_remote_id:
        requested_role = standard_folder_name(requested_remote_id)
    return existing_role is not None and existing_role == requested_role
