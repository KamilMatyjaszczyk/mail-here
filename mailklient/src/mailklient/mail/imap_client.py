"""Small IMAP client wrapper."""

from __future__ import annotations

import base64
import html
import imaplib
import re
import ssl
from collections.abc import Callable
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from mailklient.mail.config import (
    ImapAttachment,
    ImapFolder,
    ImapMessageFlags,
    ImapMessageHeader,
    ImapSettings,
)
from mailklient.security import build_xoauth2_payload

ImapConnectionFactory = Callable[..., imaplib.IMAP4_SSL]
PlainImapConnectionFactory = Callable[..., imaplib.IMAP4]
MAX_AUTO_CACHED_ATTACHMENT_BYTES = 5 * 1024 * 1024


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
                folder
                for item in data
                if item is not None
                for folder in [_parse_folder(item)]
                if folder is not None
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
            status, data = connection.select(
                _quote_mailbox(folder_name),
                readonly=True,
            )
            if status != "OK":
                return []

            message_count = _parse_message_count(data)
            if message_count == 0:
                return []

            start = max(1, message_count - limit + 1)
            sequence_set = f"{start}:*"
            status, fetch_data = connection.fetch(
                sequence_set,
                "(UID FLAGS BODY.PEEK[])",
            )
            if status != "OK":
                return []

            headers: list[ImapMessageHeader] = []
            for fetch_item in fetch_data:
                header_bytes = _extract_header_bytes([fetch_item])
                if header_bytes is None:
                    continue

                uid = _extract_uid([fetch_item])
                if uid is None:
                    continue

                headers.append(
                    _parse_message_header(
                        uid,
                        header_bytes,
                        _extract_flags([fetch_item]),
                    )
                )

            return list(reversed(headers))
        finally:
            connection.logout()

    def fetch_headers_since_uid(
        self,
        folder_name: str,
        last_seen_uid: int,
        limit: int = 100,
    ) -> list[ImapMessageHeader]:
        """Fetch message headers newer than the last synced IMAP UID."""
        connection = self._login()
        try:
            status, _data = connection.select(
                _quote_mailbox(folder_name),
                readonly=True,
            )
            if status != "OK":
                return []

            status, search_data = connection.uid(
                "SEARCH",
                None,
                f"UID {last_seen_uid + 1}:4294967295",
            )
            if status != "OK":
                return []

            uids = _parse_uid_search_result(search_data)
            if not uids:
                return []

            selected_uids = uids[:limit] if limit > 0 else []
            if not selected_uids:
                return []

            uid_set = ",".join(str(uid) for uid in selected_uids)
            status, fetch_data = connection.uid(
                "FETCH",
                uid_set,
                "(UID FLAGS BODY.PEEK[])",
            )
            if status != "OK":
                return []

            headers: list[ImapMessageHeader] = []
            for fetch_item in fetch_data:
                header_bytes = _extract_header_bytes([fetch_item])
                if header_bytes is None:
                    continue

                uid = _extract_uid([fetch_item])
                if uid is None:
                    continue

                headers.append(
                    _parse_message_header(
                        uid,
                        header_bytes,
                        _extract_flags([fetch_item]),
                    )
                )

            return list(reversed(headers))
        finally:
            connection.logout()

    def list_uids(self, folder_name: str) -> list[str] | None:
        """Return all message UIDs for one folder, or None on IMAP failure."""
        connection = self._login()
        try:
            status, _data = connection.select(
                _quote_mailbox(folder_name),
                readonly=True,
            )
            if status != "OK":
                return None

            status, search_data = connection.uid("SEARCH", None, "ALL")
            if status != "OK":
                return None

            return [str(uid) for uid in _parse_uid_search_result(search_data)]
        finally:
            connection.logout()

    def fetch_recent_flags(
        self,
        folder_name: str,
        limit: int = 100,
    ) -> list[ImapMessageFlags]:
        """Fetch recent message flags without downloading message bodies."""
        connection = self._login()
        try:
            status, data = connection.select(
                _quote_mailbox(folder_name),
                readonly=True,
            )
            if status != "OK":
                return []

            message_count = _parse_message_count(data)
            if message_count == 0:
                return []

            start = max(1, message_count - limit + 1)
            status, fetch_data = connection.fetch(f"{start}:*", "(UID FLAGS)")
            if status != "OK":
                return []

            flags: list[ImapMessageFlags] = []
            for fetch_item in fetch_data:
                uid = _extract_uid([fetch_item])
                if uid is None:
                    continue
                flags.append(
                    ImapMessageFlags(
                        uid=uid,
                        flags=_extract_flags([fetch_item]),
                    )
                )
            return flags
        finally:
            connection.logout()

    def set_seen(self, folder_name: str, message_uid: str, is_read: bool) -> bool:
        """Mark one message as seen or unseen on the IMAP server."""
        connection = self._login()
        try:
            status, _data = connection.select(_quote_mailbox(folder_name))
            if status != "OK":
                return False

            command = "+FLAGS.SILENT" if is_read else "-FLAGS.SILENT"
            status, _data = connection.uid(
                "STORE",
                message_uid,
                command,
                r"(\Seen)",
            )
            return status == "OK"
        finally:
            connection.logout()

    def append_message(
        self,
        folder_name: str,
        raw_message: bytes,
        *,
        flags: tuple[str, ...] = ("\\Seen",),
        internal_date: str | None = None,
    ) -> bool:
        """Append one raw RFC822 message to a server folder."""
        connection = self._login()
        try:
            status, _data = connection.append(
                _quote_mailbox(folder_name),
                f"({' '.join(flags)})" if flags else None,
                internal_date,
                raw_message,
            )
            return status == "OK"
        finally:
            connection.logout()

    def move_message(
        self,
        folder_name: str,
        message_uid: str,
        destination_folder_name: str,
    ) -> bool:
        """Move one message to another IMAP folder."""
        connection = self._login()
        try:
            status, _data = connection.select(_quote_mailbox(folder_name))
            if status != "OK":
                return False

            status, _data = connection.uid(
                "MOVE",
                message_uid,
                _quote_mailbox(destination_folder_name),
            )
            if status == "OK":
                return True

            status, _data = connection.uid(
                "COPY",
                message_uid,
                _quote_mailbox(destination_folder_name),
            )
            if status != "OK":
                return False

            status, _data = connection.uid(
                "STORE",
                message_uid,
                "+FLAGS.SILENT",
                r"(\Deleted)",
            )
            if status != "OK":
                return False

            status, _data = connection.expunge()
            return status == "OK"
        finally:
            connection.logout()

    def archive_message(self, folder_name: str, message_uid: str) -> bool:
        """Archive one message by removing it from the selected folder."""
        connection = self._login()
        try:
            status, _data = connection.select(_quote_mailbox(folder_name))
            if status != "OK":
                return False

            status, _data = connection.uid(
                "STORE",
                message_uid,
                "+FLAGS.SILENT",
                r"(\Deleted)",
            )
            if status != "OK":
                return False

            status, _data = connection.expunge()
            return status == "OK"
        finally:
            connection.logout()

    def archive_gmail_message(self, folder_name: str, message_uid: str) -> bool:
        """Archive a Gmail message by removing the Inbox label."""
        connection = self._login()
        try:
            status, _data = connection.select(_quote_mailbox(folder_name))
            if status != "OK":
                return False

            status, _data = connection.uid(
                "STORE",
                message_uid,
                "-X-GM-LABELS.SILENT",
                r"(\Inbox)",
            )
            if status == "OK":
                return True

            status, _data = connection.uid(
                "STORE",
                message_uid,
                "-FLAGS.SILENT",
                r"(\Inbox)",
            )
            return status == "OK"
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


def _parse_folder(item: bytes | str) -> ImapFolder | None:
    line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item
    line = line.strip()
    if not line:
        return None

    flags = _parse_list_flags(line)
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
        delimiter = quoted_parts[-2] if len(quoted_parts) >= 2 else None
        return ImapFolder(name=quoted_parts[-1], flags=flags, delimiter=delimiter)

    return ImapFolder(name=line.split()[-1], flags=flags)


def _parse_list_flags(line: str) -> tuple[str, ...]:
    match = re.match(r"\((?P<flags>[^)]*)\)", line)
    if match is None:
        return ()
    return tuple(flag for flag in match.group("flags").split() if flag)


def _quote_mailbox(folder_name: str) -> str:
    escaped = folder_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_message_count(data) -> int:
    if not data or data[0] is None:
        return 0

    raw_count = data[0]
    if isinstance(raw_count, bytes):
        raw_count = raw_count.decode("ascii", errors="replace")

    try:
        return int(raw_count)
    except (TypeError, ValueError):
        return 0


def _parse_uid_search_result(data) -> list[int]:
    if not data:
        return []

    uids: list[int] = []
    for item in data:
        if item is None:
            continue
        raw_item = (
            item.decode("ascii", errors="replace")
            if isinstance(item, bytes)
            else item
        )
        for raw_uid in str(raw_item).split():
            try:
                uids.append(int(raw_uid))
            except ValueError:
                continue

    return sorted(set(uids))


def _extract_header_bytes(fetch_data) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _extract_uid(fetch_data) -> str | None:
    for item in fetch_data:
        if not isinstance(item, tuple) or not item:
            continue

        metadata = item[0]
        if not isinstance(metadata, bytes):
            continue

        match = re.search(rb"UID ([0-9]+)", metadata)
        if match is None:
            continue

        return match.group(1).decode("ascii", errors="replace")

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
    message_uid: str,
    message_bytes: bytes,
    flags: tuple[str, ...],
) -> ImapMessageHeader:
    message = message_from_bytes(message_bytes, policy=default)
    raw_message_id = message.get("Message-ID")
    date = _normalize_date(message.get("Date"))
    body_text, body_html, attachments = _extract_message_parts(message)

    return ImapMessageHeader(
        uid=message_uid,
        flags=flags,
        message_id=raw_message_id.strip() if raw_message_id else None,
        subject=_decode_header_value(message.get("Subject")),
        sender=_decode_header_value(message.get("From")),
        recipients=_decode_header_value(message.get("To")),
        date=date,
        body_text=body_text,
        body_html=body_html,
        body_preview=_build_body_preview(body_text or body_html),
        attachments=attachments,
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


def _extract_message_parts(message: Message) -> tuple[str, str, tuple[ImapAttachment, ...]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    inline_resources: dict[str, str] = {}
    attachments: list[ImapAttachment] = []

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            _append_inline_resource(part, inline_resources)
            _append_attachment(part, attachments)
            _append_body_part(part, text_parts, html_parts)
    else:
        _append_body_part(message, text_parts, html_parts)

    body_text = "\n\n".join(text_parts).strip()
    body_html = "\n\n".join(html_parts).strip()
    if body_html and inline_resources:
        body_html = _inline_cid_resources(body_html, inline_resources)
    if not body_text and body_html:
        body_text = _html_to_text(body_html)

    return body_text, body_html, tuple(attachments)


def _append_attachment(part: Message, attachments: list[ImapAttachment]) -> None:
    disposition = (part.get_content_disposition() or "").casefold()
    filename = part.get_filename()
    content_id = part.get("Content-ID")

    if disposition != "attachment" and not filename:
        return

    clean_content_id = content_id.strip().strip("<>") if content_id else None
    if disposition == "inline" and clean_content_id:
        return

    payload = part.get_payload(decode=True)
    size = len(payload) if isinstance(payload, bytes) else 0
    cached_content = (
        payload
        if isinstance(payload, bytes) and size <= MAX_AUTO_CACHED_ATTACHMENT_BYTES
        else None
    )
    attachments.append(
        ImapAttachment(
            filename=_decode_header_value(filename) if filename else "(uten navn)",
            content_type=part.get_content_type(),
            size=size,
            content_id=clean_content_id,
            is_inline=disposition == "inline",
            content=cached_content,
        )
    )


def _append_inline_resource(part: Message, inline_resources: dict[str, str]) -> None:
    content_id = part.get("Content-ID")
    if not content_id:
        return

    content_type = part.get_content_type()
    if not content_type.startswith("image/"):
        return

    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return

    cid = content_id.strip().strip("<>")
    if not cid:
        return

    encoded_payload = base64.b64encode(payload).decode("ascii")
    inline_resources[cid] = f"data:{content_type};base64,{encoded_payload}"


def _inline_cid_resources(body_html: str, inline_resources: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        cid = html.unescape(match.group("cid")).strip("<>")
        return inline_resources.get(cid, match.group(0))

    return re.sub(
        r"cid:(?P<cid>[^'\"\s>)]+)",
        replace,
        body_html,
        flags=re.IGNORECASE,
    )


def _append_body_part(
    part: Message,
    text_parts: list[str],
    html_parts: list[str],
) -> None:
    if part.get_content_disposition() == "attachment":
        return

    content_type = part.get_content_type()
    if content_type not in {"text/plain", "text/html"}:
        return

    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, AttributeError):
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return
        charset = part.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")

    if not isinstance(content, str):
        return

    if content_type == "text/plain":
        text_parts.append(content.strip())
    else:
        html_parts.append(content.strip())


def _build_body_preview(body: str, max_length: int = 240) -> str:
    normalized_body = " ".join(body.split())
    if len(normalized_body) <= max_length:
        return normalized_body
    return normalized_body[: max_length - 3].rstrip() + "..."


def _html_to_text(body_html: str) -> str:
    parser = _TextFromHtmlParser()
    parser.feed(body_html)
    normalized_text = " ".join(" ".join(parser.text_parts).split())
    return html.unescape(normalized_text).strip()


class _TextFromHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self._ignored_tag_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.casefold() in {"head", "script", "style"}:
            self._ignored_tag_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"head", "script", "style"}:
            self._ignored_tag_depth = max(0, self._ignored_tag_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_tag_depth:
            return

        stripped_data = data.strip()
        if stripped_data:
            self.text_parts.append(stripped_data)
