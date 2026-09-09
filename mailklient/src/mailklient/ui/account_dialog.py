"""Dialog for adding or editing a local account."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from mailklient.domain import Account
from mailklient.mail.config import get_mail_provider_defaults
from mailklient.mail.tuta_bridge import default_certificate_path


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
    password: str | None = field(repr=False)
    provider: str = "imap"
    local_certificate: str | None = None


class AccountDialog(QDialog):
    """Collect basic local account information."""

    def __init__(self, parent=None, *, account: Account | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Add account")
        self.setMinimumWidth(520)

        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("provider_combo")
        self.provider_combo.addItem("Other IMAP", "imap")
        self.provider_combo.addItem("Gmail", "gmail")
        self.provider_combo.addItem("Tuta (local bridge)", "tuta")
        self.provider_combo.addItem("Outlook", "outlook")

        self.display_name_edit = QLineEdit()
        self.display_name_edit.setObjectName("display_name_edit")

        self.email_address_edit = QLineEdit()
        self.email_address_edit.setObjectName("email_address_edit")

        self.auth_method_combo = QComboBox()
        self.auth_method_combo.setObjectName("auth_method_combo")
        self.auth_method_combo.addItem("Password", "password")
        self.auth_method_combo.addItem("OAuth2", "oauth2")

        self.oauth_provider_combo = QComboBox()
        self.oauth_provider_combo.setObjectName("oauth_provider_combo")
        self.oauth_provider_combo.addItem("Not set", None)
        self.oauth_provider_combo.addItem("Gmail", "gmail")
        self.oauth_provider_combo.addItem("Outlook", "outlook")

        self.password_edit = QLineEdit()
        self.password_edit.setObjectName("password_edit")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_label = QLabel("Password")

        self.certificate_edit = QLineEdit(str(default_certificate_path()))
        self.certificate_edit.setObjectName("certificate_edit")
        self.certificate_label = QLabel("Bridge certificate")

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

        account_group = QGroupBox("Account")
        account_group.setObjectName("account_group")
        account_layout = QFormLayout(account_group)
        account_layout.addRow("Account type", self.provider_combo)
        account_layout.addRow("Display name", self.display_name_edit)
        account_layout.addRow("Email address", self.email_address_edit)
        account_layout.addRow("Sign-in method", self.auth_method_combo)
        account_layout.addRow("OAuth provider", self.oauth_provider_combo)
        account_layout.addRow(self.password_label, self.password_edit)
        account_layout.addRow("Username", self.username_edit)
        account_layout.addRow(self.certificate_label, self.certificate_edit)

        server_group = QGroupBox("Server")
        server_group.setObjectName("server_group")
        self.server_group = server_group
        server_layout = QFormLayout(server_group)
        server_layout.addRow("IMAP server", self.imap_host_edit)
        server_layout.addRow("IMAP port", self.imap_port_spin)
        server_layout.addRow("IMAP security", self.imap_security_combo)
        server_layout.addRow("SMTP server", self.smtp_host_edit)
        server_layout.addRow("SMTP port", self.smtp_port_spin)
        server_layout.addRow("SMTP security", self.smtp_security_combo)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.setObjectName("account_dialog_buttons")
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(account_group)
        layout.addWidget(server_group)
        layout.addWidget(self.button_box)

        self.display_name_edit.textChanged.connect(self._update_ok_button)
        self.email_address_edit.textChanged.connect(self._update_ok_button)
        self.auth_method_combo.currentIndexChanged.connect(self._update_auth_fields)
        self.provider_combo.currentIndexChanged.connect(self._update_provider_fields)
        self._update_provider_fields()
        self._update_auth_fields()
        self._update_ok_button()
        if account is not None:
            self._load_account(account)

    def _load_account(self, account: Account) -> None:
        self.setWindowTitle("Edit account")
        self.provider_combo.setCurrentIndex(
            self.provider_combo.findData(account.provider)
        )
        self.auth_method_combo.setCurrentIndex(
            self.auth_method_combo.findData(account.auth_method)
        )
        self.oauth_provider_combo.setCurrentIndex(
            self.oauth_provider_combo.findData(account.oauth_provider)
        )
        self.display_name_edit.setText(account.display_name)
        self.email_address_edit.setText(account.email_address)
        self.username_edit.setText(account.username or "")
        self.imap_host_edit.setText(account.imap_host or "")
        self.smtp_host_edit.setText(account.smtp_host or "")
        self.imap_port_spin.setValue(account.imap_port or 0)
        self.smtp_port_spin.setValue(account.smtp_port or 0)
        self.imap_security_combo.setCurrentIndex(
            self.imap_security_combo.findData(account.imap_security)
        )
        self.smtp_security_combo.setCurrentIndex(
            self.smtp_security_combo.findData(account.smtp_security)
        )
        self.certificate_edit.setText(account.local_certificate or "")
        self.email_address_edit.setReadOnly(True)
        for combo in (
            self.provider_combo,
            self.auth_method_combo,
            self.oauth_provider_combo,
        ):
            combo.setEnabled(False)
        self.password_edit.setEnabled(account.auth_method == "password")
        self.password_edit.setPlaceholderText(
            "Unchanged" if account.auth_method == "password" else "OAuth"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Save")

    def account_data(self) -> AccountDialogData:
        """Return entered account settings."""
        is_tuta = self.provider_combo.currentData() == "tuta"
        auth_method = "password" if is_tuta else self.auth_method_combo.currentData()
        oauth_provider = (
            self.oauth_provider_combo.currentData() if auth_method == "oauth2" else None
        )
        return AccountDialogData(
            display_name=self.display_name_edit.text().strip(),
            email_address=self.email_address_edit.text().strip(),
            auth_method=auth_method,
            oauth_provider=oauth_provider,
            username=(
                self.email_address_edit.text().strip()
                if is_tuta
                else _optional_text(self.username_edit)
            ),
            imap_host=_optional_text(self.imap_host_edit),
            imap_port=_optional_port(self.imap_port_spin),
            imap_security=self.imap_security_combo.currentData(),
            smtp_host=_optional_text(self.smtp_host_edit),
            smtp_port=_optional_port(self.smtp_port_spin),
            smtp_security=self.smtp_security_combo.currentData(),
            password=(
                (self.password_edit.text() or None)
                if auth_method == "password"
                else None
            ),
            provider=self.provider_combo.currentData(),
            local_certificate=_optional_text(self.certificate_edit)
            if is_tuta
            else None,
        )

    def _update_provider_fields(self) -> None:
        provider = self.provider_combo.currentData()
        is_tuta = provider == "tuta"
        self.certificate_edit.setVisible(is_tuta)
        self.certificate_label.setVisible(is_tuta)
        self.server_group.setEnabled(not is_tuta)
        self.username_edit.setEnabled(not is_tuta)
        self.auth_method_combo.setEnabled(not is_tuta)
        self.password_label.setText("Bridge password" if is_tuta else "Password")
        self.password_edit.setPlaceholderText(
            "Password stored in keyring" if is_tuta else ""
        )
        if is_tuta:
            self.auth_method_combo.setCurrentIndex(0)
            self.imap_host_edit.setText("127.0.0.1")
            self.smtp_host_edit.setText("127.0.0.1")
            self.imap_port_spin.setValue(1143)
            self.smtp_port_spin.setValue(1025)
            self.imap_security_combo.setCurrentIndex(0)
            self.smtp_security_combo.setCurrentIndex(0)
        elif provider in {"gmail", "outlook"}:
            defaults = get_mail_provider_defaults(provider)
            self.auth_method_combo.setCurrentIndex(1)
            self.oauth_provider_combo.setCurrentIndex(
                self.oauth_provider_combo.findData(provider)
            )
            self.imap_host_edit.setText(defaults.imap_host)
            self.smtp_host_edit.setText(defaults.smtp_host)
            self.imap_port_spin.setValue(defaults.imap_port)
            self.smtp_port_spin.setValue(defaults.smtp_port)
            self.imap_security_combo.setCurrentIndex(0)
            self.smtp_security_combo.setCurrentIndex(1)
        self._update_auth_fields()

    def _update_ok_button(self) -> None:
        data = self.account_data()
        if data.provider == "tuta":
            self.username_edit.setText(data.username or "")
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
    spin_box.setSpecialValueText("Not set")
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
