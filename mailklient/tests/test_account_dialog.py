from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit

from mailklient.ui.account_dialog import AccountDialog, AccountDialogData


def _get_qapplication() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_account_dialog_returns_trimmed_data() -> None:
    _get_qapplication()
    dialog = AccountDialog()

    dialog.display_name_edit.setText("  Privat  ")
    dialog.email_address_edit.setText("  privat@example.com  ")
    dialog.password_edit.setText("  hemmelig  ")
    dialog.username_edit.setText("  privatbruker  ")
    dialog.imap_host_edit.setText("  imap.example.com  ")
    dialog.imap_port_spin.setValue(993)
    dialog.imap_security_combo.setCurrentIndex(0)
    dialog.smtp_host_edit.setText("  smtp.example.com  ")
    dialog.smtp_port_spin.setValue(587)
    dialog.smtp_security_combo.setCurrentIndex(1)

    assert dialog.account_data() == AccountDialogData(
        display_name="Privat",
        email_address="privat@example.com",
        auth_method="password",
        oauth_provider=None,
        username="privatbruker",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
        password="hemmelig",
    )

    dialog.close()


def test_account_dialog_password_field_is_masked() -> None:
    _get_qapplication()
    dialog = AccountDialog()

    assert dialog.password_edit.echoMode() == QLineEdit.EchoMode.Password

    dialog.close()


def test_account_dialog_treats_empty_server_settings_as_not_set() -> None:
    _get_qapplication()
    dialog = AccountDialog()

    dialog.display_name_edit.setText("Privat")
    dialog.email_address_edit.setText("privat@example.com")

    assert dialog.account_data() == AccountDialogData(
        display_name="Privat",
        email_address="privat@example.com",
        auth_method="password",
        oauth_provider=None,
        username=None,
        imap_host=None,
        imap_port=None,
        imap_security="ssl",
        smtp_host=None,
        smtp_port=None,
        smtp_security="starttls",
        password=None,
    )

    dialog.close()


def test_account_dialog_can_choose_oauth2_auth_method() -> None:
    _get_qapplication()
    dialog = AccountDialog()

    dialog.display_name_edit.setText("Privat")
    dialog.email_address_edit.setText("privat@example.com")
    dialog.auth_method_combo.setCurrentIndex(1)

    assert dialog.account_data().auth_method == "oauth2"
    assert dialog.account_data().oauth_provider == "gmail"
    assert dialog.oauth_provider_combo.isEnabled()

    dialog.close()


def test_account_dialog_ok_button_requires_values() -> None:
    _get_qapplication()
    dialog = AccountDialog()
    ok_button = dialog.button_box.button(QDialogButtonBox.StandardButton.Ok)

    assert not ok_button.isEnabled()

    dialog.display_name_edit.setText("Privat")
    assert not ok_button.isEnabled()

    dialog.email_address_edit.setText("privat@example.com")
    assert ok_button.isEnabled()

    dialog.close()
