"""Edit an existing account without replacing its identity or credentials."""

from __future__ import annotations

from mailklient.domain import Account
from mailklient.security import delete_password, get_password, save_password
from mailklient.services.mail_store import MailStore


class AccountSettingsService:
    def __init__(self, store: MailStore) -> None:
        self._store = store

    def save(self, account: Account, password: str | None = None) -> Account:
        previous = self._store.get_account(account.id)
        if previous is None:
            raise ValueError("The account no longer exists.")
        if any(
            getattr(previous, field) != getattr(account, field)
            for field in ("email_address", "provider", "auth_method", "oauth_provider")
        ):
            raise ValueError(
                "Add a new account to use a different address or sign-in method."
            )
        if not account.display_name.strip():
            raise ValueError("Display name cannot be empty.")
        if account.auth_method == "oauth2" or not password:
            return self._store.update_account(account)

        old_password = get_password(account.email_address)
        save_password(account.email_address, password)
        try:
            return self._store.update_account(account)
        except Exception:
            if old_password is None:
                delete_password(account.email_address)
            else:
                save_password(account.email_address, old_password)
            raise
