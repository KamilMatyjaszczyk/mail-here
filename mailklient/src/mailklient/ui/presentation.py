"""Display labels, filtering and formatting shared by the window."""

from __future__ import annotations

import smtplib

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QIcon, QResizeEvent
from PySide6.QtWidgets import QLabel, QListWidgetItem, QSizePolicy, QStyle, QToolButton

from mailklient.domain import Folder, Message
from mailklient.domain.folders import standard_folder_name
from mailklient.mail import get_mail_provider_defaults
from mailklient.security import OAuthCallbackError
from mailklient.services import SendResult


def _folder_sort_key(name: str) -> tuple[int, str]:
    preferred_order = {
        "innboks": 0,
        "inbox": 0,
        "sendt": 4,
        "sent": 4,
        "sent mail": 4,
        "spam": 90,
        "junk": 90,
        "søppelpost": 90,
        "papirkurv": 99,
        "trash": 99,
        "deleted items": 99,
    }
    normalized_name = (
        standard_folder_name(name) or _normalized_folder_name(name)
    ).casefold()
    return (preferred_order.get(normalized_name, 100), normalized_name)


def _configure_message_item(item: QListWidgetItem, message: Message) -> None:
    font = QFont()
    font.setBold(not message.is_read)
    item.setFont(font)
    item.setToolTip(message.body_preview)


def _toolbar_button(
    text: str,
    icon_name: str,
    tooltip: str,
    object_name: str,
) -> QToolButton:
    button = QToolButton()
    button.setObjectName(object_name)
    button.setText(text)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    fallbacks = {
        "mail-message-new": QStyle.StandardPixmap.SP_FileIcon,
        "mail-reply-sender": QStyle.StandardPixmap.SP_ArrowBack,
        "mail-forward": QStyle.StandardPixmap.SP_ArrowForward,
        "archive-insert": QStyle.StandardPixmap.SP_DriveHDIcon,
        "user-trash": QStyle.StandardPixmap.SP_TrashIcon,
        "folder-move": QStyle.StandardPixmap.SP_DirOpenIcon,
        "mail-mark-read": QStyle.StandardPixmap.SP_DialogApplyButton,
        "mail-mark-unread": QStyle.StandardPixmap.SP_MessageBoxInformation,
        "mail-attachment": QStyle.StandardPixmap.SP_FileLinkIcon,
        "document-save-all": QStyle.StandardPixmap.SP_DialogSaveButton,
    }
    button.setIcon(
        QIcon.fromTheme(
            icon_name,
            button.style().standardIcon(
                fallbacks.get(icon_name, QStyle.StandardPixmap.SP_FileIcon)
            ),
        )
    )
    button.setIconSize(QSize(20, 20))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setAutoRaise(True)
    button.setFixedSize(32, 32)
    if object_name in {"compose_button", "attachments_button"}:
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setMaximumWidth(16777215)
        button.setMinimumWidth(0)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return button


class ElidedLabel(QLabel):
    """One-line mail metadata that never forces a column wider."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._full_text = " ".join(text.splitlines())
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideRight,
                self.contentsRect().width(),
            )
        )
        super().resizeEvent(event)


def _message_sender_label(message: Message) -> str:
    sender = message.sender.strip()
    if sender:
        return sender
    recipients = message.recipients.strip()
    if recipients:
        return f"To {recipients}"
    return "(unknown)"


def _compact_message_item_text(
    sender: str,
    subject: str,
    preview: str,
    *,
    account_label: str | None = None,
) -> str:
    lines = []
    if account_label and account_label.casefold() != sender.casefold():
        lines.append(account_label)
    lines.append(sender)
    lines.append(subject)
    if preview:
        lines.append(preview)
    return "\n".join(lines)


def _message_date_value(message: Message) -> str:
    return message.received_at or message.sent_at or ""


def _short_message_date(message: Message) -> str:
    value = _message_date_value(message)
    if not value:
        return ""
    if "T" in value:
        return value.split("T", maxsplit=1)[0]
    return value[:16]


def _folder_labels(folders: list[Folder]) -> list[str]:
    labels = [_folder_display_name(folder.name) for folder in folders]
    return [
        f"{label} ({folder.remote_id or 'local'})" if labels.count(label) > 1 else label
        for folder, label in zip(folders, labels)
    ]


def _folder_display_name(name: str) -> str:
    standard_name = standard_folder_name(name)
    if standard_name:
        # These canonical names are also used by existing caches and sync filters.
        return {
            "Innboks": "Inbox",
            "Sendt": "Sent",
            "Søppelpost": "Spam",
            "Papirkurv": "Trash",
            "Arkiv": "Archive",
        }[standard_name]
    normalized_name = _normalized_folder_name(name)
    display_names = {
        "inbox": "Inbox",
        "innboks": "Inbox",
        "sent": "Sent",
        "sent mail": "Sent",
        "sendt": "Sent",
        "trash": "Trash",
        "papirkurv": "Trash",
        "deleted items": "Trash",
        "spam": "Spam",
        "junk": "Spam",
        "søppelpost": "Spam",
        "all e-post": "All mail",
        "arkiv": "Archive",
    }
    return display_names.get(normalized_name, name.rsplit("/", maxsplit=1)[-1])


def _is_core_folder(name: str, remote_id: str | None = None) -> bool:
    if standard_folder_name(name) or (remote_id and standard_folder_name(remote_id)):
        return True
    aliases = _folder_aliases(name)
    if remote_id:
        aliases.update(_folder_aliases(remote_id))
    return bool(
        aliases
        & {
            "inbox",
            "innboks",
            "sent",
            "sent mail",
            "sendt",
            "spam",
            "junk",
            "søppelpost",
            "trash",
            "papirkurv",
            "deleted items",
            "archive",
            "arkiv",
        }
    )


def _folder_aliases(name: str) -> set[str]:
    normalized = _normalized_folder_name(name)
    aliases = {name.casefold(), normalized}
    if normalized in {"inbox", "innboks"}:
        aliases.update({"inbox", "innboks"})
    elif normalized in {"sent", "sent mail", "sendt"}:
        aliases.update({"sent", "sent mail", "sendt"})
    elif normalized in {"spam", "junk", "søppelpost"}:
        aliases.update({"spam", "junk", "søppelpost"})
    elif normalized in {"trash", "papirkurv", "deleted items"}:
        aliases.update({"trash", "papirkurv", "deleted items"})
    return aliases


def _friendly_error_message(error: Exception | str) -> str:
    if isinstance(error, str):
        return error or "Unknown error."
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "SMTP sign-in failed. Check the account/OAuth settings and try again."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The SMTP server rejected the recipient."
    if isinstance(error, smtplib.SMTPException):
        return f"SMTP error: {error}"
    if isinstance(error, OAuthCallbackError):
        return f"OAuth error: {error}"
    if isinstance(error, TimeoutError):
        return "The connection timed out."
    if isinstance(error, OSError):
        return f"Network error: {error}"
    return str(error) or error.__class__.__name__


def _send_status_text(result: SendResult) -> str:
    if result.local_copy_deferred:
        return "Email sent. The server's sent copy will be fetched during sync."
    if not result.local_copy_saved:
        return "Email sent, but saving locally failed. Do not send again."
    if not result.server_copy_attempted:
        return "Email sent. The mail server handles the sent copy."
    if result.server_copy_saved:
        return "Email sent and saved in Sent."
    return "Email sent, but saving a server copy in Sent failed."


def _normalized_folder_name(name: str) -> str:
    return name.rsplit("/", maxsplit=1)[-1].casefold()


def _apply_provider_defaults(
    provider: str,
    imap_host: str | None,
    imap_port: int | None,
    imap_security: str,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_security: str,
) -> tuple[str, int, str, str, int, str]:
    defaults = get_mail_provider_defaults(provider)
    return (
        imap_host or defaults.imap_host,
        imap_port or defaults.imap_port,
        imap_security or defaults.imap_security,
        smtp_host or defaults.smtp_host,
        smtp_port or defaults.smtp_port,
        smtp_security or defaults.smtp_security,
    )
