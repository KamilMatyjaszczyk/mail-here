"""MIME section selection using IMAPClient's structured response parser."""

from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default

from imapclient.response_parser import parse_fetch_response
from imapclient.response_types import BodyData


class MessageMissingError(ValueError):
    """The selected UID no longer exists in the mailbox."""


@dataclass(frozen=True)
class MimePart:
    section: str
    headers: EmailMessage
    encoded_size: int

    @property
    def is_attachment(self) -> bool:
        disposition = self.headers.get_content_disposition()
        if disposition == "inline" and self.headers.get("Content-ID"):
            return False
        return disposition == "attachment" or bool(self.headers.get_filename())


def mime_parts(body: BodyData, prefix: str = "") -> list[MimePart]:
    """Map nested BODYSTRUCTURE nodes to RFC MIME section numbers."""
    if body.is_multipart:
        return [
            part
            for index, child in enumerate(body[0], 1)
            for part in mime_parts(child, f"{prefix}.{index}" if prefix else str(index))
        ]
    headers = EmailMessage(policy=default)
    content_type = f"{_text(body[0])}/{_text(body[1])}".lower()
    headers.set_type(content_type)
    _add_parameters(headers, "Content-Type", body[2])
    if body[3]:
        headers["Content-ID"] = _text(body[3])
    headers["Content-Transfer-Encoding"] = _text(body[5])
    disposition_index = 9 if content_type.startswith("text/") else 8
    if content_type == "message/rfc822":
        disposition_index = 11
    if len(body) > disposition_index and body[disposition_index]:
        disposition = body[disposition_index]
        headers["Content-Disposition"] = _text(disposition[0]).lower()
        _add_parameters(headers, "Content-Disposition", disposition[1])
    size = body[6]
    if not isinstance(size, int) or size < 0:
        raise ValueError("Invalid MIME size.")
    return [MimePart(prefix or "1", headers, size)]


def fetch_fields(connection, uid: str, query: str) -> dict:
    status, data = connection.uid("FETCH", uid, query)
    if status != "OK":
        raise ValueError("Could not fetch the message part from the server.")
    rows = parse_fetch_response(data)
    fields = rows.get(int(uid))
    if fields is None:
        raise MessageMissingError("The message no longer exists. Sync again.")
    return fields


def fetch_part(connection, uid: str, part: MimePart) -> tuple[EmailMessage, bytes]:
    section = part.section
    fields = fetch_fields(
        connection, uid, f"(UID BODY.PEEK[{section}.MIME] BODY.PEEK[{section}])"
    )
    headers, payload = (
        fields.get(f"BODY[{section}.MIME]".encode()),
        fields.get(f"BODY[{section}]".encode()),
    )
    if not isinstance(payload, bytes):
        raise TypeError("The server did not return the complete message part.")
    # Outlook can append CRLF to text without counting it in BODYSTRUCTURE.
    text_line_ending = (
        not part.is_attachment
        and part.headers.get_content_type() in {"text/plain", "text/html"}
        and len(payload) == part.encoded_size + 2
        and payload.endswith(b"\r\n")
    )
    if len(payload) != part.encoded_size and not text_line_ending:
        raise ValueError("The server returned an incomplete message part.")
    # Some Outlook single-part messages return NIL for section 1.MIME.
    if headers is None or headers == b"":
        headers = part.headers.as_bytes()
    if not isinstance(headers, bytes):
        raise TypeError("The server returned invalid MIME headers.")
    message = message_from_bytes(
        headers.rstrip(b"\r\n") + b"\r\n\r\n" + payload, policy=default
    )
    # Decode transfer encoding without treating an attached .eml as a MIME tree.
    decoder = EmailMessage(policy=default)
    decoder["Content-Transfer-Encoding"] = message.get(
        "Content-Transfer-Encoding", "7bit"
    )
    decoder.set_payload(payload)
    content = decoder.get_payload(decode=True)
    if not isinstance(content, bytes):
        raise TypeError("Could not decode the message part.")
    return message, content


def _add_parameters(headers: EmailMessage, name: str, parameters: tuple | None) -> None:
    if not parameters:
        return
    if len(parameters) % 2:
        raise ValueError("Invalid MIME parameters.")
    for key, value in zip(parameters[::2], parameters[1::2], strict=True):
        headers.set_param(_text(key), _text(value), header=name)


def _text(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")
