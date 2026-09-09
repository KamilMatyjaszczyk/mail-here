"""Small IMAP client wrapper."""

from __future__ import annotations

import base64
import html
import imaplib
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from imapclient.exceptions import ProtocolError

from mailklient.mail.config import (
    ImapAttachment,
    ImapFolder,
    ImapMessageFlags,
    ImapMessageHeader,
    ImapSettings,
)
from mailklient.mail.imap_parts import (
    MessageMissingError,
    fetch_fields,
    fetch_part,
    mime_parts,
)
from mailklient.security import build_xoauth2_payload
from mailklient.security.tls import create_mail_ssl_context

ImapConnectionFactory = Callable[..., imaplib.IMAP4_SSL]
PlainImapConnectionFactory = Callable[..., imaplib.IMAP4]
MAX_AUTO_CACHED_ATTACHMENT_BYTES = 5 * 1024 * 1024


class ImapAuthenticationError(imaplib.IMAP4.error):
    """The server rejected authentication, without exposing its raw reply."""


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
        self._expected_validity: dict[str, int] = {}
        self._shared_connection: imaplib.IMAP4 | None = None
        self._progress: Callable[[str], None] | None = None

    def set_progress_callback(self, callback: Callable[[str], None]) -> None:
        self._progress = callback

    def _report_progress(self, message: str) -> None:
        if self._progress is not None:
            self._progress(message)

    @contextmanager
    def session(self) -> Iterator[None]:
        """Keep one authenticated connection for a complete sync."""
        if self._shared_connection is not None:
            raise RuntimeError("An IMAP session is already active.")
        connection = self._login()
        self._shared_connection = connection
        try:
            yield
        except BaseException:
            # A cancelled or timed-out command must not wait for LOGOUT too.
            with suppress(Exception):
                connection.shutdown()
            raise
        else:
            try:
                connection.logout()
            except Exception:
                with suppress(Exception):
                    connection.shutdown()
        finally:
            self._shared_connection = None

    def _release_connection(self, connection) -> None:
        if connection is not self._shared_connection:
            connection.logout()

    def expect_uidvalidity(self, folder_name: str, validity: int) -> None:
        self._expected_validity[folder_name] = validity

    def _select(self, connection, folder_name: str, readonly: bool = False):
        self._report_progress(f"Opening {folder_name}...")
        result = connection.select(_quote_mailbox(folder_name), readonly=readonly)
        expected = self._expected_validity.get(folder_name)
        if result[0] == "OK" and expected is not None:
            if _selected_uidvalidity(connection) != expected:
                raise ValueError(
                    "The folder has changed on the server. Sync before trying again."
                )
        return result

    def folder_uidvalidity(self, folder_name: str) -> int:
        connection = self._login()
        try:
            if self._select(connection, folder_name, readonly=True)[0] != "OK":
                raise ValueError("Could not open the IMAP folder.")
            return _selected_uidvalidity(connection)
        finally:
            self._release_connection(connection)

    def test_connection(self) -> bool:
        """Connect with SSL, login, logout and report success."""
        connection = self._login()
        try:
            self._release_connection(connection)
        except Exception:
            with suppress(Exception):
                connection.shutdown()
            raise
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
            self._release_connection(connection)

    def fetch_headers(
        self,
        folder_name: str,
        limit: int = 25,
    ) -> list[ImapMessageHeader]:
        """Fetch recent message headers from one folder."""
        connection = self._login()
        try:
            status, data = self._select(
                connection,
                folder_name,
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
            self._release_connection(connection)

    def fetch_headers_since_uid(
        self,
        folder_name: str,
        last_seen_uid: int,
        limit: int = 100,
    ) -> list[ImapMessageHeader]:
        """Fetch message headers newer than the last synced IMAP UID."""
        connection = self._login()
        try:
            status, _data = self._select(
                connection,
                folder_name,
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
            self._release_connection(connection)

    def fetch_attachment(
        self, folder_name: str, message_uid: str, index: int
    ) -> ImapAttachment:
        """Fetch an uncached attachment, without marking its message as read."""
        _validate_uid(message_uid)
        if index < 0:
            raise ValueError("Invalid attachment.")
        connection = self._login()
        try:
            if self._select(connection, folder_name, readonly=True)[0] != "OK":
                raise ValueError("Could not open the attachment folder.")
            fields = fetch_fields(connection, message_uid, "(UID BODYSTRUCTURE)")
            parts = [
                part
                for part in mime_parts(fields[b"BODYSTRUCTURE"])
                if part.is_attachment
            ]
            if index >= len(parts):
                raise ValueError("The attachment does not exist in this message.")
            part = parts[index]
            message, content = fetch_part(connection, message_uid, part)
            return ImapAttachment(
                filename=_decode_header_value(message.get_filename()) or "(unnamed)",
                content_type=message.get_content_type(),
                size=len(content),
                content_id=str(message.get("Content-ID", "")).strip("<>") or None,
                is_inline=message.get_content_disposition() == "inline",
                content=content,
                imap_section=part.section,
            )
        finally:
            with suppress(Exception):
                self._release_connection(connection)

    def fetch_messages(
        self,
        folder_name: str,
        *,
        limit: int = 25,
        after_uid: int = 0,
        before_uid: int | None = None,
        message_uids: tuple[str, ...] | None = None,
    ) -> list[ImapMessageHeader]:
        """Fetch headers and readable parts, deferring attachment bytes until requested."""
        if limit <= 0 or (before_uid is not None and before_uid <= 1):
            return []
        if message_uids is not None:
            if after_uid or before_uid is not None:
                raise ValueError("An explicit UID selection cannot be combined with a UID range.")
            for requested_uid in message_uids:
                _validate_uid(requested_uid)
            if not message_uids:
                return []
        connection = self._login()
        try:
            if self._select(connection, folder_name, readonly=True)[0] != "OK":
                raise ValueError("Could not open the IMAP folder.")
            if message_uids is not None:
                uids = sorted({int(uid) for uid in message_uids})[:limit]
            else:
                criteria = "ALL"
                if before_uid is not None:
                    criteria = f"UID 1:{before_uid - 1}"
                elif after_uid:
                    criteria = f"UID {after_uid + 1}:4294967295"
                status, data = connection.uid("SEARCH", None, criteria)
                if status != "OK":
                    raise ValueError("Could not find messages.")
                uids = sorted(
                    uid
                    for uid in _parse_uid_search_result(data)
                    if uid > after_uid and (before_uid is None or uid < before_uid)
                )
                uids = uids[:limit] if after_uid else uids[-limit:]
            messages = []
            for index, uid in enumerate(reversed(uids), 1):
                self._report_progress(
                    f"{folder_name}: fetching message {index} of {len(uids)}..."
                )
                try:
                    fields = fetch_fields(
                        connection, str(uid), "(UID FLAGS BODYSTRUCTURE BODY.PEEK[HEADER])"
                    )
                except MessageMissingError:
                    # A message can disappear between SEARCH and FETCH, or before a retry.
                    continue
                flags = tuple(
                    flag.decode("ascii", errors="replace")
                    for flag in fields.get(b"FLAGS", ())
                )
                header = _parse_message_header(
                    str(uid), fields.get(b"BODY[HEADER]", b""), flags
                )
                try:
                    parts = mime_parts(fields[b"BODYSTRUCTURE"])
                    attachments = []
                    texts: list[str] = []
                    htmls: list[str] = []
                    inline: dict[str, str] = {}
                    remaining = 10 * 1024 * 1024
                    for part in parts:
                        if part.is_attachment:
                            attachments.append(
                                ImapAttachment(
                                    filename=_decode_header_value(
                                        part.headers.get_filename()
                                    )
                                    or "(unnamed)",
                                    content_type=part.headers.get_content_type(),
                                    size=part.encoded_size,
                                    imap_section=part.section,
                                    content_id=str(
                                        part.headers.get("Content-ID", "")
                                    ).strip("<>")
                                    or None,
                                    is_inline=part.headers.get_content_disposition()
                                    == "inline",
                                )
                            )
                            continue
                        is_text = part.headers.get_content_type() in {
                            "text/plain",
                            "text/html",
                        }
                        is_image = (
                            part.headers.get_content_maintype() == "image"
                            and part.headers.get("Content-ID")
                        )
                        if not is_text and not is_image:
                            continue
                        if part.encoded_size > remaining:
                            if is_text:
                                raise ValueError("The message body is too large.")
                            continue
                        self._report_progress(
                            f"{folder_name}: message {index}/{len(uids)}, "
                            f"fetching part {part.section}..."
                        )
                        message, _content = fetch_part(connection, str(uid), part)
                        remaining -= part.encoded_size
                        _append_body_part(message, texts, htmls)
                        _append_inline_resource(message, inline)
                    body_html = _inline_cid_resources(
                        "\n\n".join(htmls).strip(), inline
                    )
                    body_text = "\n\n".join(texts).strip() or _html_to_text(body_html)
                    header = replace(
                        header,
                        body_html=body_html,
                        body_text=body_text,
                        body_preview=_build_body_preview(body_text),
                        attachments=tuple(attachments),
                    )
                except (
                    LookupError,
                    UnicodeError,
                    ValueError,
                    TypeError,
                    IndexError,
                    ProtocolError,
                ):
                    header = replace(
                        header,
                        parse_error=True,
                        body_text="The message body could not be fetched. Try syncing again.",
                    )
                messages.append(header)
            return messages
        finally:
            with suppress(Exception):
                self._release_connection(connection)

    def list_uids(self, folder_name: str) -> list[str] | None:
        """Return all message UIDs for one folder, or None on IMAP failure."""
        connection = self._login()
        try:
            status, _data = self._select(
                connection,
                folder_name,
                readonly=True,
            )
            if status != "OK":
                return None

            status, search_data = connection.uid("SEARCH", None, "ALL")
            if status != "OK":
                return None

            return [str(uid) for uid in _parse_uid_search_result(search_data)]
        finally:
            self._release_connection(connection)

    def fetch_recent_flags(
        self,
        folder_name: str,
        limit: int = 100,
    ) -> list[ImapMessageFlags]:
        """Fetch recent message flags without downloading message bodies."""
        connection = self._login()
        try:
            status, data = self._select(
                connection,
                folder_name,
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
            self._release_connection(connection)

    def set_seen(self, folder_name: str, message_uid: str, is_read: bool) -> bool:
        """Mark one message as seen or unseen on the IMAP server."""
        connection = self._login()
        try:
            status, _data = self._select(connection, folder_name)
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
            self._release_connection(connection)

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
            self._release_connection(connection)

    def move_message(
        self,
        folder_name: str,
        message_uid: str,
        destination_folder_name: str,
    ) -> bool:
        """Move one message to another IMAP folder."""
        connection = self._login()
        try:
            status, _data = self._select(connection, folder_name)
            if status != "OK":
                return False

            _validate_uid(message_uid)
            capabilities = {
                c.decode() if isinstance(c, bytes) else c
                for c in connection.capabilities
            }
            if "MOVE" not in capabilities:
                raise ValueError(
                    "The server does not support safe moves (UID MOVE). No message was moved."
                )
            status, _data = connection.uid(
                "MOVE", message_uid, _quote_mailbox(destination_folder_name)
            )
            return status == "OK"
        finally:
            with suppress(Exception):
                self._release_connection(connection)

    def archive_message(self, folder_name: str, message_uid: str) -> bool:
        """Legacy entry point; archive by moving, never by expunging."""
        return self.move_message(folder_name, message_uid, "Archive")

    def archive_gmail_message(self, folder_name: str, message_uid: str) -> bool:
        """Archive a Gmail message by removing the Inbox label."""
        connection = self._login()
        try:
            status, _data = self._select(connection, folder_name)
            if status != "OK":
                return False

            status, _data = connection.uid(
                "STORE",
                message_uid,
                "-X-GM-LABELS.SILENT",
                r"(\Inbox)",
            )
            return status == "OK"
        finally:
            self._release_connection(connection)

    def fetch_headers_before_uid(
        self, folder_name: str, before_uid: int, limit: int = 25
    ):
        if before_uid <= 1 or limit <= 0:
            return []
        connection = self._login()
        try:
            if self._select(connection, folder_name, readonly=True)[0] != "OK":
                raise ValueError("Could not open the IMAP folder.")
            status, data = connection.uid("SEARCH", None, f"UID 1:{before_uid - 1}")
            if status != "OK":
                raise ValueError("Could not find older messages.")
            uids = sorted(u for u in _parse_uid_search_result(data) if u < before_uid)[
                -limit:
            ]
            if not uids:
                return []
            status, data = connection.uid(
                "FETCH", ",".join(map(str, uids)), "(UID FLAGS BODY.PEEK[])"
            )
            if status != "OK":
                raise ValueError("Could not fetch older messages.")
            headers = []
            for item in data:
                raw, uid = _extract_header_bytes([item]), _extract_uid([item])
                if raw is not None and uid is not None:
                    headers.append(
                        _parse_message_header(uid, raw, _extract_flags([item]))
                    )
            return list(reversed(headers))
        finally:
            self._release_connection(connection)

    def _login(self):
        if self._shared_connection is not None:
            return self._shared_connection
        self._report_progress("Connecting to the IMAP server...")
        context = create_mail_ssl_context(
            self._settings.host, self._settings.local_certificate
        )

        if self._settings.security == "ssl":
            connection = self._connection_factory(
                self._settings.host,
                self._settings.port,
                ssl_context=context,
                timeout=self._settings.timeout,
            )
        elif self._settings.security == "starttls":
            connection = self._starttls_connection_factory(
                self._settings.host,
                self._settings.port,
                timeout=self._settings.timeout,
            )
        else:
            raise ValueError(
                f"Unsupported IMAP security mode: {self._settings.security}"
            )

        auth_started = False
        try:
            if self._settings.security == "starttls":
                connection.starttls(ssl_context=context)
            self._report_progress("Signing in to the IMAP server...")
            auth_started = True
            if self._settings.auth_method == "oauth2":
                payload = build_xoauth2_payload(
                    self._settings.username, self._settings.password
                ).encode("utf-8")
                responses = 0

                def respond(challenge: bytes) -> bytes | None:
                    nonlocal responses
                    self._report_progress("Signing in to the IMAP server...")
                    responses += 1
                    if responses == 1 and not challenge:
                        return payload
                    # XOAUTH2 error challenges require an empty acknowledgement.
                    # Abort if the server keeps asking instead of completing auth.
                    return b"" if responses <= 2 else None

                connection.authenticate(
                    "XOAUTH2",
                    respond,
                )
            elif self._settings.password_mechanism == "plain":
                if "AUTH=PLAIN" not in connection.capabilities:
                    raise ValueError("IMAP server does not advertise AUTH=PLAIN.")
                if (
                    "\x00" in self._settings.username
                    or "\x00" in self._settings.password
                ):
                    raise ValueError("NUL is not allowed in SASL PLAIN credentials.")
                payload = (
                    f"\x00{self._settings.username}\x00{self._settings.password}"
                ).encode()
                connection.authenticate("PLAIN", lambda _challenge: payload)
            else:
                connection.login(self._settings.username, self._settings.password)
        except imaplib.IMAP4.error as error:
            with suppress(Exception):
                connection.shutdown()
            if not auth_started or isinstance(error, imaplib.IMAP4.abort):
                raise
            raise ImapAuthenticationError(
                "IMAP sign-in was rejected. Sign in to the account again."
            ) from None
        except Exception:
            with suppress(Exception):
                connection.shutdown()
            raise
        return connection


def _validate_uid(uid: str) -> None:
    if not uid.isascii() or not uid.isdecimal() or int(uid) <= 0:
        raise ValueError("Invalid IMAP UID.")


def _selected_uidvalidity(connection) -> int:
    _kind, data = connection.response("UIDVALIDITY")
    if not data or data[0] is None or not data[0].isdigit() or int(data[0]) <= 0:
        raise ValueError("The server did not provide a valid UIDVALIDITY. Sync cancelled.")
    return int(data[0])


def _parse_folder(item: bytes | str | tuple[bytes, bytes]) -> ImapFolder | None:
    """Parse LIST fields in order; the mailbox need not be quoted."""
    literal = None
    if isinstance(item, tuple):
        item, literal = item
    line = item.decode("utf-8", errors="strict") if isinstance(item, bytes) else item
    quoted = r'"(?:[^"\\\r\n\x00]|\\["\\])*"'
    match = re.fullmatch(
        rf"\((?P<flags>[^()\r\n]*)\) +(?P<delimiter>NIL|{quoted}) +"
        rf'(?P<name>{quoted}|\{{[0-9]+\}}|[^\s(){{}}"\\\x00-\x1f\x7f]+)'
        r"(?: +\(.*\))?",
        line.strip(),
    )
    if match is None:
        return None
    name = match.group("name")
    if name.startswith("{"):
        if literal is None or len(literal) != int(name[1:-1]):
            return None
        name = literal.decode("utf-8", errors="strict")
    elif literal is not None:
        return None
    else:
        name = _unquote_imap_string(name)
    delimiter = match.group("delimiter")
    return ImapFolder(
        name=name,
        flags=tuple(match.group("flags").split()),
        delimiter=None if delimiter == "NIL" else _unquote_imap_string(delimiter),
    )


def _unquote_imap_string(value: str) -> str:
    if not value.startswith('"'):
        return value
    return re.sub(r'\\(["\\])', r"\1", value[1:-1])


def _quote_mailbox(folder_name: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in folder_name):
        raise ValueError("Control characters are not supported in IMAP mailbox names.")
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
            item.decode("ascii", errors="replace") if isinstance(item, bytes) else item
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
            flag.decode("ascii", errors="replace") for flag in match.group(1).split()
        )

    return ()


def _parse_message_header(
    message_uid: str,
    message_bytes: bytes,
    flags: tuple[str, ...],
) -> ImapMessageHeader:
    try:
        return _parse_message_header_content(message_uid, message_bytes, flags)
    except (LookupError, UnicodeError, ValueError, TypeError, IndexError):
        # Keep the UID visible and let later messages sync even if MIME is broken.
        return ImapMessageHeader(
            uid=message_uid,
            flags=flags,
            subject="Message could not be parsed",
            body_text="This message has invalid content. Open it in webmail.",
            parse_error=True,
        )


def _parse_message_header_content(
    message_uid: str, message_bytes: bytes, flags: tuple[str, ...]
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
        recipients=", ".join(
            _decode_header_value(value)
            for name in ("To", "Cc")
            for value in message.get_all(name, [])
        ),
        reply_to=_decode_header_value(message.get("Reply-To")),
        in_reply_to=str(message.get("In-Reply-To", "")),
        references=str(message.get("References", "")),
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


def _extract_message_parts(
    message: Message,
) -> tuple[str, str, tuple[ImapAttachment, ...]]:
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


def _append_attachment(
    part: Message, attachments: list[ImapAttachment], *, cache_all: bool = False
) -> None:
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
        if isinstance(payload, bytes)
        and (cache_all or size <= MAX_AUTO_CACHED_ATTACHMENT_BYTES)
        else None
    )
    attachments.append(
        ImapAttachment(
            filename=_decode_header_value(filename) if filename else "(unnamed)",
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
        try:
            content = payload.decode(charset, errors="replace")
        except LookupError:
            content = payload.decode("utf-8", errors="replace")

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
