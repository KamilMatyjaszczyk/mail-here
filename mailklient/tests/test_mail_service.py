"""The application contract can be used with a repository other than SQLite."""

import sqlite3
from unittest.mock import Mock

import pytest

from mailklient.database.mail_reader import MailReadRepository
from mailklient.domain.errors import InvalidSearchQuery, MailStoreUnavailable
from mailklient.domain.mail_queries import EmailDetails, EmailFilters, EmailPage
from mailklient.domain.mail_repository import MailRepository
from mailklient.domain.models import Message
from mailklient.services import MailService
from mailklient.services.mail_read import MailReadService


def test_injected_repository_needs_no_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An injected repository must not open SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    repository = Mock(spec=MailRepository)
    service = MailService(repository=repository)
    message = Message(7, 2, 3)
    page = EmailPage((message,), 10, 0, None)
    repository.search.return_value = page
    repository.get_email.return_value = EmailDetails(message, 7, ())
    repository.get_attachments.return_value = ()
    filters = EmailFilters(account_id=2, mailbox="INBOX")

    assert service.get_recent_emails(filters=filters, limit=10) is page
    assert repository.search.call_args.args == (
        "", EmailFilters(account_id=2, mailbox="Innboks")
    )
    assert repository.search.call_args.kwargs["sort_order"] == "date_desc"
    assert service.search_emails("  Invoice  ", filters, limit=10) is page
    assert repository.search.call_args.args[0] == "Invoice"
    assert service.get_unread_emails(filters=filters, limit=10) is page
    assert repository.search.call_args.args[1].is_read is False
    assert filters.is_read is None
    assert service.get_email(7).message is message
    assert service.get_attachments(7) == ()
    assert service.get_thread(7, limit=10) is page
    repository.search.assert_called_with(
        "", EmailFilters(thread_id=7), limit=10, offset=0,
        sort_order="date_asc", require_thread=True,
    )


@pytest.mark.parametrize("operation, kwargs", [
    ("get_recent_emails", {"limit": 0}),
    ("search_emails", {"filters": EmailFilters(account_id=True)}),
    ("get_unread_emails", {"filters": EmailFilters(is_read=True)}),
    ("get_email", {"email_id": "7"}),
    ("get_thread", {"thread_id": 7, "offset": -1}),
])
def test_invalid_requests_never_reach_repository(operation, kwargs):
    repository = Mock(spec=MailRepository)
    service = MailService(repository=repository)
    with pytest.raises(InvalidSearchQuery):
        getattr(service, operation)(**kwargs)
    assert repository.mock_calls == []


def test_constructor_requires_one_source_and_preserves_legacy_import(tmp_path):
    assert MailReadService is MailService
    with pytest.raises(ValueError, match="exactly one"):
        MailService()
    with pytest.raises(ValueError, match="exactly one"):
        MailService(tmp_path / "cache.db", repository=Mock(spec=MailRepository))


def test_iteration_is_lazy_and_propagates_failure_on_later_page():
    repository = Mock(spec=MailRepository)
    service = MailService(repository=repository)
    message = Message(1, 1, 1)
    repository.search.side_effect = [
        EmailPage((message,), 1, 0, 1),
        MailStoreUnavailable("Cache unavailable."),
    ]
    messages = service.iter_emails("invoice", EmailFilters(account_id=1), page_size=1)
    repository.search.assert_not_called()
    assert next(messages) is message
    with pytest.raises(MailStoreUnavailable):
        next(messages)
    assert repository.search.call_count == 2
    assert repository.search.call_args.kwargs["offset"] == 1
    assert repository.search.call_args.args == ("invoice", EmailFilters(account_id=1))


@pytest.mark.parametrize("operation, args", [
    ("get_recent_emails", ()), ("search_emails", ("invoice",)),
    ("get_unread_emails", ()), ("get_email", (1,)), ("get_thread", (1,)),
])
def test_repository_sanitizes_sqlite_failures(tmp_path, monkeypatch, operation, args):
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("private database path and diagnostics")

    repository = MailReadRepository(tmp_path / "cache.db")
    service = MailService(repository=repository)
    monkeypatch.setattr(sqlite3, "connect", unavailable)
    with pytest.raises(MailStoreUnavailable) as caught:
        getattr(service, operation)(*args)
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__
