"""Dialog for adding a local account."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


@dataclass(frozen=True, slots=True)
class AccountDialogData:
    """Account settings collected by the dialog."""

    display_name: str
    email_address: str
    auth_method: str
    oauth_provider: str | None
    username: str | None
    imap_host: str | None
    imap_port: int | None
    imap_security: str
    smtp_host: str | None
    smtp_port: int | None
    smtp_security: str
    password: str | None


class AccountDialog(QDialog):
    """Collect basic local account information."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Legg til konto")

        self.display_name_edit = QLineEdit()
        self.display_name_edit.setObjectName("display_name_edit")

        self.email_address_edit = QLineEdit()
        self.email_address_edit.setObjectName("email_address_edit")

        self.auth_method_combo = QComboBox()
        self.auth_method_combo.setObjectName("auth_method_combo")
        self.auth_method_combo.addItem("Passord", "password")
        self.auth_method_combo.addItem("OAuth2", "oauth2")

        self.oauth_provider_combo = QComboBox()
        self.oauth_provider_combo.setObjectName("oauth_provider_combo")
        self.oauth_provider_combo.addItem("Ikke satt", None)
        self.oauth_provider_combo.addItem("Gmail", "gmail")
        self.oauth_provider_combo.addItem("Outlook", "outlook")

        self.password_edit = QLineEdit()
        self.password_edit.setObjectName("password_edit")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.username_edit = QLineEdit()
        self.username_edit.setObjectName("username_edit")

        self.imap_host_edit = QLineEdit()
        self.imap_host_edit.setObjectName("imap_host_edit")

        self.imap_port_spin = _build_port_spin_box("imap_port_spin")
        self.imap_security_combo = _build_security_combo("imap_security_combo")
        self.imap_security_combo.setCurrentIndex(0)

        self.smtp_host_edit = QLineEdit()
        self.smtp_host_edit.setObjectName("smtp_host_edit")

        self.smtp_port_spin = _build_port_spin_box("smtp_port_spin")
        self.smtp_security_combo = _build_security_combo("smtp_security_combo")
        self.smtp_security_combo.setCurrentIndex(1)

        account_group = QGroupBox("Konto")
        account_group.setObjectName("account_group")
        account_layout = QFormLayout(account_group)
        account_layout.addRow("Visningsnavn", self.display_name_edit)
        account_layout.addRow("E-postadresse", self.email_address_edit)
        account_layout.addRow("Innlogging", self.auth_method_combo)
        account_layout.addRow("OAuth-provider", self.oauth_provider_combo)
        account_layout.addRow("Passord", self.password_edit)
        account_layout.addRow("Brukernavn", self.username_edit)

        server_group = QGroupBox("Server")
        server_group.setObjectName("server_group")
        server_layout = QFormLayout(server_group)
        server_layout.addRow("IMAP-server", self.imap_host_edit)
        server_layout.addRow("IMAP-port", self.imap_port_spin)
        server_layout.addRow("IMAP-sikkerhet", self.imap_security_combo)
        server_layout.addRow("SMTP-server", self.smtp_host_edit)
        server_layout.addRow("SMTP-port", self.smtp_port_spin)
        server_layout.addRow("SMTP-sikkerhet", self.smtp_security_combo)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.setObjectName("account_dialog_buttons")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(account_group)
        layout.addWidget(server_group)
        layout.addWidget(self.button_box)

        self.display_name_edit.textChanged.connect(self._update_ok_button)
        self.email_address_edit.textChanged.connect(self._update_ok_button)
        self.auth_method_combo.currentIndexChanged.connect(self._update_auth_fields)
        self._update_auth_fields()
        self._update_ok_button()

    def account_data(self) -> AccountDialogData:
        """Return entered account settings."""
        auth_method = self.auth_method_combo.currentData()
        oauth_provider = (
            self.oauth_provider_combo.currentData()
            if auth_method == "oauth2"
            else None
        )
        return AccountDialogData(
            display_name=self.display_name_edit.text().strip(),
            email_address=self.email_address_edit.text().strip(),
            auth_method=auth_method,
            oauth_provider=oauth_provider,
            username=_optional_text(self.username_edit),
            imap_host=_optional_text(self.imap_host_edit),
            imap_port=_optional_port(self.imap_port_spin),
            imap_security=self.imap_security_combo.currentData(),
            smtp_host=_optional_text(self.smtp_host_edit),
            smtp_port=_optional_port(self.smtp_port_spin),
            smtp_security=self.smtp_security_combo.currentData(),
            password=_optional_text(self.password_edit),
        )

    def _update_ok_button(self) -> None:
        data = self.account_data()
        ok_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(bool(data.display_name and data.email_address))

    def _update_auth_fields(self) -> None:
        uses_oauth = self.auth_method_combo.currentData() == "oauth2"
        self.oauth_provider_combo.setEnabled(uses_oauth)
        if uses_oauth and self.oauth_provider_combo.currentData() is None:
            self.oauth_provider_combo.setCurrentIndex(1)
        if not uses_oauth:
            self.oauth_provider_combo.setCurrentIndex(0)


def _build_port_spin_box(object_name: str) -> QSpinBox:
    spin_box = QSpinBox()
    spin_box.setObjectName(object_name)
    spin_box.setRange(0, 65535)
    spin_box.setSpecialValueText("Ikke satt")
    return spin_box


def _build_security_combo(object_name: str) -> QComboBox:
    combo_box = QComboBox()
    combo_box.setObjectName(object_name)
    combo_box.addItem("SSL", "ssl")
    combo_box.addItem("STARTTLS", "starttls")
    return combo_box


def _optional_text(line_edit: QLineEdit) -> str | None:
    value = line_edit.text().strip()
    return value or None


def _optional_port(spin_box: QSpinBox) -> int | None:
    value = spin_box.value()
    return value if value > 0 else None
