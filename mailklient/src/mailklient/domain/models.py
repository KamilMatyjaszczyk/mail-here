"""Small domain models used by the application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Account:
    """An email account known to the local cache."""

    id: int
    display_name: str
    email_address: str
    username: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: str = "ssl"
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str = "starttls"
    auth_method: str = "password"
    oauth_provider: str | None = None
    provider: str = "imap"
    local_certificate: str | None = None


@dataclass(frozen=True, slots=True)
class Folder:
    """A mail folder known to the local cache."""

    id: int
    account_id: int
    name: str
    remote_id: str | None


@dataclass(frozen=True, slots=True)
class Message:
    """Message metadata stored in the local cache."""

    id: int
    account_id: int
    folder_id: int
    imap_uid: str | None = None
    flags: str = ""
    message_id: str | None = None
    subject: str = ""
    sender: str = ""
    recipients: str = ""
    sent_at: str | None = None
    received_at: str | None = None
    is_read: bool = False
    body_preview: str = ""
    body_text: str = ""
    body_html: str = ""
    reply_to: str = ""
    in_reply_to: str = ""
    references: str = ""


@dataclass(frozen=True, slots=True)
class Attachment:
    """Attachment metadata stored in the local cache."""

    id: int
    message_id: int
    filename: str
    content_type: str
    size: int
    content_id: str | None = None
    is_inline: bool = False
    has_content: bool = False
    content: bytes | None = None
    imap_section: str | None = None
