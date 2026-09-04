"""HTML-based message viewer widgets."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import os
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from mailklient.domain import Attachment, Message

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # pragma: no cover - depends on the local PySide6 build
    QWebEnginePage = None
    QWebEngineSettings = None
    QWebEngineView = None


class MessageViewer(QWidget):
    """Render a cached email with a Gmail-like reading layout."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("message_view")
        self._plain_text = ""
        self._html = ""
        self._allow_remote_content = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if _should_use_web_engine():
            self._viewer = QWebEngineView()
            self._viewer.setObjectName("message_view_web")
            self._viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            if QWebEnginePage is not None:
                self._web_page = _ExternalLinkWebPage(self._viewer)
                self._viewer.setPage(self._web_page)
            if QWebEngineSettings is not None:
                settings = self._viewer.settings()
                settings.setAttribute(
                    QWebEngineSettings.WebAttribute.AutoLoadImages,
                    True,
                )
                settings.setAttribute(
                    QWebEngineSettings.WebAttribute.JavascriptEnabled,
                    False,
                )
                settings.setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                    False,
                )
        else:
            self._viewer = QTextBrowser()
            self._viewer.setObjectName("message_view_text")
            self._viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self._viewer.setOpenExternalLinks(False)
            self._viewer.setReadOnly(True)
            self._viewer.anchorClicked.connect(_open_external_url)

        layout.addWidget(self._viewer)
        self.set_empty()

    def isReadOnly(self) -> bool:
        """Compatibility helper used by UI tests."""

        return True

    def toPlainText(self) -> str:
        """Return the last rendered message as searchable plain text."""

        return self._plain_text

    def rendered_html(self) -> str:
        """Return the last rendered HTML document."""

        return self._html

    def set_remote_content_allowed(self, allowed: bool) -> None:
        """Allow or block remote resources in rendered email HTML."""

        self._allow_remote_content = allowed
        if _should_use_web_engine() and QWebEngineSettings is not None:
            settings = self._viewer.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                allowed,
            )

    def remote_content_allowed(self) -> bool:
        """Return whether remote email resources are allowed."""

        return self._allow_remote_content

    def set_empty(self) -> None:
        """Show the empty selection state."""

        self._plain_text = "Velg en melding"
        self._set_html(
            _render_document(
                title="Velg en melding",
                sender="",
                recipients="",
                date_text="",
                account_label="",
                attachments=[],
                body_html='<p class="empty-state">Velg en melding</p>',
            )
        )

    def show_message(
        self,
        message: Message,
        account_label: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        """Render a selected message."""

        attachments = attachments or []
        date_text = message.received_at or message.sent_at or ""
        body_text = message.body_text or message.body_preview
        if message.body_html:
            body_html = _sanitize_email_html(
                message.body_html,
                allow_remote_content=self._allow_remote_content,
            )
            plain_body = _html_to_text(message.body_html) or body_text
        else:
            body_html = f'<pre class="plain-body">{escape(body_text)}</pre>'
            plain_body = body_text

        self._plain_text = "\n".join(
            [
                f"Konto: {account_label}",
                f"Emne: {message.subject}",
                f"Fra: {message.sender}",
                f"Til: {message.recipients}",
                f"Dato: {date_text}",
                "",
                plain_body,
                "",
                _attachments_text(attachments),
            ]
        )
        self._set_html(
            _render_document(
                title=message.subject or "(uten emne)",
                sender=message.sender,
                recipients=message.recipients,
                date_text=date_text,
                account_label=account_label,
                attachments=attachments,
                body_html=body_html,
            )
        )

    def _set_html(self, html: str) -> None:
        self._html = html
        if isinstance(self._viewer, QTextBrowser):
            self._viewer.setHtml(html)
            return

        self._viewer.setHtml(html, QUrl("about:blank"))


if QWebEnginePage is not None:

    class _ExternalLinkWebPage(QWebEnginePage):
        """Open user-clicked email links in the default browser."""

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            if _is_external_click_url(url):
                _open_external_url(url)
                return False
            return super().acceptNavigationRequest(
                url,
                navigation_type,
                is_main_frame,
            )

        def createWindow(self, _window_type):
            return _ExternalPopupWebPage(self)


    class _ExternalPopupWebPage(QWebEnginePage):
        """Route target=_blank windows to the default browser."""

        def acceptNavigationRequest(self, url, _navigation_type, _is_main_frame):
            if _is_external_click_url(url):
                _open_external_url(url)
            return False


else:
    _ExternalLinkWebPage = None
    _ExternalPopupWebPage = None


def _is_external_click_url(url: QUrl) -> bool:
    return url.scheme().casefold() in {"http", "https", "mailto", "tel"}


def _open_external_url(url: QUrl) -> bool:
    if not _is_external_click_url(url):
        return False
    return QDesktopServices.openUrl(url)


def _render_document(
    *,
    title: str,
    sender: str,
    recipients: str,
    date_text: str,
    account_label: str,
    attachments: list[Attachment],
    body_html: str,
) -> str:
    escaped_title = escape(title)
    escaped_sender = escape(sender or "(ukjent avsender)")
    escaped_recipients = escape(recipients)
    escaped_date = escape(date_text)
    escaped_account = escape(account_label)
    attachments_html = _attachments_html(attachments)
    header_details = ""
    if sender or recipients or date_text or account_label:
        header_details = f"""
      <div class="sender-row">
        <div class="sender-block">
          <div class="sender">{escaped_sender}</div>
          <div class="meta">til {escaped_recipients}</div>
          <div class="meta">Konto: {escaped_account}</div>
        </div>
        <div class="date">{escaped_date}</div>
      </div>"""

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    :root {{
      color-scheme: light;
      font-family: "Noto Sans", "Inter", "Segoe UI", Arial, sans-serif;
    }}

    html,
    body {{
      background: #fcfcfd;
      color: #232629;
      margin: 0;
      min-height: 100%;
    }}

    .message-shell {{
      box-sizing: border-box;
      margin: 0 auto;
      max-width: 980px;
      padding: 30px 38px 46px;
    }}

    .app-header {{
      border-bottom: 1px solid #dfe3ea;
      margin-bottom: 22px;
      padding-bottom: 20px;
    }}

    .subject {{
      color: #232629;
      font-size: 23px;
      font-weight: 500;
      line-height: 1.35;
      margin-bottom: 18px;
    }}

    .sender-row {{
      display: table;
      width: 100%;
    }}

    .sender-block {{
      display: table-cell;
      vertical-align: top;
      width: auto;
    }}

    .sender {{
      font-size: 14px;
      font-weight: 600;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}

    .meta {{
      color: #6f7885;
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    .date {{
      color: #6f7885;
      display: table-cell;
      font-size: 12px;
      text-align: right;
      vertical-align: top;
      white-space: nowrap;
      width: 1%;
    }}

    .email-body {{
      color: #232629;
      font-size: 14px;
      line-height: 1.6;
      overflow-wrap: anywhere;
    }}

    .attachments {{
      border-bottom: 1px solid #dfe3ea;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: -8px 0 22px;
      padding-bottom: 18px;
    }}

    .attachment {{
      background: #f7f9fc;
      border: 1px solid #cfd8e3;
      border-radius: 6px;
      color: #232629;
      font-size: 13px;
      padding: 8px 10px;
    }}

    .attachment-name {{
      font-weight: 700;
      margin-right: 6px;
    }}

    .attachment-meta {{
      color: #6f7885;
    }}

    .email-body img {{
      height: auto;
      max-width: 100%;
    }}

    .email-body video {{
      background: #111827;
      border-radius: 6px;
      height: auto;
      max-width: 100%;
    }}

    .blocked-remote-image {{
      border: 1px dashed #dadce0;
      color: #5f6368;
      display: inline-block;
      font-size: 12px;
      min-height: 20px;
      min-width: 120px;
      padding: 8px;
    }}

    .email-body table {{
      max-width: 100%;
    }}

    .plain-body {{
      font-family: "Noto Sans", "Inter", "Segoe UI", Arial, sans-serif;
      white-space: pre-wrap;
    }}

    .empty-state {{
      color: #6f7885;
      margin-top: 24px;
    }}
  </style>
</head>
<body>
  <article class="message-shell">
    <header class="app-header">
      <div class="subject">{escaped_title}</div>
{header_details}
    </header>
    {attachments_html}
    <main class="email-body">{body_html}</main>
  </article>
</body>
</html>"""


def _attachments_html(attachments: list[Attachment]) -> str:
    if not attachments:
        return ""

    items = []
    for attachment in attachments:
        name = escape(attachment.filename)
        meta = escape(
            f"{attachment.content_type}, {_format_size(attachment.size)}"
        )
        items.append(
            '<div class="attachment">'
            f'<span class="attachment-name">{name}</span>'
            f'<span class="attachment-meta">{meta}</span>'
            "</div>"
        )
    return '<section class="attachments">' + "".join(items) + "</section>"


def _attachments_text(attachments: list[Attachment]) -> str:
    if not attachments:
        return ""
    lines = ["Vedlegg:"]
    for attachment in attachments:
        lines.append(
            f"- {attachment.filename} ({attachment.content_type}, "
            f"{_format_size(attachment.size)})"
        )
    return "\n".join(lines)


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _should_use_web_engine() -> bool:
    if QWebEngineView is None:
        return False
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    return os.environ.get("MAILKLIENT_DISABLE_WEBENGINE") != "1"


def _sanitize_email_html(
    body_html: str,
    *,
    allow_remote_content: bool = False,
) -> str:
    parser = _EmailHtmlSanitizer(allow_remote_content=allow_remote_content)
    parser.feed(body_html)
    parser.close()
    return parser.html()


def _html_to_text(body_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(body_html)
    return parser.text()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style", "head"}:
            self._ignored_depth += 1
        if tag in {"br", "p", "div", "tr", "li"} and not self._ignored_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"p", "div", "tr", "li"} and not self._ignored_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        text = "".join(self._parts)
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()


class _EmailHtmlSanitizer(HTMLParser):
    _allowed_tags = {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "center",
        "code",
        "del",
        "div",
        "em",
        "font",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
        "source",
        "video",
    }
    _void_tags = {"br", "hr", "img", "source"}
    _ignored_tags = {
        "base",
        "button",
        "embed",
        "form",
        "head",
        "iframe",
        "input",
        "link",
        "meta",
        "object",
        "script",
        "style",
    }
    _drop_content_tags = {"button", "form", "head", "iframe", "script", "style"}
    _global_attrs = {
        "align",
        "bgcolor",
        "class",
        "height",
        "style",
        "title",
        "valign",
        "width",
    }
    _tag_attrs = {
        "a": {"href", "name", "target"},
        "font": {"color", "face", "size"},
        "img": {"alt", "src", "srcset", "height", "width"},
        "ol": {"start", "type"},
        "source": {"src", "type"},
        "table": {"border", "cellpadding", "cellspacing"},
        "td": {"colspan", "rowspan"},
        "th": {"colspan", "rowspan"},
        "ul": {"type"},
        "video": {
            "controls",
            "height",
            "poster",
            "preload",
            "src",
            "width",
        },
    }

    def __init__(self, *, allow_remote_content: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._allow_remote_content = allow_remote_content
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        if tag in self._ignored_tags:
            if tag in self._drop_content_tags:
                self._ignored_depth += 1
            return
        if self._ignored_depth or tag not in self._allowed_tags:
            return

        clean_attrs = self._clean_attrs(tag, attrs)
        attrs_html = _attrs_html(clean_attrs)
        self._parts.append(f"<{tag}{attrs_html}>")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        if self._ignored_depth or tag not in self._allowed_tags:
            return

        clean_attrs = self._clean_attrs(tag, attrs)
        attrs_html = _attrs_html(clean_attrs)
        self._parts.append(f"<{tag}{attrs_html}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._drop_content_tags and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth or tag not in self._allowed_tags:
            return
        if tag not in self._void_tags:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(escape(data))

    def html(self) -> str:
        return "".join(self._parts)

    def _clean_attrs(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> list[tuple[str, str]]:
        clean_attrs: list[tuple[str, str]] = []
        remote_image_blocked = False
        allowed_attrs = self._global_attrs | self._tag_attrs.get(tag, set())

        for raw_name, raw_value in attrs:
            name = raw_name.casefold()
            value = raw_value or ""
            if name.startswith("on") or name not in allowed_attrs:
                continue
            if name == "style":
                value = _clean_style(
                    value,
                    allow_remote_content=self._allow_remote_content,
                )
                if not value:
                    continue
            if name == "srcset" and tag == "img":
                value = _clean_srcset(
                    value,
                    allow_remote_content=self._allow_remote_content,
                )
                if not value:
                    remote_image_blocked = True
                    continue
            if name in {"href", "poster", "src"}:
                is_resource = tag in {"img", "source", "video"} and name in {
                    "poster",
                    "src",
                }
                if not _is_safe_url(
                    value,
                    allow_remote_content=self._allow_remote_content,
                    is_resource=is_resource,
                ):
                    if tag == "img" and name in {"poster", "src"}:
                        remote_image_blocked = True
                    continue
                value = _normalize_url(value)
            clean_attrs.append((name, value))

        if remote_image_blocked:
            clean_attrs = [
                (name, value)
                for name, value in clean_attrs
                if name not in {"height", "width"}
            ]
            classes = [
                value for name, value in clean_attrs if name == "class"
            ]
            clean_attrs = [
                (name, value)
                for name, value in clean_attrs
                if name != "class"
            ]
            class_value = " ".join([*classes, "blocked-remote-image"]).strip()
            clean_attrs.append(("class", class_value))
            if not any(name == "alt" for name, _value in clean_attrs):
                clean_attrs.append(("alt", "Eksternt bilde blokkert"))

        if tag == "a" and any(name == "href" for name, _value in clean_attrs):
            clean_attrs = [
                (name, value)
                for name, value in clean_attrs
                if name not in {"target", "rel"}
            ]
            clean_attrs.append(("target", "_blank"))
            clean_attrs.append(("rel", "noreferrer noopener"))

        return clean_attrs


def _attrs_html(attrs: list[tuple[str, str]]) -> str:
    if not attrs:
        return ""
    return "".join(
        f' {escape(name, quote=True)}="{escape(value, quote=True)}"'
        for name, value in attrs
    )


def _clean_style(style: str, *, allow_remote_content: bool = False) -> str:
    lowered = style.casefold()
    if "@import" in lowered or "expression(" in lowered:
        return ""
    if "behavior:" in lowered:
        return ""

    def replace_url(match: re.Match[str]) -> str:
        url = match.group("url").strip().strip("'\"")
        if not _is_safe_url(
            url,
            allow_remote_content=allow_remote_content,
            is_resource=True,
        ):
            raise ValueError
        return f'url("{_normalize_url(url)}")'

    try:
        return re.sub(
            r"url\(\s*(?P<url>[^)]+?)\s*\)",
            replace_url,
            style,
            flags=re.IGNORECASE,
        )
    except ValueError:
        return ""


def _clean_srcset(value: str, *, allow_remote_content: bool) -> str:
    candidates: list[str] = []
    for raw_candidate in value.split(","):
        candidate = raw_candidate.strip()
        if not candidate:
            continue
        parts = candidate.split(maxsplit=1)
        url = parts[0]
        descriptor = f" {parts[1]}" if len(parts) > 1 else ""
        if not _is_safe_url(
            url,
            allow_remote_content=allow_remote_content,
            is_resource=True,
        ):
            continue
        candidates.append(f"{_normalize_url(url)}{descriptor}")
    return ", ".join(candidates)


def _normalize_url(url: str) -> str:
    stripped_url = url.strip()
    if stripped_url.startswith("//"):
        return f"https:{stripped_url}"
    return stripped_url


def _is_safe_url(
    url: str,
    *,
    allow_remote_content: bool,
    is_resource: bool,
) -> bool:
    normalized = url.strip().casefold()
    if not normalized or normalized.startswith("#"):
        return True
    if normalized.startswith(("cid:", "mailto:", "tel:")):
        return True
    if normalized.startswith("data:image/"):
        return True
    if normalized.startswith(("http://", "https://", "//")):
        return allow_remote_content if is_resource else True
    return ":" not in normalized
