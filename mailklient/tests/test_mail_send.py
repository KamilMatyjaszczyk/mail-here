from __future__ import annotations

import pytest

from mailklient.mail.config import (
    ImapSettings,
    MailAccountSettings,
    SmtpSettings,
)
from mailklient.services import ComposeDraft, MailSendService, MailStore, SendResult
from mailklient.services import mail_send


class FakeSmtpClient:
    instances: list[FakeSmtpClient] = []

    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings
        self.sent_messages: list[tuple[str, list[str], str, str, str | None]] = []
        self.instances.append(self)

    def send_message(
        self,
        sender: str,
        recipients: list[str],
        subject: str,
        body_text: str,
        *,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str = "",
        attachment_paths: list[str] | None = None,
        in_reply_to: str | None = None,
        message_id: str | None = None,
        date_header: str | None = None,
    ) -> bool:
        self.sent_messages.append(
            (sender, recipients, subject, body_text, in_reply_to)
        )
        self.cc = cc or []
        self.bcc = bcc or []
        self.body_html = body_html
        self.attachment_paths = attachment_paths or []
        self.message_id = message_id
        self.date_header = date_header
        return True


class FakeImapClient:
    instances: list[FakeImapClient] = []

    def __init__(self, settings: ImapSettings) -> None:
        self.settings = settings
        self.appended_messages = []
        self.instances.append(self)

    def append_message(
        self,
        folder_name: str,
        raw_message: bytes,
        *,
        flags: tuple[str, ...] = ("\\Seen",),
        internal_date: str | None = None,
    ) -> bool:
        self.appended_messages.append(
            (folder_name, raw_message, flags, internal_date)
        )
        return True


class FailingAppendImapClient(FakeImapClient):
    instances: list[FailingAppendImapClient] = []

    def __init__(self, settings: ImapSettings) -> None:
        super().__init__(settings)
        self.instances.append(self)

    def append_message(
        self,
        folder_name: str,
        raw_message: bytes,
        *,
        flags: tuple[str, ...] = ("\\Seen",),
        internal_date: str | None = None,
    ) -> bool:
        self.appended_messages.append(
            (folder_name, raw_message, flags, internal_date)
        )
        return False


def _settings_for(account_id: int, email_address: str) -> MailAccountSettings:
    return MailAccountSettings(
        account_id=account_id,
        email_address=email_address,
        imap=ImapSettings("imap.example.com", 993, email_address, "secret"),
        smtp=SmtpSettings("smtp.example.com", 587, email_address, "secret"),
    )


def test_mail_send_service_sends_draft_with_selected_account(
    tmp_path,
    monkeypatch,
) -> None:
    FakeSmtpClient.instances = []
    FakeImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account(
        "Privat",
        "privat@example.com",
        auth_method="oauth2",
        oauth_provider="gmail",
    )
    service = MailSendService(
        store,
        smtp_client_class=FakeSmtpClient,
        imap_client_class=FakeImapClient,
    )

    monkeypatch.setattr(
        mail_send,
        "get_mail_account_settings",
        lambda _store, account_id: _settings_for(account_id, account.email_address),
    )

    draft = ComposeDraft(
        account_id=account.id,
        recipients="Friend <friend@example.com>, other@example.com",
        subject="Hei",
        body_text="Dette er kroppen.",
    )

    result = service.send_draft(draft)

    assert result == SendResult(
        sent=True,
        local_copy_saved=True,
        server_copy_attempted=False,
        server_copy_saved=False,
    )

    assert FakeSmtpClient.instances[0].sent_messages == [
        (
            "privat@example.com",
            ["friend@example.com", "other@example.com"],
            "Hei",
            "Dette er kroppen.",
            None,
        )
    ]
    assert FakeSmtpClient.instances[0].message_id is not None
    assert FakeSmtpClient.instances[0].date_header is not None

    sent_folder = store.get_or_add_folder(account.id, "Sendt")
    sent_messages = store.list_messages(account.id, sent_folder.id)

    assert len(sent_messages) == 1
    assert sent_messages[0].subject == "Hei"
    assert sent_messages[0].sender == "privat@example.com"
    assert sent_messages[0].recipients == draft.recipients
    assert sent_messages[0].is_read is True
    assert FakeImapClient.instances == []


def test_mail_send_service_appends_sent_copy_for_non_gmail_accounts(
    tmp_path,
    monkeypatch,
) -> None:
    FakeSmtpClient.instances = []
    FakeImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSendService(
        store,
        smtp_client_class=FakeSmtpClient,
        imap_client_class=FakeImapClient,
    )

    monkeypatch.setattr(
        mail_send,
        "get_mail_account_settings",
        lambda _store, account_id: _settings_for(account_id, account.email_address),
    )

    result = service.send_draft(
        ComposeDraft(
            account_id=account.id,
            recipients="friend@example.com",
            subject="Hei",
            body_text="Dette er kroppen.",
        )
    )

    assert result == SendResult(
        sent=True,
        local_copy_saved=True,
        server_copy_attempted=True,
        server_copy_saved=True,
    )
    assert FakeImapClient.instances[0].appended_messages[0][0] == "Sent"
    assert b"Subject: Hei" in FakeImapClient.instances[0].appended_messages[0][1]


def test_mail_send_service_reports_sent_copy_append_failure(
    tmp_path,
    monkeypatch,
) -> None:
    FakeSmtpClient.instances = []
    FailingAppendImapClient.instances = []
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSendService(
        store,
        smtp_client_class=FakeSmtpClient,
        imap_client_class=FailingAppendImapClient,
    )

    monkeypatch.setattr(
        mail_send,
        "get_mail_account_settings",
        lambda _store, account_id: _settings_for(account_id, account.email_address),
    )

    result = service.send_draft(
        ComposeDraft(
            account_id=account.id,
            recipients="friend@example.com",
            subject="Hei",
            body_text="Dette er kroppen.",
        )
    )

    assert result == SendResult(
        sent=True,
        local_copy_saved=True,
        server_copy_attempted=True,
        server_copy_saved=False,
    )
    assert bool(result)


def test_mail_send_service_requires_recipient(tmp_path, monkeypatch) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSendService(
        store,
        smtp_client_class=FakeSmtpClient,
        imap_client_class=FakeImapClient,
    )

    monkeypatch.setattr(
        mail_send,
        "get_mail_account_settings",
        lambda _store, account_id: _settings_for(account_id, account.email_address),
    )

    with pytest.raises(ValueError):
        service.send_draft(ComposeDraft(account_id=account.id))


def test_mail_send_service_sends_cc_bcc_and_attachments(
    tmp_path,
    monkeypatch,
) -> None:
    FakeSmtpClient.instances = []
    FakeImapClient.instances = []
    attachment_path = tmp_path / "rapport.txt"
    attachment_path.write_text("rapport", encoding="utf-8")
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    service = MailSendService(
        store,
        smtp_client_class=FakeSmtpClient,
        imap_client_class=FakeImapClient,
    )

    monkeypatch.setattr(
        mail_send,
        "get_mail_account_settings",
        lambda _store, account_id: _settings_for(account_id, account.email_address),
    )

    draft = ComposeDraft(
        account_id=account.id,
        recipients="friend@example.com",
        cc="copy@example.com",
        bcc="hidden@example.com",
        subject="Hei",
        body_text="Plain",
        body_html="<p><strong>HTML</strong></p>",
        attachment_paths=(str(attachment_path),),
    )

    assert service.send_draft(draft)

    smtp_client = FakeSmtpClient.instances[0]
    assert smtp_client.cc == ["copy@example.com"]
    assert smtp_client.bcc == ["hidden@example.com"]
    assert smtp_client.body_html == "<p><strong>HTML</strong></p>"
    assert smtp_client.attachment_paths == [str(attachment_path)]

    sent_folder = store.get_or_add_folder(account.id, "Sendt")
    sent_message = store.list_messages(account.id, sent_folder.id)[0]
    attachments = store.list_attachments(sent_message.id)

    assert sent_message.recipients == (
        "friend@example.com; Cc: copy@example.com; Bcc: hidden@example.com"
    )
    assert sent_message.body_html == "<p><strong>HTML</strong></p>"
    assert attachments[0].filename == "rapport.txt"
    assert attachments[0].size == len("rapport")
    assert attachments[0].has_content is True
    assert store.get_attachment_content(attachments[0].id) == b"rapport"


def test_mail_send_service_reply_uses_original_message_account(tmp_path) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    private = store.add_account("Privat", "privat@example.com")
    work = store.add_account("Arbeid", "arbeid@example.com")
    private_inbox = store.add_folder(private.id, "INBOX")
    work_inbox = store.add_folder(work.id, "INBOX")
    store.add_message(private.id, private_inbox.id, subject="Annen konto")
    message = store.add_message(
        work.id,
        work_inbox.id,
        message_id="<original@example.com>",
        subject="Prosjekt",
        sender="Sender <sender@example.com>",
        body_text="Original tekst",
    )
    service = MailSendService(store)

    draft = service.create_reply_draft(message.id)

    assert draft == ComposeDraft(
        account_id=work.id,
        recipients="Sender <sender@example.com>",
        subject="Re: Prosjekt",
        body_text="\n\n> Original tekst",
        in_reply_to="<original@example.com>",
    )


def test_mail_send_service_forward_uses_original_message_account(tmp_path) -> None:
    store = MailStore(tmp_path / "mailklient.sqlite3")
    account = store.add_account("Privat", "privat@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(
        account.id,
        inbox.id,
        subject="Prosjekt",
        sender="sender@example.com",
        recipients="privat@example.com",
        received_at="2026-01-03T12:00:00",
        body_text="Original tekst",
    )
    service = MailSendService(store)

    draft = service.create_forward_draft(message.id)

    assert draft is not None
    assert draft.account_id == account.id
    assert draft.recipients == ""
    assert draft.subject == "Fwd: Prosjekt"
    assert "---------- Videresendt melding ----------" in draft.body_text
    assert "Original tekst" in draft.body_text
