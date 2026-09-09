"""Choose or discard a locally saved draft."""

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
)

from mailklient.services.drafts import DraftService


class DraftDialog(QDialog):
    def __init__(self, service: DraftService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Drafts")
        self.resize(600, 360)
        self._service = service
        self.list = QListWidget()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Close
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Open).setText("Open")
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        self.delete_button = self.buttons.addButton(
            "Delete draft", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.delete_button.setIcon(QIcon.fromTheme("edit-delete"))
        self.delete_button.clicked.connect(self._delete)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.list.itemDoubleClicked.connect(self.accept)
        self.list.currentRowChanged.connect(self._update_buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addWidget(self.buttons)
        self._reload()

    def selected(self):
        row = self.list.currentRow()
        return self._drafts[row] if row >= 0 else None

    def _update_buttons(self):
        enabled = self.selected() is not None
        self.buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _reload(self):
        self.list.clear()
        self._drafts = self._service.list_drafts()
        for item in self._drafts:
            suffix = (
                " [check Sent]" if item.state in {"sending", "uncertain"} else ""
            )
            self.list.addItem(
                f"{item.draft.subject or '(no subject)'} | {item.draft.recipients}{suffix}"
            )
        self.list.setCurrentRow(0)
        self._update_buttons()

    def _delete(self):
        item = self.selected()
        if (
            item
            and QMessageBox.question(
                self, "Delete draft", "Delete this local draft?"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self._service.delete(item.id)
            self._reload()
