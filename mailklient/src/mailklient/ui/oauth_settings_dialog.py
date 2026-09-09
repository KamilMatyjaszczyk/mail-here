"""Store desktop OAuth application registration settings in the keyring."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from mailklient.security.oauth_client_config import (
    OAuthClientConfigError,
    load_oauth_client_config,
    save_oauth_client_config,
)


class OAuthSettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OAuth app settings")
        self.setMinimumWidth(480)
        self.provider = QComboBox()
        self.provider.addItem("Gmail", "gmail")
        self.provider.addItem("Outlook", "outlook")
        self.client_id = QLineEdit()
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret.setPlaceholderText("Unchanged")
        self.clear_secret = QCheckBox("Remove stored client secret")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QFormLayout(self)
        layout.addRow("Provider", self.provider)
        layout.addRow("Your client ID", self.client_id)
        layout.addRow("Client secret", self.client_secret)
        layout.addRow(self.clear_secret)
        layout.addRow(self.status)
        layout.addRow(self.buttons)
        self.provider.currentIndexChanged.connect(self._load)
        self._load()

    def _load(self) -> None:
        self.client_id.clear()
        self.client_secret.clear()
        self.clear_secret.setChecked(False)
        self.status.clear()
        try:
            client_id, _secret = load_oauth_client_config(self.provider.currentData())
            self.client_id.setText(client_id)
        except OAuthClientConfigError:
            self.status.setText("Could not read the keyring.")

    def accept(self) -> None:
        try:
            provider = self.provider.currentData()
            old_id, old_secret = load_oauth_client_config(provider)
            secret = self.client_secret.text() or (
                old_secret if old_id == self.client_id.text().strip() else None
            )
            if self.clear_secret.isChecked():
                secret = None
            save_oauth_client_config(provider, self.client_id.text(), secret)
        except OAuthClientConfigError:
            self.status.setText(
                "Could not save. Check the client ID and keyring."
            )
            return
        super().accept()
