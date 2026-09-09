"""Small SMTP client wrapper."""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from contextlib import suppress
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from mimetypes import guess_type
from pathlib import Path

from mailklient.mail.config import SmtpSettings
from mailklient.security import build_xoauth2_payload
from mailklient.security.tls import create_mail_ssl_context

SmtpConnectionFactory = Callable[..., smtplib.SMTP]
SmtpSslConnectionFactory = Callable[..., smtplib.SMTP_SSL]


class SmtpClient:
    """SMTP operations used by the application services."""

    def __init__(
        self,
        settings: SmtpSettings,
        connection_factory: SmtpConnectionFactory = smtplib.SMTP,
        ssl_connection_factory: SmtpSslConnectionFactory = smtplib.SMTP_SSL,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory
        self._ssl_connection_factory = ssl_connection_factory

    def test_connection(self) -> bool:
        """Connect, login, quit and report success."""
        connection = self._connect_and_authenticate()
        try:
            connection.quit()
        finally:
            connection.close()
        return True

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
        """Send one plain text email."""
        message, envelope_recipients = build_email_message(
            sender=sender,
            recipients=recipients,
            subject=subject,
            body_text=body_text,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            attachment_paths=attachment_paths,
            in_reply_to=in_reply_to,
            message_id=message_id,
            date_header=date_header,
        )

        connection = self._connect_and_authenticate()
        try:
            refused = connection.send_message(message, to_addrs=envelope_recipients)
            if refused:
                raise smtplib.SMTPException(
                    "The email was sent to some, but not all recipients. "
                    "Check Sent and the recipients before sending again."
                )
            return True
        finally:
            # A failed QUIT must not turn an accepted message into a send failure.
            with suppress(Exception):
                connection.quit()
            with suppress(Exception):
                connection.close()

    def _connect_and_authenticate(self):
        context = create_mail_ssl_context(
            self._settings.host, self._settings.local_certificate
        )

        if self._settings.security == "ssl":
            connection = self._ssl_connection_factory(
                self._settings.host,
                self._settings.port,
                context=context,
                timeout=self._settings.timeout,
            )
        elif self._settings.security == "starttls":
            connection = self._connection_factory(
                self._settings.host,
                self._settings.port,
                timeout=self._settings.timeout,
            )
        else:
            raise ValueError(f"Unsupported SMTP security mode: {self._settings.security}")

        try:
            if self._settings.security == "starttls":
                connection.starttls(context=context)
            if self._settings.auth_method == "oauth2":
                # EHLO after TLS must precede AUTH; a later EHLO can reset authentication.
                connection.ehlo()
                connection.auth(
                    "XOAUTH2",
                    lambda _challenge=None: build_xoauth2_payload(
                        self._settings.username,
                        self._settings.password,
                    ),
                    initial_response_ok=True,
                )
            else:
                connection.login(self._settings.username, self._settings.password)
        except Exception:
            with suppress(Exception):
                connection.close()
            raise
        return connection


def _ensure_safe_header(name: str, value: str) -> None:
    if "\r" in value or "\n" in value:
        raise ValueError(f"Invalid header value for {name}.")


def build_email_message(
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
) -> tuple[EmailMessage, list[str]]:
    """Build a MIME message and SMTP envelope recipients."""
    cc = cc or []
    bcc = bcc or []
    attachment_paths = attachment_paths or []
    envelope_recipients = [*recipients, *cc, *bcc]
    if not envelope_recipients:
        raise ValueError("At least one recipient is required.")

    _ensure_safe_header("sender", sender)
    _ensure_safe_header("subject", subject)
    for recipient in envelope_recipients:
        _ensure_safe_header("recipient", recipient)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = date_header or formatdate(localtime=True)
    message["Message-ID"] = message_id or make_msgid()
    message["User-Agent"] = "mcpMail/0.1"
    if in_reply_to:
        _ensure_safe_header("in_reply_to", in_reply_to)
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")
    for path in attachment_paths:
        _add_file_attachment(message, Path(path))

    return message, envelope_recipients


def _add_file_attachment(message: EmailMessage, path: Path) -> None:
    content_type, _encoding = guess_type(path.name)
    if content_type is None:
        content_type = "application/octet-stream"
    maintype, subtype = content_type.split("/", maxsplit=1)

    with path.open("rb") as file:
        message.add_attachment(
            file.read(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
