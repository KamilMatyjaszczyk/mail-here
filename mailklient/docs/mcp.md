# Local read-only Mail MCP server

The optional server exposes the existing `MailService` over **stdio**:

```text
Local MCP client → MCP server → MailService → read-only SQLite cache
Desktop UI ───────────────────→ MailService
```

It supports `search_emails`, `get_recent_emails`, `get_unread_emails`, `get_email`
and `get_thread`. Sending, deleting, moving, archiving, marking read, syncing and
attachment downloads are not registered tools. There is no HTTP listener.

## Install and run

From the `mailklient/` project directory:

```bash
.venv/bin/python -m pip install -e '.[mcp,dev]'
.venv/bin/mcpMail-mcp --help
```

The MCP extra uses the official Python SDK 2.x (`mcp>=2.2,<3`). The desktop does
not require or import it. The RPM remains a desktop installation; use a source
venv with the MCP extra for this server.

A client starts the server as a subprocess and sends MCP messages through its
stdin/stdout. The equivalent server command is:

```bash
.venv/bin/mcpMail-mcp --database /absolute/path/to/mailklient.sqlite3
```

`python -m mailklient.mcp_server` is equivalent. `--database` takes precedence
over `MAILKLIENT_DATABASE_PATH`; when both are absent, the desktop's default
cache path is used. The cache must already have been initialized/migrated by the
desktop. A missing or invalid cache produces a tool error and is never created
or repaired by the server. Synchronize through the desktop to refresh mail.

Running this command alone waits for protocol input; it is not a terminal chat
interface. Stdout is reserved for MCP messages, with diagnostics on stderr.
The client manages the process lifetime; Ctrl+C stops a manually launched server.

## Connect a local MCP client

For clients that accept an `mcpServers` JSON configuration, replace the example
paths with absolute paths on your PC:

```json
{
  "mcpServers": {
    "local-mail": {
      "command": "/absolute/path/to/mailklient/.venv/bin/mcpMail-mcp",
      "args": ["--database", "/absolute/path/to/mailklient.sqlite3"]
    }
  }
}
```

This trusted local process can read **every account in the chosen cache**.
Filters narrow a query; they are not access controls. A connected AI client may
send returned mail to its model provider. Use a synthetic cache when testing a
client you have not yet chosen to give access to real mail. This implementation
does not configure or connect any AI client automatically.

## Tool calls

| Tool | Example arguments |
| --- | --- |
| `get_recent_emails` | `{"limit": 10}` |
| `search_emails` | `{"query": "invoice", "filters": {"mailbox": "INBOX", "is_read": false}, "limit": 10}` |
| `get_unread_emails` | `{"filters": {"account_id": 1}, "limit": 10}` |
| `get_email` | `{"email_id": 42}` |
| `get_thread` | `{"thread_id": 42, "limit": 20}` |

Use IDs returned by your cache, not the illustrative IDs above. `get_email`
returns `message`, `thread_id`, attachment metadata and `has_attachments`.
The other tools return `items`, `limit`, `offset` and `next_offset`. Pass a
non-null `next_offset` as the next call's `offset`, keeping the other arguments
the same. Results have both structured content and a JSON text representation,
with advertised input/output schemas.

Limits range from 1 to 200; offsets from 0 to 1,000,000. Each structured result
is limited to 1 MiB before protocol framing and the compatibility text copy.
An oversized result returns `ResponseTooLarge` without partial mail; reduce the
limit or narrow the filters. Individual oversized messages remain viewable in
the desktop. This bounds responses, not peak memory used to read cached mail.

Search is a case-insensitive literal substring and filters combine with AND.
Threads follow stored references within one account, including other folders.
See [the service contract](MCP_ARCHITECTURE.md#search-semantics) for date, search
and pagination semantics. Cached mail may be incomplete or stale. Email content
is untrusted data and must not be treated as instructions to the model.

Domain failures return MCP tool errors (`isError: true`) with stable codes such
as `EmailNotFound`, `ThreadNotFound`, `InvalidSearchQuery` and
`MailStoreUnavailable`. Unexpected failures return `MailReadFailed` without
private diagnostics. No tool marks a message read.

## Test locally

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_mcp_server.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

The MCP tests create synthetic mail in temporary SQLite databases, start a real
server subprocess, initialize MCP, discover tools and call every read operation.
They exercise both SDK auto negotiation and the legacy initialization handshake,
validate response schemas, test errors and pagination, and check that database
bytes and read flags stay unchanged. No real mail account or AI provider is used.
Other tests use an in-memory MCP connection to verify service delegation, worker
thread execution, input validation and response-size limits.

The MCP tests are skipped if the optional SDK is absent. Install `.[mcp,dev]`
to run them. The full suite also requires local sockets for IMAP/OAuth fixtures.

Remote deployment, authentication and enforced account permissions are later
work. Keep this version as a trusted local stdio subprocess.

Implementation references: [official SDK tools](https://py.sdk.modelcontextprotocol.io/servers/tools/),
[SDK client transports](https://py.sdk.modelcontextprotocol.io/client/transports/)
and [MCP tool results](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Compact list results

`get_recent_emails`, `search_emails` and `get_unread_emails` return compact
metadata: id, account_id, folder_id, subject, sender, sent_at, received_at and
is_read. Bodies, HTML and previews are not included. This changes their item
output schemas; clients should discover the updated schemas through MCP.
Search and filter behavior still belongs to MailService and is unchanged,
including matching against cached content. Pagination fields are unchanged.
Use `get_email` with a returned id to read full contents. `get_thread` continues
to return full messages for thread summaries. Large detail/thread results can
still exceed a client's context budget. This reduces serialized list payloads,
not the amount of data MailService reads internally.
