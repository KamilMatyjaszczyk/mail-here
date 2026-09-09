"""Attachment selection, preview, download and cache actions."""

from __future__ import annotations

import base64
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeDatabase, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox, QStyle

from mailklient.domain import Attachment
from mailklient.security.attachment_files import save_attachment
from mailklient.services.attachment_cache import AttachmentCache

if TYPE_CHECKING:
    from mailklient.ui.main_window import MainWindow


class AttachmentController:
    def __init__(self, window: MainWindow) -> None:
        self.window = window

    def show_dialog(self) -> None:
        if not self.window._attachments_by_id:
            return
        self.window.attachment_dialog.show()
        self.window.attachment_dialog.raise_()
        self.window.attachment_dialog.activateWindow()
        if self.window.attachment_list.currentRow() < 0:
            self.window.attachment_list.setCurrentRow(0)
        else:
            self._preview_selected_attachment()

    def preview_closed(self) -> None:
        self.window._pending_attachment_preview = None

    def _show_attachments(self, attachments: list[Attachment]) -> None:
        self.window.attachment_dialog.reject()
        self.window._pending_attachment_preview = None
        self.window._attachments_by_id = {
            attachment.id: attachment for attachment in attachments
        }
        has_attachments = bool(attachments)
        self.window.attachment_bar.setVisible(has_attachments)
        label = "attachment" if len(attachments) == 1 else "attachments"
        self.window.attachments_button.setText(f"{len(attachments)} {label}")
        self.window.attachment_list.setVisible(has_attachments)
        self.window.attachment_preview.setVisible(False)
        self.window.open_attachment_button.setVisible(has_attachments)
        self.window.save_attachment_button.setVisible(has_attachments)
        self.window.save_all_attachments_button.setVisible(has_attachments)
        self.window.attachment_list.clear()
        mime_database = QMimeDatabase()
        for attachment in attachments:
            suffix = "" if attachment.has_content else " - not cached locally"
            item = QListWidgetItem(
                f"{attachment.filename}\n"
                f"{_format_attachment_size(attachment.size)} - "
                f"{_display_content_type(attachment.content_type)}"
            )
            mime_type = mime_database.mimeTypeForName(attachment.content_type)
            item.setIcon(
                QIcon.fromTheme(
                    mime_type.iconName(),
                    self.window.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon),
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, attachment.id)
            item.setToolTip(f"{attachment.filename}\n{attachment.content_type}{suffix}")
            self.window.attachment_list.addItem(item)
        self._preview_selected_attachment()

    def _selected_attachment(self) -> Attachment | None:
        current = self.window.attachment_list.currentItem()
        if current is None:
            return None
        attachment_id = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(attachment_id, int):
            return None
        return self.window._attachments_by_id.get(attachment_id)

    def _update_attachment_actions(self) -> None:
        attachment = self._selected_attachment()
        idle = not (
            self.window._task_runner.busy
            or self.window._sync_in_progress
            or self.window._send_in_progress
            or self.window._manual_sync_batch
        )
        enabled = attachment is not None and idle
        self.window.open_attachment_button.setEnabled(enabled)
        self.window.save_attachment_button.setEnabled(enabled)
        self.window.save_all_attachments_button.setEnabled(
            bool(self.window._attachments_by_id) and idle
        )
        if idle and self.window._pending_attachment_preview is not None:
            QTimer.singleShot(0, self._resume_attachment_preview)

    def _resume_attachment_preview(self) -> None:
        pending = self.window._pending_attachment_preview
        self.window._pending_attachment_preview = None
        attachment = self._selected_attachment()
        if attachment is not None and attachment.id == pending:
            self._preview_selected_attachment()

    def _preview_selected_attachment(self) -> None:
        self.window._pending_attachment_preview = None
        attachment = self._selected_attachment()
        self._update_attachment_actions()
        if attachment is None:
            self.window.attachment_preview.setHtml("")
            self.window.attachment_preview.setVisible(False)
            return
        self.window.attachment_preview.setVisible(True)
        if not attachment.has_content:
            if (
                self.window._task_runner.busy
                or self.window._sync_in_progress
                or self.window._send_in_progress
                or self.window._manual_sync_batch
            ):
                self.window._pending_attachment_preview = attachment.id
                self.window.attachment_preview.setHtml(
                    "<p>Preview is waiting for the current operation to finish.</p>"
                )
                return
            self.window.attachment_preview.setHtml("<p>Fetching attachment...</p>")
            self.window._start_task(
                "Fetching attachment...",
                lambda: self.window._attachment_service.content(attachment),
                lambda content: self._attachment_loaded(attachment, content),
            )
            return

        content = self.window._mail_store.get_attachment_content(attachment.id)
        if content is None:
            self.window._attachments_by_id[attachment.id] = replace(
                attachment, has_content=False
            )
            self._preview_selected_attachment()
            return

        self.window.attachment_preview.setHtml(
            _attachment_preview_html(attachment, content)
        )

    def _attachment_loaded(self, attachment: Attachment, content: object) -> None:
        if not isinstance(content, bytes):
            return
        if attachment.id in self.window._attachments_by_id:
            cached = any(
                item.id == attachment.id and item.has_content
                for item in self.window._mail_store.list_attachments(
                    attachment.message_id
                )
            )
            self.window._attachments_by_id[attachment.id] = replace(
                attachment, has_content=cached, size=len(content)
            )
        selected = self._selected_attachment()
        if selected is not None and selected.id == attachment.id:
            self.window.attachment_preview.setHtml(
                _attachment_preview_html(attachment, content)
            )
            self._update_attachment_actions()
        self.window.sync_status_label.setText("Attachment fetched.")

    def _clear_attachment_cache(self) -> None:
        if (
            self.window._task_runner.busy
            or self.window._sync_in_progress
            or self.window._send_in_progress
        ):
            return
        cache = AttachmentCache(self.window._mail_store)
        usage = cache.usage()
        answer = QMessageBox.question(
            self.window,
            "Clear attachment cache",
            f"Attachments use {_format_attachment_size(usage.total_bytes)} locally. "
            f"Remove {_format_attachment_size(usage.removable_bytes)} "
            "that can be downloaded from the server again? "
            "Drafts, local messages and downloaded files will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.window._start_task(
                "Clearing attachment cache...",
                lambda: cache.trim(0, compact=True),
                lambda _size: self._cache_cleared(),
            )

    def _cache_cleared(self) -> None:
        self.window._rerender_selected_message()
        self.window.sync_status_label.setText("Attachment cache cleared.")

    def _save_selected_attachment(self) -> bool:
        attachment = self._selected_attachment()
        if attachment is None:
            return False
        save_path, _selected_filter = QFileDialog.getSaveFileName(
            self.window,
            "Save attachment",
            str(_download_directory() / _safe_filename(attachment.filename)),
        )
        if not save_path:
            return False

        def save() -> bool:
            save_attachment(
                Path(save_path), self.window._attachment_service.content(attachment)
            )
            return True

        return self.window._start_task(
            "Saving attachments...",
            save,
            lambda result: self.window._complete_task(result, "Attachment saved."),
        )

    def _save_all_available_attachments(self) -> bool:
        attachments = list(self.window._attachments_by_id.values())
        if not attachments:
            return False
        directory = QFileDialog.getExistingDirectory(
            self.window,
            "Save all attachments",
            str(_download_directory()),
        )
        if not directory:
            return False

        def save_all() -> int:
            count = 0
            for attachment in attachments:
                content = self.window._attachment_service.content(attachment)
                path = _unique_attachment_path(
                    Path(directory), _safe_filename(attachment.filename)
                )
                save_attachment(path, content, overwrite=False)
                count += 1
            return count

        return self.window._start_task(
            "Saving attachments...",
            save_all,
            lambda count: self.window.sync_status_label.setText(
                f"Saved {count} attachment{'s' if count != 1 else ''}."
            ),
        )

    def _open_selected_attachment(self) -> bool:
        attachment = self._selected_attachment()
        if attachment is None:
            return False

        def prepare() -> Path:
            return self.window._attachment_files.write(
                attachment.filename,
                self.window._attachment_service.content(attachment),
            )

        def open_file(path: object) -> None:
            if isinstance(path, Path) and QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path))
            ):
                self.window.sync_status_label.setText("Attachment opened.")
            else:
                self.window._handle_task_error("No application could open this file.")

        return self.window._start_task("Preparing attachment...", prepare, open_file)


def _download_directory() -> Path:
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )
    return Path(location) if location else Path.home() / "Downloads"


def _format_attachment_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _attachment_preview_html(attachment: Attachment, content: bytes) -> str:
    escaped_name = _html_escape(attachment.filename)
    escaped_type = _html_escape(attachment.content_type)
    size = _format_attachment_size(len(content))
    summary = (
        f"<p><strong>{escaped_name}</strong><br>"
        f"{_html_escape(_display_content_type(attachment.content_type))}, {size}</p>"
    )

    if attachment.content_type.startswith("image/"):
        encoded = base64.b64encode(content).decode("ascii")
        return (
            summary + f'<img src="data:{escaped_type};base64,{encoded}" '
            'style="max-width: 100%; max-height: 130px;">'
        )

    if attachment.content_type.startswith("text/"):
        return summary + f"<pre>{_html_escape(_decode_preview_text(content))}</pre>"

    if (
        attachment.content_type == "application/pdf"
        or attachment.filename.casefold().endswith(".pdf")
    ):
        page_count = _guess_pdf_page_count(content)
        page_text = (
            f"{page_count} page{'s' if page_count != 1 else ''}"
            if page_count is not None
            else "PDF document"
        )
        return (
            summary + f"<p>{page_text}. Use Open to preview in your PDF reader, "
            "or Save as to keep the file.</p>"
        )

    return (
        summary + "<p>Inline preview is not supported for this file type. "
        "Use Open or Save as.</p>"
    )


def _attachment_type_label(attachment: Attachment) -> str:
    content_type = attachment.content_type.casefold()
    filename = attachment.filename.casefold()
    if content_type.startswith("image/"):
        return "[IMAGE]"
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        return "[PDF]"
    if content_type.startswith("text/"):
        return "[TEXT]"
    if content_type.startswith("audio/"):
        return "[AUDIO]"
    if content_type.startswith("video/"):
        return "[VIDEO]"
    if "zip" in content_type or filename.endswith((".zip", ".tar", ".gz")):
        return "[ARCHIVE]"
    return "[FILE]"


def _display_content_type(content_type: str) -> str:
    labels = {
        "application/pdf": "PDF",
        "application/zip": "ZIP archive",
        "text/plain": "Text",
        "text/html": "HTML",
        "image/jpeg": "JPEG image",
        "image/png": "PNG image",
        "image/gif": "GIF image",
    }
    return labels.get(content_type.casefold(), content_type)


def _guess_pdf_page_count(content: bytes) -> int | None:
    if not content.startswith(b"%PDF"):
        return None
    matches = set()
    for match in re.finditer(rb"/Type\s*/Page\b", content):
        matches.add(match.start())
    if not matches:
        return None
    return len(matches)


def _decode_preview_text(content: bytes, max_length: int = 8000) -> str:
    preview = content[:max_length]
    for encoding in ("utf-8", "latin-1"):
        try:
            text = preview.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = preview.decode("utf-8", errors="replace")

    if len(content) > max_length:
        text += "\n\n..."
    return text


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _safe_filename(filename: str) -> str:
    safe_characters = []
    for character in filename:
        if character.isalnum() or character in {" ", ".", "-", "_"}:
            safe_characters.append(character)
        else:
            safe_characters.append("_")

    safe_name = "".join(safe_characters).strip(" .")
    return safe_name or "attachment"


def _unique_attachment_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    if not path.exists():
        return path

    stem = path.stem or "attachment"
    suffix = path.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
