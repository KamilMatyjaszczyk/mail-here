"""Local demo data for early development."""

from __future__ import annotations

from mailklient.services.mail_store import MailStore


def seed_demo_data(store: MailStore) -> None:
    """Add local demo data if the cache is empty."""
    if store.list_accounts():
        return

    account = store.add_account("Demo", "demo@example.com")
    inbox = store.add_folder(account.id, "Innboks")
    sent = store.add_folder(account.id, "Sendt")
    store.add_folder(account.id, "Søppelpost")
    store.add_folder(account.id, "Papirkurv")

    store.add_message(
        account.id,
        inbox.id,
        message_id="<welcome@example.com>",
        subject="Velkommen til Mailklient",
        sender="demo@example.com",
        recipients="deg@example.com",
        received_at="2026-01-03T12:00:00",
        body_preview="Dette er lokale demo-data fra SQLite.",
    )
    store.add_message(
        account.id,
        inbox.id,
        message_id="<local-cache@example.com>",
        subject="Lokal cache er koblet til GUI",
        sender="demo@example.com",
        recipients="deg@example.com",
        received_at="2026-01-02T12:00:00",
        is_read=True,
        body_preview="Vinduet viser nå data via MailStore.",
    )
    store.add_message(
        account.id,
        sent.id,
        message_id="<sent-demo@example.com>",
        subject="Sendt demo-melding",
        sender="deg@example.com",
        recipients="demo@example.com",
        sent_at="2026-01-01T12:00:00",
        body_preview="Dette er bare lokal demo-data.",
    )
