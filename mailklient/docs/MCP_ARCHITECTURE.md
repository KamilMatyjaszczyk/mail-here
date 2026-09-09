# Preparing a read-only MCP adapter

No MCP server, AI model, listener, API authentication or new dependency is added
in this step. These are local Python contracts that a thin adapter can call.

## Architecture

```text
Desktop UI                         Future MCP/API adapter
    |                                      |
    +---------- MailReadService ------------+
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
- `services/mail_read.py` validates requests, orchestrates reads, translates
  storage errors, and emits content-free operation logs.
- `database/mail_reader.py` owns parameterized SQL, pagination and transactions.
  Each operation opens/closes its own `mode=ro` connection with `query_only`.
- `MailStore` and `database/repositories.py` retain existing cache writes.
  `MailStore` construction initializes/migrates the database and can clean up
  empty placeholders. **A read adapter must not construct MailStore.**
- The desktop search and message reader now call `MailReadService`. The desktop
  consumes all pages to preserve its existing full-list behavior. Large-list
  UI virtualization and asynchronous local search are separate future work.
- `mail/` remains the provider/protocol layer; `workers/` handles GUI background
  jobs. None of the read operations requires Qt, a worker, keyring or a prompt.

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
from mailklient.services.mail_read import MailReadService

reader = MailReadService(default_database_path())
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

The `mailklient.services.mail_read` logger emits INFO records with `operation`,
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

## Building the future adapter

1. Add a thin adapter module that constructs only `MailReadService`. Explicitly
   register the seven read operations with input/output schemas; do not discover
   and publish every method in `services/` automatically. Map stable domain
   errors to tool errors. MCP supports structured tool results and schemas;
   see the [official tool specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
2. SQLite is synchronous. An async adapter should use `asyncio.to_thread` rather
   than blocking its event loop. No conversion of the entire client to async is
   necessary; connections are created inside each operation.
3. Add caller authentication, explicit account scope checks on every search and
   ID lookup, response-size budgets and rate limits before remote exposure.
   Current filters are query options, **not authorization**. Read-only means no
   mutations, not permission to disclose all accounts. Tailscale access does not
   replace application authorization; consult the
   [MCP security guidance](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices).
4. Choose transport/bind address/port through deployment configuration. Keep
   stdout reserved for protocol messages when using stdio. Add adapter tests for
   schemas, serialization, authentication, scope and error mapping.
5. Treat all email/HTML/attachment content as untrusted data, never instructions
   to the agent. Decide explicitly what may be sent to an external AI provider.
   Summaries and action classification belong above this retrieval layer.
6. Later downloads and write tools require separate grants and services. Sending,
   deleting, moving, archiving and marking read must not become available through
   a read grant. Require confirmation for appropriate write actions. Do not rely
   on tool annotations alone to enforce access control.

Attachment metadata is ready; remote attachment retrieval, content-size limits,
safe resource delivery, a background server process, AI summaries and the actual
MCP transport/SDK integration are deliberately not implemented here.
