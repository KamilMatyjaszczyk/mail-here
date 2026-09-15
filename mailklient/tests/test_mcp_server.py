"""Exercise the optional MCP adapter through real MCP clients and stdio."""

import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("mcp.server.mcpserver", reason="Install the optional .[mcp] dependencies")

import anyio
from jsonschema import validate
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from mailklient.domain.mail_queries import EmailPage
from mailklient.domain.models import Attachment, Message
from mailklient.mcp_server.server import MAX_RESPONSE_BYTES, create_server
from mailklient.services import MailService, MailStore

TOOLS = {"search_emails", "get_recent_emails", "get_unread_emails", "get_email", "get_thread"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def cache(tmp_path):
    store = MailStore(tmp_path / "mail cache #1.sqlite3")
    account = store.add_account("Personal", "me@example.com")
    other = store.add_account("Work", "work@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    sent = store.add_folder(account.id, "Sent")
    other_inbox = store.add_folder(other.id, "INBOX")
    root = store.add_message(
        account.id, inbox.id, subject="Invoice ÆØÅ", body_text="private-mcp-test-body",
        sender="alice@example.com", message_id="<root@example.com>",
        received_at="2026-01-01T12:00:00Z",
    )
    reply = store.add_message(
        account.id, sent.id, subject="Re: Invoice ÆØÅ", is_read=True,
        message_id="<reply@example.com>", references="<root@example.com>",
        received_at="2026-01-02T12:00:00Z",
    )
    outside = store.add_message(
        other.id, other_inbox.id, message_id="<root@example.com>",
        received_at="2026-01-03T12:00:00Z",
    )
    store.replace_message_attachments(root.id, [
        Attachment(0, root.id, "test.txt", "text/plain", 6, content=b"secret")
    ])
    return SimpleNamespace(
        store=store, account=account, root=root, reply=reply, outside=outside,
        path=store.database_path,
    )


def parameters(path, *, from_environment=False):
    return StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "mailklient.mcp_server"] + (
            [] if from_environment else ["--database", str(path)]
        ),
        env={"MAILKLIENT_DATABASE_PATH": str(path)},
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_stdio_discovery_queries_schemas_and_no_writes(cache, tmp_path, mode):
    before = cache.path.read_bytes()
    with (tmp_path / "stderr.txt").open("w+") as stderr, anyio.fail_after(20):
        async with Client(
            stdio_client(parameters(cache.path, from_environment=mode == "auto"), errlog=stderr),
            mode=mode, read_timeout_seconds=5,
        ) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == TOOLS
            for tool in tools.values():
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.destructive_hint is False
                assert tool.output_schema is not None

            async def call(name, args):
                result = await client.call_tool(name, args)
                assert not result.is_error, result.content
                validate(result.structured_content, tools[name].output_schema)
                return result.structured_content

            page = await call("get_recent_emails", {"limit": 1})
            assert [m["id"] for m in page["items"]] == [cache.outside.id]
            assert page["next_offset"] == 1
            page = await call("get_recent_emails", {"offset": page["next_offset"]})
            assert [m["id"] for m in page["items"]] == [cache.reply.id, cache.root.id]
            assert page["next_offset"] is None
            page = await call("search_emails", {
                "query": "invoice æøå", "filters": {"account_id": cache.account.id},
                "sort_order": "date_asc",
            })
            assert [m["id"] for m in page["items"]] == [cache.root.id, cache.reply.id]
            page = await call("search_emails", {"query": "' OR 1=1 --"})
            assert page["items"] == []
            page = await call("get_unread_emails", {"filters": {"account_id": cache.account.id}})
            assert [m["id"] for m in page["items"]] == [cache.root.id]
            detail = await call("get_email", {"email_id": cache.root.id})
            assert detail["message"]["body_text"] == "private-mcp-test-body"
            assert detail["has_attachments"] is True
            assert detail["attachments"][0]["content"] is None
            page = await call("get_thread", {"thread_id": detail["thread_id"]})
            assert [m["id"] for m in page["items"]] == [cache.root.id, cache.reply.id]
            for name in ["send_email", "delete_email", "archive_email", "mark_message_read"]:
                result = await client.call_tool(name, {"email_id": cache.root.id})
                assert result.is_error
        stderr.seek(0)
        log = stderr.read()
        assert "private-mcp-test-body" not in log
        assert "alice@example.com" not in log
    assert cache.path.read_bytes() == before
    assert cache.store.get_message(cache.root.id).is_read is False


@pytest.mark.anyio
async def test_invalid_requests_and_missing_ids_are_tool_errors(cache):
    service = MailService(cache.path)
    async with Client(create_server(service)) as client:
        for name, args, code in [
            ("get_email", {"email_id": 999}, "EmailNotFound"),
            ("get_thread", {"thread_id": 999}, "ThreadNotFound"),
            ("get_unread_emails", {"filters": {"is_read": True}}, "InvalidSearchQuery"),
            ("search_emails", {"filters": {"after": "yesterday"}}, "InvalidSearchQuery"),
        ]:
            result = await client.call_tool(name, args)
            assert result.is_error
            assert code in result.content[0].text
        for name, args in [
            ("get_recent_emails", {"limit": 0}),
            ("get_recent_emails", {"limit": 201}),
            ("get_recent_emails", {"limit": True}),
            ("get_recent_emails", {"limit": "2"}),
            ("get_recent_emails", {"offset": -1}),
            ("get_email", {}),
            ("get_email", {"email_id": False}),
            ("get_thread", {"thread_id": 2**63}),
            ("search_emails", {"query": "x" * 1001}),
            ("search_emails", {"sort_order": "invalid"}),
            ("search_emails", {"filters": {"account_id": True}}),
            ("search_emails", {"filters": {"is_read": "false"}}),
            ("search_emails", {"filters": {"unknown_field": 1}}),
        ]:
            result = await client.call_tool(name, args)
            assert result.is_error, (name, args)
        # Errors must not terminate the protocol session.
        assert not (await client.call_tool("get_recent_emails", {})).is_error


@pytest.mark.anyio
@pytest.mark.parametrize("broken", [False, True])
async def test_stdio_unavailable_cache_is_not_created_or_changed(tmp_path, broken):
    path = tmp_path / "unavailable.sqlite3"
    if broken:
        path.write_bytes(b"not a SQLite database")
    with anyio.fail_after(20):
        async with Client(parameters(path), read_timeout_seconds=5) as client:
            assert len((await client.list_tools()).tools) == 5
            result = await client.call_tool("get_recent_emails", {})
            assert result.is_error
            assert "MailStoreUnavailable" in result.content[0].text
            assert str(path) not in result.content[0].text
    assert path.read_bytes() == b"not a SQLite database" if broken else not path.exists()


@pytest.mark.anyio
async def test_adapter_uses_service_off_event_loop_and_masks_unexpected_errors(caplog):
    service = Mock(spec=MailService)
    event_thread = threading.get_ident()
    called_threads = []

    def read(**kwargs):
        called_threads.append(threading.get_ident())
        return EmailPage((), 50, 0, None)

    service.get_recent_emails.side_effect = read
    async with Client(create_server(service)) as client:
        result = await client.call_tool("get_recent_emails", {})
        assert not result.is_error
        assert called_threads and called_threads[0] != event_thread
        service.get_recent_emails.side_effect = RuntimeError("private-secret-diagnostics")
        result = await client.call_tool("get_recent_emails", {})
        assert result.is_error
        assert "MailReadFailed" in result.content[0].text
        assert "private-secret-diagnostics" not in str(result)
    assert "private-secret-diagnostics" not in caplog.text


@pytest.mark.anyio
async def test_oversized_result_returns_error_without_partial_mail(cache):
    large = cache.store.add_message(
        cache.root.account_id, cache.root.folder_id,
        subject="Large synthetic message", body_text="x" * MAX_RESPONSE_BYTES,
        body_html="<html>" + "y" * MAX_RESPONSE_BYTES + "</html>",
        received_at="2026-09-15T12:00:00Z",
    )
    before = cache.path.read_bytes()
    async with Client(create_server(MailService(cache.path))) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in ("get_recent_emails", "search_emails", "get_unread_emails"):
            result = await client.call_tool(name, {"limit": 1})
            assert not result.is_error
            data = result.structured_content
            validate(data, tools[name].output_schema)
            assert data["items"][0]["id"] == large.id
            assert "body_text" not in data["items"][0]
            assert "body_html" not in data["items"][0]
            assert "body_preview" not in data["items"][0]
            assert len(result.content[0].text) < 12000
        result = await client.call_tool("get_email", {"email_id": large.id})
        assert result.is_error
        assert "ResponseTooLarge" in result.content[0].text
        assert result.structured_content is None
    assert cache.path.read_bytes() == before


def test_server_import_is_headless_and_cli_has_no_network_transport():
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "import sys; from mailklient.mcp_server.server import create_server; "
        "assert not any(n.startswith('PySide6') for n in sys.modules)",
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([
        sys.executable, "-B", "-m", "mailklient.mcp_server", "--database", "relative.db",
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "absolute path" in result.stderr
    assert result.stdout == ""
