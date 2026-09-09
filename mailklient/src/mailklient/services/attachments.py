"""Retrieve attachment content from cache or the original IMAP mailbox."""

from __future__ import annotations

from mailklient.domain import Attachment
from mailklient.mail import ImapClient
from mailklient.services.attachment_cache import (
    MAX_ATTACHMENT_CACHE_BYTES,
    AttachmentCache,
)
from mailklient.services.mail_settings import get_mail_account_settings
from mailklient.services.mail_store import MailStore


class AttachmentService:
    def __init__(self, store: MailStore, imap_client_class=ImapClient) -> None:
        self._store = store
        self._client_class = imap_client_class

    def content(self, attachment: Attachment) -> bytes:
        content = self._store.get_attachment_content(attachment.id)
        if content is not None:
            return content
        message = self._store.get_message(attachment.message_id)
        if message is None or not message.imap_uid:
            raise ValueError("The attachment is missing locally. Sync the message first.")
        folder = self._store.get_folder(message.folder_id)
        if folder is None or not folder.remote_id:
            raise ValueError("The attachment is not linked to a server folder.")
        validity = self._store.folder_uidvalidity(message.account_id, folder.id)
        if validity is None:
            raise ValueError("Sync the folder before fetching the attachment.")
        settings = get_mail_account_settings(self._store, message.account_id)
        if settings is None:
            raise ValueError("Sign in to the account before fetching the attachment.")
        attachments = self._store.list_attachments(message.id)
        index = next(
            (i for i, item in enumerate(attachments) if item.id == attachment.id), None
        )
        if index is None:
            raise ValueError("The attachment has changed. Select the message again.")
        client = self._client_class(settings.imap)
        client.expect_uidvalidity(folder.remote_id, validity)
        remote = client.fetch_attachment(folder.remote_id, message.imap_uid, index)
        if (remote.filename, remote.content_type) != (
            attachment.filename,
            attachment.content_type,
        ) or remote.content is None:
            raise ValueError(
                "The attachment does not match the local metadata. Sync again."
            )
        if attachment.imap_section is not None:
            if remote.imap_section != attachment.imap_section:
                raise ValueError("The attachment part has changed. Sync again.")
        elif remote.size != attachment.size:
            raise ValueError("The attachment size has changed. Sync again.")
        current = self._store.get_message(message.id)
        if current is None or (current.folder_id, current.imap_uid) != (
            message.folder_id,
            message.imap_uid,
        ):
            raise ValueError("The message was moved while its attachment was being fetched.")
        if len(remote.content) <= MAX_ATTACHMENT_CACHE_BYTES:
            self._store.set_attachment_content(attachment.id, remote.content)
            AttachmentCache(self._store).trim()
        return remote.content
