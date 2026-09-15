# Read-only mail service and local MCP adapter

The local Python read contracts are now used by an optional stdio MCP adapter.
See [the local MCP guide](mcp.md) for installation, client configuration and tests.
No HTTP listener, remote authentication or write tools are implemented.

## Architecture

```text
Desktop UI                         Local stdio MCP adapter
    |                                      |
    +------------- MailService -------------+
                       |
             MailRepository (Protocol)
                       |
               MailReadRepository
                       |
             existing SQLite cache (read-only)

Desktop commands / background workers
    |-- MailSyncService --> IMAP --> MailStore --> SQLite
    |-- MailSendService --> SMTP / sent-copy persistence
    |-- DraftService --> local drafts
    +-- AttachmentService --> explicit download/cache operations

security/ --> keyring, OAuth, TLS, safe attachment files
```

- `domain/models.py` remains the source for `Message` and `Attachment`.
  `domain/mail_queries.py` adds only filters and result envelopes, not duplicate
  mail models. `domain/errors.py` defines the public read errors.
- `services/mail_service.py` validates requests, orchestrates reads and page
  traversal, and emits content-free operation logs.
- `domain/mail_repository.py` defines the injectable `MailRepository` protocol.
  Implementations accept normalized filters, return the shared result models,
  and report storage failures as domain errors.
- `database/mail_reader.py` owns parameterized SQL, pagination, transactions
  and translation of SQLite failures to `MailStoreUnavailable`.
  Each operation opens/closes its own `mode=ro` connection with `query_only`.
- `MailStore` and `database/repositories.py` retain existing cache writes.
  `MailStore` construction initializes/migrates the database and can clean up
  empty placeholders. **A read adapter must not construct MailStore.**
- The desktop search and message reader call `MailService`. `main.py` injects
  the service into `MainWindow`; tests can supply an alternative instance.
  The service's `iter_emails()` consumes search pages lazily. The desktop
  renders all results to preserve its existing full-list behavior. Large-list
  UI virtualization and asynchronous local search are separate future work.
- `mail/` remains the provider/protocol layer; `workers/` handles GUI background
  jobs. None of the read operations requires Qt, a worker, keyring or a prompt.

`MailService(database_path)` uses the existing SQLite repository.
`MailService(repository=custom_repository)` uses an injected implementation;
provide exactly one source. No database is opened when injecting a repository.
The old import `from mailklient.services.mail_read import MailReadService` remains
an alias for compatibility.

## Available operations

| Method | Result / behavior |
| --- | --- |
| `search_emails(query="", filters=None, *, limit=50, offset=0, sort_order="date_desc")` | `EmailPage` |
| `get_recent_emails(*, filters=None, limit=50, offset=0)` | Newest cached messages |
| `get_unread_emails(*, filters=None, limit=50, offset=0)` | Unread cached messages |
| `get_emails_from_sender(sender, *, filters=None, limit=50, offset=0)` | Exact mailbox-address match |
| `get_email(email_id)` | `EmailDetails(message, thread_id, attachments)` |
| `get_thread(thread_id, *, limit=50, offset=0)` | Cached thread members, oldest first |
| `get_attachments(email_id)` | Tuple of `Attachment` metadata; no content download |

`iter_emails(query="", filters=None, *, page_size=200, sort_order="date_desc")`
is a local convenience iterator used by the desktop. It validates on iteration,
fetches pages on demand and propagates failures. An adapter should expose the
bounded operations above, rather than collecting an unlimited iterator.

An `EmailPage` contains `items`, `limit`, `offset`, and `next_offset` (`None` at
the end). `EmailPage.to_dict()` and `EmailDetails.to_dict()` are JSON-serializable;
attachment metadata can be serialized with `dataclasses.asdict`. No returned
attachment contains bytes or a local filesystem path. `has_content` tells whether
bytes are already cached. `EmailDetails.to_dict()` also includes `has_attachments`.

`Message` keeps its existing raw sender/recipient header strings and stored date
strings; consumers should not assume these are separate address objects or UTC
timestamps. New incoming messages retain both To and Cc in `recipients`.

```python
import json

from mailklient.config import default_database_path
from mailklient.domain.mail_queries import EmailFilters
from mailklient.services.mail_service import MailService

reader = MailService(default_database_path())
page = reader.search_emails(
    "invoice",
    EmailFilters(is_read=False, after="2026-01-01", mailbox="INBOX"),
    limit=20,
)
payload = page.to_dict()  # Return to an authorized caller; do not log this.
encoded = json.dumps(payload)
```

## Search semantics

All filters combine with AND. `EmailFilters` accepts `account_id`, `folder_id`,
`mailbox`, `sender`, `recipient`, `subject`, `is_read`, `after`, `before`,
`has_attachments`, and `thread_id`. Omitting account/folder scope searches the
whole cache, including sent and archived mail. Standard mailbox aliases use the
same mapping as the desktop. Use `folder_id` for a custom folder.

- Free text is a Unicode case-insensitive literal substring across subject,
  sender, recipients, preview and plain body. No query language, SQL wildcards,
  FTS index or semantic search is implied. HTML/CSS source is not searched.
- Sender/recipient filters match a parsed mailbox address, case-insensitively,
  not part of an address or a display name. Subject is a literal substring.
- `after` is inclusive; `before` is exclusive. A date means midnight UTC.
  Timestamps must include a timezone. Empty, reversed or invalid date ranges
  raise `InvalidSearchQuery`. Cached ISO/RFC dates are normalized for comparisons;
  legacy timezone-less dates mean UTC. Missing/invalid dates sort last and do not
  match date filters; valid `sent_at` is a fallback for absent/invalid `received_at`.
- `has_attachments` includes inline MIME parts; it does not mean downloaded.
- Limits are integers from 1 to 200, offsets from 0 to 1,000,000. Sort order is
  `date_desc`, `date_asc`, `sender` or `subject`, with an ID tie-breaker.
  Offsets beyond the results return an empty page. Separate page requests are
  not a shared snapshot: concurrent sync/deletion can shift offsets. Cursor or
  snapshot pagination can be added if an adapter requires a consistent export.

## Threads and migration

New IMAP fetches preserve `In-Reply-To` and `References`. Local sent replies keep
their parent reference too. Schema version 2 adds these fields and a small
`message_references` index, updated in the same transaction as message writes.
Existing message IDs are indexed during migration; no cache is deleted.

Threads are connected components of explicit bracketed Message-ID references
**within one account**. Missing ancestors and reference cycles are supported.
Identical subjects alone never merge threads. Distinct cached folder copies
remain distinct messages. The canonical `thread_id` is the lowest local message
ID in the component; any existing member ID also works as a thread anchor.
IDs are local cache identifiers, not Gmail conversation IDs or globally stable
identifiers. Merges, deletion or a UIDVALIDITY/cache rebuild can change them.

Old cache rows did not retain reply headers. They remain readable, but related
messages without cached references cannot be reconstructed reliably. Newly
fetched messages improve the index; this step does not force a full resync or
re-download old mail. A thread result means **all matching cached members across
its pages**, not proof that the entire server-side conversation is present.

## Errors and logging

`MailReadError` has these stable subclasses:

- `InvalidSearchQuery`: invalid parameters, types or conflicting filters.
- `EmailNotFound`: unknown ID for a detail/attachment request.
- `ThreadNotFound`: unknown anchor passed to `get_thread`.
- `MailStoreUnavailable`: missing, inaccessible, locked, outdated or invalid cache.

An ordinary search with no matches (including an unknown thread filter) returns
an empty page. SQL diagnostics and original exception messages are not exposed.
Provider/authentication error translation is left to a future explicit network
service; these read operations never authenticate or contact a provider.

The `mailklient.services.mail_service` logger emits INFO records with `operation`,
`success`, `duration_ms`, `result_count`, and `error_type`. It does not log search
arguments, identifiers, message content, addresses, attachment names, passwords,
tokens or exception tracebacks. Configure handlers/retention in the application
entry point or adapter; the library does not install a file handler or alter the
root logger. This is not a complete desktop crash-reporting implementation.

## Configuration and homelab deployment

`MAILKLIENT_DATABASE_PATH` selects an absolute cache path for both the desktop and
headless reader. Otherwise the existing
`${XDG_DATA_HOME:-~/.local/share}/mailklient/mailklient.sqlite3` location is used.
The read service accepts an explicit path as well. It never creates or migrates
a missing database; first initialize/upgrade it using the desktop application.

Provider host/port settings remain account configuration with existing provider
presets. OAuth configuration and tokens remain in keyring/environment overrides.
No server hostname, transport port or Tailscale address is added or hardcoded.
Keep SQLite local to the process accessing it; a future remote client should
call the adapter, not open the database over a shared network filesystem.

An always-on sync runner, headless OAuth/bootstrap and unattended keyring access
still need implementation and testing. A headless reader by itself does not keep
mail fresh. Moving to a NAS is not enabled merely by setting the database path.

## Validation

Run the service and UI integration tests without a visible desktop:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/test_mail_service.py tests/test_mail_read.py tests/test_mail_read_ui.py
```

These cover an injected repository without SQLite, validation before repository
access, real temporary caches, search/filter combinations, deterministic paging,
unread selection, detail serialization, thread isolation between accounts,
missing ancestors/cycles, migration, stable errors and private logs. They also
check that reads leave cache bytes and read flags unchanged, avoid credentials
and network calls, and import without Qt. The UI tests exercise injected service
use and lists spanning multiple pages. Run the full suite with
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`.

## Local adapter and future remote access

`mcp_server/server.py` registers five tools explicitly: `search_emails`,
`get_recent_emails`, `get_unread_emails`, `get_email` and `get_thread`. It converts
JSON filters to domain filters and uses `asyncio.to_thread` to call `MailService`.
Pydantic models provide transport schemas while reusing the domain message and
attachment models. Errors become MCP tool errors, and structured responses have
a 1 MiB budget. The CLI constructs only `MailService`, never `MailStore`.
The SDK is an optional extra and is not imported by the desktop.

The remaining considerations for future remote deployment are:

1. Add caller authentication, explicit account scope checks on every search and
   ID lookup, request-size limits and rate limits before remote exposure.
   Current filters are query options, **not authorization**. Read-only means no
   mutations, not permission to disclose all accounts. Tailscale access does not
   replace application authorization; consult the
   [MCP security guidance](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices).
2. Choose transport/bind address/port through deployment configuration. Extend
   the local protocol tests with remote authentication, account-scope enforcement
   and transport tests.
3. Treat all email/HTML/attachment content as untrusted data, never instructions
   to the agent. Decide explicitly what may be sent to an external AI provider.
   Summaries and action classification belong above this retrieval layer.
4. Later downloads and write tools require separate grants and services. Sending,
   deleting, moving, archiving and marking read must not become available through
   a read grant. Require confirmation for appropriate write actions. Do not rely
   on tool annotations alone to enforce access control.

Attachment metadata is ready; remote attachment retrieval, download-size limits,
safe resource delivery, an always-on sync process, remote MCP transports and AI
summaries are not implemented here.
