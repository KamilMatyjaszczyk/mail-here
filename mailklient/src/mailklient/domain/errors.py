"""Stable errors for programmatic, read-only mail access."""


class MailReadError(Exception):
    """Base error safe to present without database/provider diagnostics."""


class InvalidSearchQuery(MailReadError):
    """An identifier, filter or pagination argument is invalid."""


class EmailNotFound(MailReadError):
    """The requested message is not in the local cache."""


class ThreadNotFound(MailReadError):
    """The requested thread anchor is not in the local cache."""


class MailStoreUnavailable(MailReadError):
    """The local cache cannot currently be read."""
