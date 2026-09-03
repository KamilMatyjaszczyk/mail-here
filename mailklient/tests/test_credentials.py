from __future__ import annotations

import keyring

from mailklient.security import delete_password, get_password, save_password
from mailklient.security.credentials import SERVICE_NAME


def test_credentials_save_get_and_delete_password(monkeypatch) -> None:
    saved_passwords: dict[tuple[str, str], str] = {}

    def fake_set_password(service_name: str, username: str, password: str) -> None:
        saved_passwords[(service_name, username)] = password

    def fake_get_password(service_name: str, username: str) -> str | None:
        return saved_passwords.get((service_name, username))

    def fake_delete_password(service_name: str, username: str) -> None:
        del saved_passwords[(service_name, username)]

    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    save_password("  Person@Example.COM  ", "hemmelig")

    assert get_password("person@example.com") == "hemmelig"
    assert saved_passwords == {(SERVICE_NAME, "person@example.com"): "hemmelig"}

    delete_password("person@example.com")

    assert get_password("person@example.com") is None


def test_credentials_ignore_missing_password_on_delete(monkeypatch) -> None:
    def fake_delete_password(_service_name: str, _username: str) -> None:
        raise keyring.errors.PasswordDeleteError("missing")

    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    delete_password("missing@example.com")
