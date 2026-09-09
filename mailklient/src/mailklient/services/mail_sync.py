"""Mail synchronization service."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass

from mailklient.domain import Account, Attachment, Folder
from mailklient.domain.folders import standard_folder_name
from mailklient.mail import (
    ImapClient,
    ImapFolder,
    ImapMessageFlags,
    ImapMessageHeader,
    SmtpClient,
)
from mailklient.services.attachment_cache import AttachmentCache
from mailklient.services.mail_settings import get_mail_account_settings
from mailklient.services.mail_store import MailStore


@dataclass(frozen=True, slots=True)
class HeaderSyncResult:
    """Summary of an IMAP header sync."""

    folders_seen: int
    messages_seen: int
    messages_failed: int = 0


CORE_SYNC_FOLDER_NAMES = (
    "INBOX",
    "Innboks",
    "Sent",
    "Sent Mail",
    "Sendt",
    "Spam",
    "Junk",
    "Søppelpost",
    "Trash",
    "Papirkurv",
    "Deleted Items",
    "Archive",
    "Arkiv",
)


class MailSyncService:
    """Coordinate mail protocol clients and the local store."""

    def __init__(
        self,
        store: MailStore,
        imap_client_class=ImapClient,
        smtp_client_class=SmtpClient,
    ) -> None:
        self._store = store
        self._imap_client_class = imap_client_class
        self._smtp_client_class = smtp_client_class

    def test_imap_connection(self, account_id: int) -> bool:
        """Test IMAP SSL login for one account."""
        settings = get_mail_account_settings(self._store, account_id)
        if settings is None:
            return False

        return self._imap_client_class(settings.imap).test_connection()

    def test_smtp_connection(self, account_id: int) -> bool:
        """Test SMTP SSL login for one account."""
        settings = get_mail_account_settings(self._store, account_id)
        if settings is None:
            return False

        return self._smtp_client_class(settings.smtp).test_connection()

    def fetch_imap_headers(
        self,
        account_id: int,
        limit_per_folder: int = 25,
        folder_names: tuple[str, ...] | None = None,
        message_folder_names: tuple[str, ...] | None = CORE_SYNC_FOLDER_NAMES,
        flag_refresh_limit: int = 250,
        deletion_reconcile_limit: int = 1000,
        fetch_older: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> HeaderSyncResult:
        """Fetch folder names and recent message headers into the local cache."""

        def report(message: str) -> None:
            if progress is not None:
                progress(message)

        report("Loading credentials from the keyring...")
        settings = get_mail_account_settings(self._store, account_id)
        if settings is None:
            raise ValueError(
                "Valid credentials or complete server settings are missing. "
                "Sign in to the account again and check the IMAP/SMTP settings."
            )

        account = self._store.get_account(account_id)
        imap_client = self._imap_client_class(settings.imap)
        if hasattr(imap_client, "set_progress_callback"):
            imap_client.set_progress_callback(report)
        with (
            imap_client.session() if hasattr(imap_client, "session") else nullcontext()
        ):
            report("Fetching folder list...")
            folders = _selectable_folders(
                _filter_folders(imap_client.list_folders(), folder_names)
            )
            folders = _filter_core_folders(folders, message_folder_names)
            messages_seen = 0
            messages_failed = 0

            for folder_index, imap_folder in enumerate(folders, 1):
                report(f"Folder {folder_index}/{len(folders)}: {imap_folder.name}")
                local_folder_name = _local_folder_name(account, imap_folder)
                folder = self._store.get_or_add_folder(
                    account_id,
                    local_folder_name,
                    remote_id=imap_folder.name,
                )
                if not _should_fetch_folder(imap_folder, message_folder_names):
                    continue

                if hasattr(imap_client, "folder_uidvalidity"):
                    validity = imap_client.folder_uidvalidity(imap_folder.name)
                    self._store.reconcile_uidvalidity(account_id, folder.id, validity)
                    imap_client.expect_uidvalidity(imap_folder.name, validity)

                last_seen_uid = self._store.get_folder_last_seen_uid(
                    account_id, folder.id
                )
                oldest_uid = self._store.oldest_folder_uid(account_id, folder.id)
                if hasattr(imap_client, "fetch_messages"):
                    failed_uids = self._store.failed_message_uids(
                        account_id, folder.id, limit_per_folder
                    )
                    headers = imap_client.fetch_messages(
                        imap_folder.name,
                        limit=limit_per_folder,
                        after_uid=0 if fetch_older else last_seen_uid,
                        before_uid=oldest_uid if fetch_older else None,
                    )
                    fetched_uids = {header.uid for header in headers}
                    retry_uids = tuple(
                        uid for uid in failed_uids if uid not in fetched_uids
                    )
                    if retry_uids:
                        report(
                            f"{imap_folder.name}: retrying previously failed "
                            "content downloads..."
                        )
                        headers.extend(
                            imap_client.fetch_messages(
                                imap_folder.name,
                                limit=limit_per_folder,
                                message_uids=retry_uids,
                            )
                        )
                elif fetch_older and oldest_uid is not None:
                    headers = imap_client.fetch_headers_before_uid(
                        imap_folder.name, oldest_uid, limit_per_folder
                    )
                elif last_seen_uid > 0:
                    headers = imap_client.fetch_headers_since_uid(
                        imap_folder.name,
                        last_seen_uid,
                        limit_per_folder,
                    )
                else:
                    headers = imap_client.fetch_headers(
                        imap_folder.name, limit_per_folder
                    )
                messages_seen += len(headers)
                messages_failed += sum(header.parse_error for header in headers)

                for header_index, header in enumerate(headers, 1):
                    report(
                        f"{imap_folder.name}: saving message "
                        f"{header_index}/{len(headers)}..."
                    )
                    message = self._store.save_message_metadata(
                        account_id,
                        folder.id,
                        imap_uid=header.uid,
                        flags=" ".join(header.flags),
                        message_id=header.message_id,
                        subject=header.subject,
                        sender=header.sender,
                        recipients=header.recipients,
                        reply_to=header.reply_to,
                        in_reply_to=header.in_reply_to,
                        references=header.references,
                        received_at=header.date,
                        is_read=_is_seen(header.flags),
                        body_preview=header.body_preview,
                        body_text=header.body_text,
                        body_html=header.body_html,
                        body_fetch_failed=header.parse_error,
                    )
                    if header.parse_error:
                        # Keep any previously downloaded attachment metadata/content.
                        continue
                    self._store.replace_message_attachments(
                        message.id,
                        [
                            Attachment(
                                id=0,
                                message_id=message.id,
                                filename=attachment.filename,
                                content_type=attachment.content_type,
                                size=attachment.size,
                                content_id=attachment.content_id,
                                is_inline=attachment.is_inline,
                                has_content=attachment.content is not None,
                                content=attachment.content,
                                imap_section=attachment.imap_section,
                            )
                            for attachment in header.attachments
                        ],
                    )

                max_uid = _max_numeric_uid(headers)
                if max_uid is not None:
                    self._store.update_folder_last_seen_uid(
                        account_id,
                        folder.id,
                        max_uid,
                    )

                if deletion_reconcile_limit > 0:
                    report(
                        f"{imap_folder.name}: checking moved "
                        "and deleted messages..."
                    )
                    remote_uids = imap_client.list_uids(imap_folder.name)
                    if remote_uids is not None:
                        self._store.delete_messages_missing_from_folder(
                            account_id,
                            folder.id,
                            remote_uids,
                        )

                report(f"{imap_folder.name}: updating read/unread status...")
                self._refresh_recent_flags(
                    account_id,
                    folder.id,
                    imap_client,
                    imap_folder.name,
                    flag_refresh_limit,
                )

        report("Finishing local storage...")
        self._store.remove_empty_folder_placeholders(account_id)
        AttachmentCache(self._store).trim()
        return HeaderSyncResult(
            folders_seen=len(folders),
            messages_seen=messages_seen,
            messages_failed=messages_failed,
        )

    def mark_message_read(self, message_id: int, is_read: bool = True) -> bool:
        """Mark a message read/unread locally and on IMAP when possible."""
        message = self._store.get_message(message_id)
        if message is None:
            return False

        if not message.imap_uid:
            self._store.mark_message_read(message_id, is_read)
            return True

        folder = self._store.get_folder(message.folder_id)
        settings = get_mail_account_settings(self._store, message.account_id)
        if settings is None or folder is None:
            self._store.mark_message_read(message_id, is_read)
            return True

        imap_client = self._imap_client_class(settings.imap)
        self._guard_remote_uid(imap_client, folder)
        updated = imap_client.set_seen(
            folder.remote_id or folder.name,
            message.imap_uid,
            is_read,
        )
        if not updated:
            return False

        self._store.mark_message_read(message_id, is_read)
        return True

    def move_message_to_folder(
        self,
        message_id: int,
        destination_folder_id: int,
    ) -> bool:
        """Move a message locally and remotely when possible."""
        message = self._store.get_message(message_id)
        destination = self._store.get_folder(destination_folder_id)
        if message is None or destination is None:
            return False
        if destination.account_id != message.account_id:
            raise ValueError("Moving messages between accounts is not supported.")
        if destination.id == message.folder_id:
            return True

        source = self._store.get_folder(message.folder_id)
        settings = get_mail_account_settings(self._store, message.account_id)
        if message.imap_uid and settings is None:
            raise ValueError("Credentials are missing. No message was moved.")
        if source and source.remote_id and not message.imap_uid:
            raise ValueError("Sync the folder before moving this message again.")
        if message.imap_uid and source is not None and settings is not None:
            if not destination.remote_id:
                raise ValueError(
                    "The destination folder is not linked to the server. Sync first."
                )
            client = self._imap_client_class(settings.imap)
            self._guard_remote_uid(client, source)
            moved = client.move_message(
                source.remote_id or source.name,
                message.imap_uid,
                destination.remote_id or destination.name,
            )
            if not moved:
                return False

        return self._store.move_message_to_folder(message_id, destination.id)

    def move_message_to_trash(self, message_id: int) -> bool:
        """Move a message to the account's trash folder."""
        message = self._store.get_message(message_id)
        if message is None:
            return False

        trash = _get_or_add_trash_folder(self._store, message.account_id)
        return self.move_message_to_folder(message_id, trash.id)

    def archive_message(self, message_id: int) -> bool:
        """Archive a message from its current folder."""
        message = self._store.get_message(message_id)
        if message is None:
            return False

        account = self._store.get_account(message.account_id)
        if account is not None and account.provider == "tuta":
            return False

        source = self._store.get_folder(message.folder_id)
        if not _is_gmail_account(account):
            settings = get_mail_account_settings(self._store, message.account_id)
            if message.imap_uid and settings is not None:
                client = self._imap_client_class(settings.imap)
                archive_folder = next(
                    (
                        f
                        for f in _selectable_folders(client.list_folders())
                        if _has_special_use(f, "\\Archive")
                        or _normalized_folder_name(f.name)
                        in {"archive", "archives", "arkiv"}
                    ),
                    None,
                )
                if archive_folder is None:
                    raise ValueError(
                        "No archive folder was found on the server. "
                        "Create it in webmail, then sync."
                    )
                archive = self._store.get_or_add_folder(
                    message.account_id, "Arkiv", remote_id=archive_folder.name
                )
            else:
                archive = _get_or_add_archive_folder(self._store, message.account_id)
            return self.move_message_to_folder(message_id, archive.id)
        settings = get_mail_account_settings(self._store, message.account_id)
        if message.imap_uid and source is not None and settings is not None:
            client = self._imap_client_class(settings.imap)
            self._guard_remote_uid(client, source)
            archived = client.archive_gmail_message(
                source.remote_id or source.name, message.imap_uid
            )
            if not archived:
                return False

        if message.imap_uid and settings is None:
            raise ValueError("Credentials are missing. No message was archived.")

        archive = _get_or_add_archive_folder(self._store, message.account_id)
        return self._store.move_message_to_folder(message_id, archive.id)

    def _guard_remote_uid(self, client, folder: Folder) -> None:
        if hasattr(client, "expect_uidvalidity"):
            validity = self._store.folder_uidvalidity(folder.account_id, folder.id)
            if validity is None:
                raise ValueError(
                    "Sync the folder before changing messages on the server."
                )
            client.expect_uidvalidity(folder.remote_id or folder.name, validity)

    def _refresh_recent_flags(
        self,
        account_id: int,
        folder_id: int,
        imap_client: ImapClient,
        folder_name: str,
        limit: int,
    ) -> None:
        if limit <= 0 or not hasattr(imap_client, "fetch_recent_flags"):
            return

        for item in imap_client.fetch_recent_flags(folder_name, limit):
            flags = _coerce_message_flags(item)
            if flags is None:
                continue
            self._store.update_message_flags(
                account_id,
                folder_id,
                flags.uid,
                " ".join(flags.flags),
                _is_seen(flags.flags),
            )


def _is_seen(flags: tuple[str, ...]) -> bool:
    return any(flag.casefold() == "\\seen" for flag in flags)


def _max_numeric_uid(headers: list[ImapMessageHeader]) -> int | None:
    numeric_uids: list[int] = []
    for header in headers:
        try:
            numeric_uids.append(int(header.uid))
        except ValueError:
            continue

    if not numeric_uids:
        return None
    return max(numeric_uids)


def _filter_folders(
    folders: list[ImapFolder],
    folder_names: tuple[str, ...] | None,
) -> list[ImapFolder]:
    if folder_names is None:
        return folders

    selected_names = {name.casefold() for name in folder_names}
    return [folder for folder in folders if folder.name.casefold() in selected_names]


def _selectable_folders(folders: list[ImapFolder]) -> list[ImapFolder]:
    return [
        folder
        for folder in folders
        if not any(flag.casefold() == "\\noselect" for flag in folder.flags)
    ]


def _filter_core_folders(
    folders: list[ImapFolder],
    message_folder_names: tuple[str, ...] | None,
) -> list[ImapFolder]:
    if message_folder_names is None:
        return folders
    return [
        folder
        for folder in folders
        if _should_fetch_folder(folder, message_folder_names)
    ]


def _should_fetch_folder(
    folder: ImapFolder,
    message_folder_names: tuple[str, ...] | None,
) -> bool:
    if message_folder_names is None:
        return True

    selected_names = {
        alias for name in message_folder_names for alias in _folder_aliases(name)
    }
    folder_names = _folder_aliases(folder.name)
    if _has_special_use(folder, "\\inbox"):
        folder_names.update(_folder_aliases("INBOX"))
    if _has_special_use(folder, "\\sent"):
        folder_names.update(_folder_aliases("Sent"))
    if _has_special_use(folder, "\\trash"):
        folder_names.update(_folder_aliases("Trash"))
    if _has_special_use(folder, "\\junk"):
        folder_names.update(_folder_aliases("Spam"))
    if _has_special_use(folder, "\\Archive"):
        folder_names.update(_folder_aliases("Archive"))
    return bool(folder_names & selected_names)


def _get_or_add_trash_folder(store: MailStore, account_id: int) -> Folder:
    account = store.get_account(account_id)
    for folder in store.list_folders(account_id):
        normalized = folder.name.rsplit("/", maxsplit=1)[-1].casefold()
        remote_normalized = (
            folder.remote_id.rsplit("/", maxsplit=1)[-1].casefold()
            if folder.remote_id
            else ""
        )
        if normalized in {"trash", "papirkurv", "deleted items"}:
            return folder
        if remote_normalized in {"trash", "papirkurv", "deleted items"}:
            return folder

    if account is not None and account.oauth_provider == "gmail":
        return store.get_or_add_folder(
            account_id,
            "Papirkurv",
            remote_id="[Gmail]/Trash",
        )
    return store.get_or_add_folder(account_id, "Papirkurv")


def _get_or_add_archive_folder(store: MailStore, account_id: int) -> Folder:
    account = store.get_account(account_id)
    if _is_gmail_account(account):
        for folder in store.list_folders(account_id):
            if _is_gmail_all_mail(folder.name) or _is_gmail_all_mail(
                folder.remote_id or ""
            ):
                return folder
        return store.get_or_add_folder(
            account_id,
            "All e-post",
            remote_id="[Gmail]/All Mail",
        )
    return store.get_or_add_folder(account_id, "Arkiv")


def _local_folder_name(account: Account | None, folder: ImapFolder) -> str:
    if _has_special_use(folder, "\\Archive") or _normalized_folder_name(
        folder.name
    ) in {"archive", "archives", "arkiv"}:
        return "Arkiv"
    if _has_special_use(folder, "\\inbox") or folder.name.casefold() == "inbox":
        return "Innboks"
    if _has_special_use(folder, "\\sent") or _is_sent_name(folder.name):
        return "Sendt"
    if _has_special_use(folder, "\\trash") or _is_trash_name(folder.name):
        return "Papirkurv"
    if (
        _has_special_use(folder, "\\drafts")
        or _normalized_folder_name(folder.name) == "drafts"
    ):
        return "Utkast"
    if _has_special_use(folder, "\\junk") or _normalized_folder_name(folder.name) in {
        "spam",
        "junk",
    }:
        return "Spam"
    if _has_special_use(folder, "\\all") or (
        _is_gmail_account(account) and _is_gmail_all_mail(folder.name)
    ):
        return "All e-post"
    return standard_folder_name(folder.name) or folder.name


def _coerce_message_flags(item: object) -> ImapMessageFlags | None:
    if isinstance(item, ImapMessageFlags):
        return item
    uid = getattr(item, "uid", None)
    flags = getattr(item, "flags", None)
    if not isinstance(uid, str) or not isinstance(flags, tuple):
        return None
    return ImapMessageFlags(uid=uid, flags=flags)


def _has_special_use(folder: ImapFolder, special_use: str) -> bool:
    normalized = special_use.casefold()
    return any(flag.casefold() == normalized for flag in folder.flags)


def _is_gmail_account(account: Account | None) -> bool:
    return account is not None and account.oauth_provider == "gmail"


def _is_gmail_all_mail(name: str) -> bool:
    normalized = _normalized_folder_name(name)
    return normalized in {"all mail", "all e-post", "alle e-poster"}


def _is_sent_name(name: str) -> bool:
    return standard_folder_name(name) == "Sendt"


def _is_trash_name(name: str) -> bool:
    return standard_folder_name(name) == "Papirkurv"


def _folder_aliases(name: str) -> set[str]:
    normalized = _normalized_folder_name(name)
    standard_name = standard_folder_name(name)
    if standard_name:
        normalized = standard_name.casefold()
    aliases = {name.casefold(), normalized}
    if normalized in {"inbox", "innboks"}:
        aliases.update({"inbox", "innboks"})
    elif normalized in {"archive", "archives", "arkiv"}:
        aliases.update({"archive", "archives", "arkiv"})
    elif normalized in {"sent", "sent mail", "sendt"}:
        aliases.update({"sent", "sent mail", "sendt"})
    elif normalized in {"trash", "papirkurv", "deleted items"}:
        aliases.update({"trash", "papirkurv", "deleted items"})
    elif normalized in {"spam", "junk", "søppelpost"}:
        aliases.update({"spam", "junk", "søppelpost"})
    return aliases


def _normalized_folder_name(name: str) -> str:
    return name.rsplit("/", maxsplit=1)[-1].casefold()
