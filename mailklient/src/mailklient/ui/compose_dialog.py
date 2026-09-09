"""Compose dialog for writing email."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from mailklient.domain import Account
from mailklient.services import ComposeDraft
from mailklient.services.drafts import DraftService


class ComposeDialog(QDialog):
    """Small dialog for writing a mail draft."""

    def __init__(
        self,
        accounts: list[Account],
        draft: ComposeDraft,
        parent=None,
        *,
        draft_service: DraftService | None = None,
        draft_id: int | None = None,
        uncertain: bool = False,
    ) -> None:
        super().__init__(parent)
        self._draft_service = draft_service
        self.draft_id = draft_id
        self._uncertain = uncertain
        self.setWindowTitle("New email")
        self.resize(720, 560)
        self.setStyleSheet(_COMPOSE_DIALOG_STYLESHEET)

        self.account_combo = QComboBox()
        self.account_combo.setObjectName("compose_account_combo")
        for account in accounts:
            self.account_combo.addItem(account.email_address, account.id)

        selected_index = self.account_combo.findData(draft.account_id)
        if selected_index >= 0:
            self.account_combo.setCurrentIndex(selected_index)

        self.recipients_edit = QLineEdit(draft.recipients)
        self.recipients_edit.setObjectName("compose_recipients_edit")
        self.recipients_edit.setPlaceholderText("recipient@example.com")

        self.cc_edit = QLineEdit(draft.cc)
        self.cc_edit.setObjectName("compose_cc_edit")

        self.bcc_edit = QLineEdit(draft.bcc)
        self.bcc_edit.setObjectName("compose_bcc_edit")

        self.subject_edit = QLineEdit(draft.subject)
        self.subject_edit.setObjectName("compose_subject_edit")

        self.body_edit = QTextEdit()
        self.body_edit.setObjectName("compose_body_edit")
        self.body_edit.setAcceptRichText(True)
        if draft.body_html:
            self.body_edit.setHtml(draft.body_html)
        else:
            self.body_edit.setPlainText(draft.body_text)

        self.bold_button = QPushButton("B")
        self.bold_button.setObjectName("compose_bold_button")
        self.bold_button.setCheckable(True)
        self.bold_button.setFixedWidth(36)
        self.bold_button.setToolTip("Bold")
        self.bold_button.clicked.connect(self._toggle_bold)

        self.italic_button = QPushButton("I")
        self.italic_button.setObjectName("compose_italic_button")
        self.italic_button.setCheckable(True)
        self.italic_button.setFixedWidth(36)
        self.italic_button.setToolTip("Italic")
        self.italic_button.clicked.connect(self._toggle_italic)

        self.underline_button = QPushButton("U")
        self.underline_button.setObjectName("compose_underline_button")
        self.underline_button.setCheckable(True)
        self.underline_button.setFixedWidth(36)
        self.underline_button.setToolTip("Underline")
        self.underline_button.clicked.connect(self._toggle_underline)

        format_layout = QHBoxLayout()
        format_layout.addWidget(self.bold_button)
        format_layout.addWidget(self.italic_button)
        format_layout.addWidget(self.underline_button)
        format_layout.addStretch()

        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("compose_attachment_list")
        self.attachment_list.setMaximumHeight(90)

        self.add_attachment_button = QPushButton("Attach...")
        self.add_attachment_button.setObjectName("compose_add_attachment_button")
        self.add_attachment_button.clicked.connect(self._add_attachments)

        self.remove_attachment_button = QPushButton("Remove selected")
        self.remove_attachment_button.setObjectName("compose_remove_attachment_button")
        self.remove_attachment_button.clicked.connect(self._remove_selected_attachment)

        attachment_button_layout = QHBoxLayout()
        attachment_button_layout.addWidget(self.add_attachment_button)
        attachment_button_layout.addWidget(self.remove_attachment_button)
        attachment_button_layout.addStretch()

        self._attachment_paths = list(draft.attachment_paths)
        self._reload_attachment_list()

        form_layout = QFormLayout()
        form_layout.addRow("From", self.account_combo)
        form_layout.addRow("To", self.recipients_edit)
        form_layout.addRow("Cc", self.cc_edit)
        form_layout.addRow("Bcc", self.bcc_edit)
        form_layout.addRow("Subject", self.subject_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.setObjectName("compose_button_box")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Send")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if draft_service is not None:
            save_button = buttons.addButton(
                "Save draft", QDialogButtonBox.ButtonRole.ActionRole
            )
            save_button.setIcon(QIcon.fromTheme("document-save"))
            save_button.clicked.connect(self._save_draft)
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Close")
        self.save_status = QLabel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addLayout(form_layout)
        layout.addLayout(format_layout)
        layout.addWidget(self.body_edit)
        layout.addWidget(self.attachment_list)
        layout.addLayout(attachment_button_layout)
        layout.addWidget(buttons)
        layout.addWidget(self.save_status)

        self._in_reply_to = draft.in_reply_to
        self._autosave = QTimer(self)
        self._autosave.setInterval(2000)
        self._autosave.setSingleShot(True)
        self._autosave.timeout.connect(self._save_draft)
        for edit in (
            self.recipients_edit,
            self.cc_edit,
            self.bcc_edit,
            self.subject_edit,
            self.body_edit,
        ):
            edit.textChanged.connect(lambda *_args: self._autosave.start())
        self.account_combo.currentIndexChanged.connect(lambda: self._autosave.start())

    def _save_draft(self) -> bool:
        if self._draft_service is None:
            return True
        try:
            self.draft_id = self._draft_service.save(self.draft(), self.draft_id)
            self._attachment_paths = list(
                self._draft_service.get(self.draft_id).draft.attachment_paths
            )
            self._reload_attachment_list(autosave=False)
        except Exception:
            self.save_status.setText("Could not save draft. Keep this window open.")
            return False
        self.save_status.setText("Draft saved locally")
        return True

    def accept(self) -> None:
        if not self.recipients_edit.text().strip():
            self.save_status.setText("Enter at least one recipient.")
            return
        if (
            self._uncertain
            and QMessageBox.question(
                self,
                "Check Sent",
                "The previous send has an unknown outcome. Have you checked Sent and do you want to send this draft again?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if self._save_draft():
            self._autosave.stop()
            super().accept()

    def reject(self) -> None:
        if self._save_draft():
            self._autosave.stop()
            super().reject()

    def draft(self) -> ComposeDraft:
        """Return the current compose form as draft data."""
        return ComposeDraft(
            account_id=self.account_combo.currentData(),
            recipients=self.recipients_edit.text().strip(),
            cc=self.cc_edit.text().strip(),
            bcc=self.bcc_edit.text().strip(),
            subject=self.subject_edit.text().strip(),
            body_text=self.body_edit.toPlainText(),
            body_html=self.body_edit.toHtml(),
            attachment_paths=tuple(self._attachment_paths),
            in_reply_to=self._in_reply_to,
        )

    def _toggle_bold(self) -> None:
        weight = (
            QFont.Weight.Bold if self.bold_button.isChecked() else QFont.Weight.Normal
        )
        self.body_edit.setFontWeight(weight)

    def _toggle_italic(self) -> None:
        self.body_edit.setFontItalic(self.italic_button.isChecked())

    def _toggle_underline(self) -> None:
        self.body_edit.setFontUnderline(self.underline_button.isChecked())

    def _add_attachments(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Attach files",
        )
        for path in paths:
            if path not in self._attachment_paths:
                self._attachment_paths.append(path)
        self._reload_attachment_list()

    def _remove_selected_attachment(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self.attachment_list.selectedIndexes()},
            reverse=True,
        )
        for row in selected_rows:
            del self._attachment_paths[row]
        self._reload_attachment_list()

    def _reload_attachment_list(self, *, autosave: bool = True) -> None:
        self.attachment_list.clear()
        for path in self._attachment_paths:
            self.attachment_list.addItem(Path(path).name)
        if autosave and hasattr(self, "_autosave"):
            self._autosave.start()


_COMPOSE_DIALOG_STYLESHEET = """
QDialog {
    background: #eff3f7;
    color: #232629;
    font-family: "Noto Sans", "Inter", "Segoe UI", sans-serif;
    font-size: 10pt;
}

QLineEdit,
QTextEdit,
QComboBox,
QListWidget {
    background: #fcfcfd;
    border: 1px solid #b8c2cc;
    border-radius: 6px;
    color: #232629;
    selection-background-color: #3daee9;
}

QLineEdit,
QComboBox {
    min-height: 30px;
    padding: 4px 8px;
}

QTextEdit {
    padding: 8px;
}

QLineEdit:focus,
QTextEdit:focus,
QComboBox:focus {
    border: 1px solid #3daee9;
}

QListWidget {
    min-height: 58px;
    max-height: 92px;
}

QListWidget::item {
    border-radius: 4px;
    margin: 2px;
    padding: 6px 8px;
}

QListWidget::item:selected {
    background: #d8edf9;
    color: #14384b;
}

QPushButton {
    background: #fcfcfd;
    border: 1px solid #b8c2cc;
    border-radius: 6px;
    color: #232629;
    font-weight: 500;
    min-height: 30px;
    padding: 4px 10px;
}

QPushButton:hover {
    background: #e7f4fd;
    border: 1px solid #3daee9;
}

QPushButton:checked,
QPushButton:pressed {
    background: #cbe7f7;
    border: 1px solid #2586bd;
}

QPushButton:disabled {
    background: #eef2f6;
    border: 1px solid #d7dee7;
    color: #9aa8b5;
}
"""
