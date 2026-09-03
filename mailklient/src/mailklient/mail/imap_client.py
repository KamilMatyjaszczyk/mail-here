"""Small IMAP client wrapper."""

from __future__ import annotations

import imaplib
import re
import ssl
from collections.abc import Callable
from email import message_from_bytes
from email.header import decode_header, make_header
from email.policy import default
from email.utils import parsedate_to_datetime

from mailklient.mail.config import ImapFolder, ImapMessageHeader, ImapSettings
from mailklient.security import build_xoauth2_payload

ImapConnectionFactory = Callable[..., imaplib.IMAP4_SSL]
PlainImapConnectionFactory = Callable[..., imaplib.IMAP4]


class ImapClient:
    """IMAP operations used by the application services."""

    def __init__(
        self,
        settings: ImapSettings,
        connection_factory: ImapConnectionFactory = imaplib.IMAP4_SSL,
        starttls_connection_factory: PlainImapConnectionFactory = imaplib.IMAP4,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory
        self._starttls_connection_factory = starttls_connection_factory

    def test_connection(self) -> bool:
        """Connect with SSL, login, logout and report success."""
        connection = self._login()
        connection.logout()
        return True

    def list_folders(self) -> list[ImapFolder]:
        """Return folders reported by the IMAP server."""
        connection = self._login()
        try:
            status, data = connection.list()
            if status != "OK":
                return []

            return [
                ImapFolder(name=name)
                for item in data
                if item is not None
                for name in [_parse_folder_name(item)]
                if name
            ]
        finally:
            connection.logout()

    def fetch_headers(
        self,
        folder_name: str,
        limit: int = 25,
    ) -> list[ImapMessageHeader]:
        """Fetch recent message headers from one folder."""
        connection = self._login()
        try:
            status, _data = connection.select(_quote_mailbox(folder_name), readonly=True)
            if status != "OK":
                return []

            status, data = connection.uid("SEARCH", None, "ALL")
            if status != "OK" or not data or data[0] is None:
                return []

            message_uids = data[0].split()
            selected_uids = list(reversed(message_uids[-limit:]))
            headers: list[ImapMessageHeader] = []

            for message_uid in selected_uids:
                status, fetch_data = connection.uid(
                    "FETCH",
                    message_uid,
                    "(FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM TO DATE)])",
                )
                if status != "OK":
                    continue

                header_bytes = _extract_header_bytes(fetch_data)
                if header_bytes is None:
                    continue

                headers.append(
                    _parse_message_header(
                        message_uid,
                        header_bytes,
                        _extract_flags(fetch_data),
                    )
                )

            return headers
        finally:
            connection.logout()

    def _login(self):
        context = ssl.create_default_context()

        if self._settings.security == "ssl":
            connection = self._connection_factory(
                self._settings.host,
                self._settings.port,
                ssl_context=context,
            )
        elif self._settings.security == "starttls":
            connection = self._starttls_connection_factory(
                self._settings.host,
                self._settings.port,
            )
            connection.starttls(ssl_context=context)
        else:
            raise ValueError(f"Unsupported IMAP security mode: {self._settings.security}")

        if self._settings.auth_method == "oauth2":
            connection.authenticate(
                "XOAUTH2",
                lambda _challenge: build_xoauth2_payload(
                    self._settings.username,
                    self._settings.password,
                ).encode("utf-8"),
            )
        else:
            connection.login(self._settings.username, self._settings.password)
        return connection


def _parse_folder_name(item: bytes | str) -> str | None:
    line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item
    line = line.strip()
    if not line:
        return None

    quoted_parts: list[str] = []
    current = []
    in_quotes = False
    escaped = False

    for character in line:
        if escaped:
            current.append(character)
            escaped = False
            continue

        if character == "\\" and in_quotes:
            escaped = True
            continue

        if character == '"':
            if in_quotes:
                quoted_parts.append("".join(current))
                current = []
            in_quotes = not in_quotes
            continue

        if in_quotes:
            current.append(character)

    if quoted_parts:
        return quoted_parts[-1]

    return line.split()[-1]


def _quote_mailbox(folder_name: str) -> str:
    escaped = folder_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _extract_header_bytes(fetch_data) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _extract_flags(fetch_data) -> tuple[str, ...]:
    for item in fetch_data:
        if not isinstance(item, tuple) or not item:
            continue

        metadata = item[0]
        if not isinstance(metadata, bytes):
            continue

        match = re.search(rb"FLAGS \(([^)]*)\)", metadata)
        if match is None:
            continue

        return tuple(
            flag.decode("ascii", errors="replace")
            for flag in match.group(1).split()
        )

    return ()


def _parse_message_header(
    message_uid: bytes,
    header_bytes: bytes,
    flags: tuple[str, ...],
) -> ImapMessageHeader:
    message = message_from_bytes(header_bytes, policy=default)
    raw_message_id = message.get("Message-ID")
    date = _normalize_date(message.get("Date"))

    return ImapMessageHeader(
        uid=message_uid.decode("ascii", errors="replace"),
        flags=flags,
        message_id=raw_message_id.strip() if raw_message_id else None,
        subject=_decode_header_value(message.get("Subject")),
        sender=_decode_header_value(message.get("From")),
        recipients=_decode_header_value(message.get("To")),
        date=date,
    )


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value
