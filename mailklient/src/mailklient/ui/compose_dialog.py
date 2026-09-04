"""Compose dialog for writing email."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from mailklient.domain import Account
from mailklient.services import ComposeDraft


class ComposeDialog(QDialog):
    """Small dialog for writing a mail draft."""

    def __init__(
        self,
        accounts: list[Account],
        draft: ComposeDraft,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ny e-post")
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
        self.recipients_edit.setPlaceholderText("mottaker@example.com")

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
        self.bold_button.setToolTip("Fet")
        self.bold_button.clicked.connect(self._toggle_bold)

        self.italic_button = QPushButton("I")
        self.italic_button.setObjectName("compose_italic_button")
        self.italic_button.setCheckable(True)
        self.italic_button.setFixedWidth(36)
        self.italic_button.setToolTip("Kursiv")
        self.italic_button.clicked.connect(self._toggle_italic)

        self.underline_button = QPushButton("U")
        self.underline_button.setObjectName("compose_underline_button")
        self.underline_button.setCheckable(True)
        self.underline_button.setFixedWidth(36)
        self.underline_button.setToolTip("Understreking")
        self.underline_button.clicked.connect(self._toggle_underline)

        format_layout = QHBoxLayout()
        format_layout.addWidget(self.bold_button)
        format_layout.addWidget(self.italic_button)
        format_layout.addWidget(self.underline_button)
        format_layout.addStretch()

        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("compose_attachment_list")
        self.attachment_list.setMaximumHeight(90)

        self.add_attachment_button = QPushButton("Legg ved...")
        self.add_attachment_button.setObjectName("compose_add_attachment_button")
        self.add_attachment_button.clicked.connect(self._add_attachments)

        self.remove_attachment_button = QPushButton("Fjern valgt")
        self.remove_attachment_button.setObjectName("compose_remove_attachment_button")
        self.remove_attachment_button.clicked.connect(self._remove_selected_attachment)

        attachment_button_layout = QHBoxLayout()
        attachment_button_layout.addWidget(self.add_attachment_button)
        attachment_button_layout.addWidget(self.remove_attachment_button)
        attachment_button_layout.addStretch()

        self._attachment_paths = list(draft.attachment_paths)
        self._reload_attachment_list()

        form_layout = QFormLayout()
        form_layout.addRow("Fra", self.account_combo)
        form_layout.addRow("Til", self.recipients_edit)
        form_layout.addRow("Cc", self.cc_edit)
        form_layout.addRow("Bcc", self.bcc_edit)
        form_layout.addRow("Emne", self.subject_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.setObjectName("compose_button_box")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Send")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addLayout(form_layout)
        layout.addLayout(format_layout)
        layout.addWidget(self.body_edit)
        layout.addWidget(self.attachment_list)
        layout.addLayout(attachment_button_layout)
        layout.addWidget(buttons)

        self._in_reply_to = draft.in_reply_to

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
        weight = QFont.Weight.Bold if self.bold_button.isChecked() else QFont.Weight.Normal
        self.body_edit.setFontWeight(weight)

    def _toggle_italic(self) -> None:
        self.body_edit.setFontItalic(self.italic_button.isChecked())

    def _toggle_underline(self) -> None:
        self.body_edit.setFontUnderline(self.underline_button.isChecked())

    def _add_attachments(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Legg ved filer",
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

    def _reload_attachment_list(self) -> None:
        self.attachment_list.clear()
        for path in self._attachment_paths:
            self.attachment_list.addItem(Path(path).name)


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
