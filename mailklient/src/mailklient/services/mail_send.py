"""Service layer for composing and sending mail."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import getaddresses, make_msgid
from mimetypes import guess_type
from pathlib import Path

from mailklient.domain import Attachment, Message
from mailklient.mail import ImapClient, ImapSettings, SmtpClient, build_email_message
from mailklient.mail.config import is_personal_outlook_account
from mailklient.security.attachment_files import AttachmentWorkspace
from mailklient.services.attachments import AttachmentService
from mailklient.services.mail_settings import get_mail_account_settings
from mailklient.services.mail_store import MailStore


@dataclass(frozen=True, slots=True)
class ComposeDraft:
    """Plain text draft data used by the compose UI."""

    account_id: int
    recipients: str = ""
    cc: str = ""
    bcc: str = ""
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    attachment_paths: tuple[str, ...] = ()
    in_reply_to: str | None = None


@dataclass(frozen=True, slots=True)
class SendResult:
    """Result of sending one draft."""

    sent: bool
    local_copy_saved: bool = False
    server_copy_attempted: bool = False
    server_copy_saved: bool = False
    local_copy_deferred: bool = False

    def __bool__(self) -> bool:
        return self.sent


class MailSendError(Exception):
    """A send failure safe to show without raw server responses."""


class MailSendService:
    """Coordinate SMTP sending and compose defaults."""

    def __init__(
        self,
        store: MailStore,
        smtp_client_class=SmtpClient,
        imap_client_class=ImapClient,
    ) -> None:
        self._store = store
        self._smtp_client_class = smtp_client_class
        self._imap_client_class = imap_client_class
        self._forward_files: AttachmentWorkspace | None = None

    def send_draft(self, draft: ComposeDraft) -> SendResult:
        """Send a draft through the selected account."""
        settings = get_mail_account_settings(self._store, draft.account_id)
        if settings is None:
            return SendResult(sent=False)

        recipients = _parse_recipients(draft.recipients)
        cc = _parse_recipients(draft.cc)
        bcc = _parse_recipients(draft.bcc)
        if not recipients:
            raise ValueError("Enter at least one recipient.")

        account = self._store.get_account(draft.account_id)
        is_tuta = account is not None and account.provider == "tuta"
        server_copy_attempted = _should_append_sent_copy_to_server(
            self._store, draft.account_id
        )

        sent_at = datetime.now(timezone.utc)
        message_id = make_msgid(domain=_message_id_domain(settings.email_address))
        try:
            smtp = replace(settings.smtp, timeout=60.0) if is_tuta else settings.smtp
            sent = self._smtp_client_class(smtp).send_message(
                sender=settings.email_address,
                recipients=recipients,
                subject=draft.subject,
                body_text=draft.body_text,
                cc=cc,
                bcc=bcc,
                body_html=draft.body_html,
                attachment_paths=list(draft.attachment_paths),
                in_reply_to=draft.in_reply_to,
                message_id=message_id,
                date_header=sent_at.strftime("%a, %d %b %Y %H:%M:%S %z"),
            )
        except Exception as error:
            if not is_tuta:
                raise
            raise MailSendError(_tuta_send_error(error)) from None
        if sent:
            deferred = account is not None and is_personal_outlook_account(account)
            local_copy_saved = False
            # Outlook.com rewrites Message-ID and saves Sent itself; sync that copy.
            if not deferred:
                try:
                    self._save_sent_copy(
                        draft, settings.email_address, message_id, sent_at
                    )
                except Exception:  # noqa: BLE001 - SMTP success must survive cache failures.
                    local_copy_saved = False
                else:
                    local_copy_saved = True
            server_copy_saved = False
            if server_copy_attempted:
                server_copy_saved = self._append_sent_copy_to_server(
                    settings.imap,
                    draft,
                    settings.email_address,
                    recipients,
                    cc,
                    bcc,
                    message_id,
                    sent_at,
                )
            return SendResult(
                sent=True,
                local_copy_saved=local_copy_saved,
                server_copy_attempted=server_copy_attempted,
                server_copy_saved=server_copy_saved,
                local_copy_deferred=deferred,
            )
        return SendResult(sent=False)

    def create_reply_draft(self, message_id: int) -> ComposeDraft | None:
        """Create a reply draft using the original receiving account."""
        message = self._store.get_message(message_id)
        if message is None:
            return None

        return ComposeDraft(
            account_id=message.account_id,
            recipients=message.reply_to or message.sender,
            subject=_reply_subject(message.subject),
            body_text=_quoted_reply_body(message),
            in_reply_to=message.message_id,
        )

    def create_forward_draft(self, message_id: int) -> ComposeDraft | None:
        """Create a forward draft using the original receiving account."""
        message = self._store.get_message(message_id)
        if message is None:
            return None
        paths = []
        attachments = self._store.list_attachments(message.id)
        if attachments:
            if self._forward_files is None:
                self._forward_files = AttachmentWorkspace()
            service = AttachmentService(self._store, self._imap_client_class)
            for attachment in attachments:
                paths.append(str(self._forward_files.write(attachment.filename, service.content(attachment))))
        return ComposeDraft(
            account_id=message.account_id,
            recipients="",
            subject=_forward_subject(message.subject),
            body_text=_forward_body(message),
            attachment_paths=tuple(paths),
        )

    def close(self) -> None:
        if self._forward_files is not None:
            self._forward_files.close()

    def _save_sent_copy(
        self,
        draft: ComposeDraft,
        sender: str,
        message_id: str,
        sent_at: datetime,
    ) -> None:
        sent_folder = self._store.get_or_add_folder(draft.account_id, "Sendt")
        message = self._store.save_message_metadata(
            draft.account_id,
            sent_folder.id,
            message_id=message_id,
            subject=draft.subject,
            in_reply_to=draft.in_reply_to or "",
            references=draft.in_reply_to or "",
            sender=sender,
            recipients=_sent_recipients_label(draft),
            sent_at=sent_at.isoformat(),
            is_read=True,
            body_preview=_preview(draft.body_text),
            body_text=draft.body_text,
            body_html=draft.body_html,
        )

        self._store.replace_message_attachments(
            message.id,
            [
                _attachment_from_path(message.id, Path(path))
                for path in draft.attachment_paths
            ],
        )

    def _append_sent_copy_to_server(
        self,
        imap_settings: ImapSettings,
        draft: ComposeDraft,
        sender: str,
        recipients: list[str],
        cc: list[str],
        bcc: list[str],
        message_id: str,
        sent_at: datetime,
    ) -> bool:
        try:
            message, _envelope = build_email_message(
                sender=sender,
                recipients=recipients,
                subject=draft.subject,
                body_text=draft.body_text,
                cc=cc,
                bcc=bcc,
                body_html=draft.body_html,
                attachment_paths=list(draft.attachment_paths),
                in_reply_to=draft.in_reply_to,
                message_id=message_id,
                date_header=sent_at.strftime("%a, %d %b %Y %H:%M:%S %z"),
            )
            return self._imap_client_class(imap_settings).append_message(
                _sent_server_folder_name(self._store, draft.account_id),
                message.as_bytes(),
                flags=("\\Seen",),
                internal_date=sent_at.strftime("%d-%b-%Y %H:%M:%S %z"),
            )
        except Exception:  # noqa: BLE001 - Report copy failure separately from sending.
            return False


def _parse_recipients(recipients: str) -> list[str]:
    parsed_addresses = getaddresses([recipients])
    return [address for _name, address in parsed_addresses if address]


def _tuta_send_error(error: Exception) -> str:
    if isinstance(error, ssl.SSLCertVerificationError):
        return "The TutaBridge certificate could not be verified. No email was sent."
    if isinstance(error, ConnectionRefusedError):
        return "TutaBridge is unavailable. Start it via Account > TutaBridge."
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "TutaBridge rejected the bridge password. No email was sent."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "TutaBridge rejected the recipients. No email was sent."
    if isinstance(error, smtplib.SMTPDataError):
        return "TutaBridge rejected the message. Check the bridge status and attachment size."
    if isinstance(error, (ValueError, FileNotFoundError, PermissionError)):
        return "Could not prepare the email. Check the fields and attachments."
    return (
        "Sending through Tuta could not be confirmed. Check Sent in Tuta before sending again."
    )


def _attachment_from_path(message_id: int, path: Path) -> Attachment:
    content = path.read_bytes()
    return Attachment(
        id=0,
        message_id=message_id,
        filename=path.name,
        content_type=guess_type(path)[0] or "application/octet-stream",
        size=len(content),
        has_content=True,
        content=content,
    )


def _sent_recipients_label(draft: ComposeDraft) -> str:
    parts = [draft.recipients.strip()]
    if draft.cc.strip():
        parts.append(f"Cc: {draft.cc.strip()}")
    if draft.bcc.strip():
        parts.append(f"Bcc: {draft.bcc.strip()}")
    return "; ".join(part for part in parts if part)


def _message_id_domain(email_address: str) -> str:
    if "@" not in email_address:
        return "mailklient.local"
    return email_address.rsplit("@", maxsplit=1)[-1] or "mailklient.local"


def _sent_server_folder_name(store: MailStore, account_id: int) -> str:
    account = store.get_account(account_id)
    if account is not None and account.oauth_provider == "gmail":
        default_name = "[Gmail]/Sent Mail"
        preferred_names = {"[gmail]/sent mail", "sent mail"}
    else:
        default_name = "Sent"
        preferred_names = {"sent mail", "sent", "sendt"}
    for folder in store.list_folders(account_id):
        if folder.remote_id:
            candidates = {folder.name.casefold(), folder.remote_id.casefold()}
            if candidates & preferred_names:
                return folder.remote_id
        elif folder.name.casefold() in {"sent mail", "sent"}:
            return folder.name
    return default_name


def _should_append_sent_copy_to_server(store: MailStore, account_id: int) -> bool:
    account = store.get_account(account_id)
    return not (
        account is not None
        and (
            account.oauth_provider == "gmail"
            or account.provider == "tuta"
            or is_personal_outlook_account(account)
        )
    )


def _reply_subject(subject: str) -> str:
    stripped_subject = subject.strip()
    if stripped_subject.casefold().startswith("re:"):
        return stripped_subject
    return f"Re: {stripped_subject}" if stripped_subject else "Re:"


def _forward_subject(subject: str) -> str:
    stripped_subject = subject.strip()
    if stripped_subject.casefold().startswith(("fwd:", "fw:")):
        return stripped_subject
    return f"Fwd: {stripped_subject}" if stripped_subject else "Fwd:"


def _quoted_reply_body(message: Message) -> str:
    original_body = message.body_text or message.body_preview
    quote_lines = [f"> {line}" if line else ">" for line in original_body.splitlines()]
    return "\n\n" + "\n".join(quote_lines)


def _forward_body(message: Message) -> str:
    original_body = message.body_text or message.body_preview
    return "\n\n".join(
        [
            "",
            "---------- Forwarded message ----------",
            f"From: {message.sender}",
            f"To: {message.recipients}",
            f"Date: {message.received_at or message.sent_at or ''}",
            f"Subject: {message.subject}",
            "",
            original_body,
        ]
    )


def _preview(body_text: str, max_length: int = 240) -> str:
    normalized = " ".join(body_text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."
