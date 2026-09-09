"""Recognize standard folder names without changing IMAP wire identifiers."""

from __future__ import annotations


def standard_folder_name(name: str) -> str | None:
    """Return a display name for a known root or Gmail system folder."""
    prefix, separator, leaf = name.rpartition("/")
    if separator and prefix.casefold() not in {"[gmail]", "[google mail]"}:
        return None
    normalized = (leaf if separator else name).casefold()
    groups = (
        ("Innboks", {"inbox", "innboks"}),
        (
            "Sendt",
            {
                "sent",
                "sent mail",
                "sent items",
                "sendt",
                "sendt e-post",
                "sendte elementer",
            },
        ),
        (
            "Søppelpost",
            {
                "spam",
                "junk",
                "junk email",
                "junk e-mail",
                "søppelpost",
                "s&apg-ppelpost",
            },
        ),
        (
            "Papirkurv",
            {"trash", "deleted", "deleted items", "papirkurv", "slettede elementer"},
        ),
        ("Arkiv", {"archive", "archives", "arkiv"}),
    )
    for display_name, aliases in groups:
        if normalized in aliases:
            return display_name
    return None
