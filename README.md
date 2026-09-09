# mcpMail

A desktop email client for Linux that brings multiple accounts together in one
unified inbox. Built in Python with PySide6/Qt, IMAP/SMTP and SQLite as a local cache.

**Status: alpha, under active development.** The client is intended for personal
use and has not completed a security audit. Gmail and personal Outlook/Hotmail
accounts are the main focus.

**Before you begin:** You must register your own OAuth app for Gmail or Outlook
and provide your own client ID, whether you install from source or use the RPM.
The maintainer's client ID and client secret are not bundled with mcpMail.
See the [OAuth guide](mailklient/docs/oauth.md).

## Features

- Multiple accounts with a unified inbox and clear account labels for each message.
- Three columns that can be rearranged by dragging, with theme-aware Qt controls.
- Manual and automatic background sync, progress indicators and cancellation.
- Search across locally stored messages, unread filtering, sorting and fetching older email.
- Sending, replying and forwarding. Replies automatically use the account that received the message.
- CC/BCC, HTML messages, outgoing attachments and locally saved drafts.
- HTML reading, control over external content and links that open in the default browser.
- Attachment details, previews for supported types, and file opening and saving.
- Archiving, moving, moving to trash and changing read/unread status.

The folder view is deliberately simple: **Inbox, Trash and Spam**. Locally cached
messages can be read offline; sending and further syncing require a connection.

### Accounts

| Account type | Status |
| --- | --- |
| Gmail | OAuth setup is available. Requires your own Google app registration. |
| Personal Outlook / Hotmail | OAuth setup is available. Requires your own Microsoft app registration. |
| Other IMAP/SMTP accounts | Manual server settings and passwords are supported where the provider allows them. |
| Work and student accounts | No general guarantee of support. Organization access policies may prevent sign-in. |
| Tuta via TutaBridge | Experimental code is retained, but development is paused. Not part of the recommended setup. |

## Installation

### Fedora 44: RPM

Use the prebuilt `.noarch.rpm` file from
[Releases](https://github.com/KamilMatyjaszczyk/mail-here/releases) once a release
has been published:

```bash
sudo dnf install ./mcpmail-0.1.0-1.fc44.noarch.rpm
mcpMail
```

This installs the app, dependencies and an application menu entry without a venv.
The [RPM guide](mailklient/docs/rpm.md) also explains how to build the package directly from
a GitHub clone. You still need a keyring and your own OAuth setup.
The remaining installation commands below apply to installation from source.

### From source

You need:

- Python **3.11 or newer**, `pip` and virtual environment support.
- A Linux desktop with a browser and the required Qt libraries for Wayland/X11.
- A working, unlocked keyring, such as GNOME Keyring or KWallet with Secret Service.

Clone or download the repository. Then run from the repository root:

```bash
cd mailklient
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/mcpMail
```

Run the remaining commands in this guide from the `mailklient/` project directory,
where `pyproject.toml` is located. You do not need to activate the environment when
using `.venv/bin/` explicitly. Run the app as a regular user in a desktop session.

### Your first account

For **Gmail**, you need your own desktop app's **client ID and client secret**.
For **personal Outlook / Hotmail**, use your own **Application (client) ID** and
leave the client secret empty for the public desktop app. A client ID identifies
your app registration; it is not your email password. Save these values through
the app's settings dialog, not in the source code or Git.

1. Follow the [Gmail or Outlook setup](mailklient/docs/oauth.md) to register your own OAuth app.
2. Open **Account > OAuth app settings** and save the provider's values in the keyring.
3. Select **Account > Add account**, enter the account details and use OAuth2 for Gmail/Outlook.
4. Complete sign-in in the browser. Use **Account > Sign in with OAuth** when needed.
5. Test the connection with **Test IMAP** and **Test SMTP**, then select **Sync account**.

When **All inboxes** is selected, sync can run for all configured accounts.
The first sync is limited; select an account and use **Fetch older messages** for
more history. Connection tests do not send any messages.

### Application menu and demo

The RPM installation already includes an application menu entry. The following
applies to a venv installation:

Add a shortcut with **Account > Add to application menu**, or run:

```bash
.venv/bin/mcpMail-launcher
```

The shortcut points to this virtual environment. Keep the project directory in
the same location, or recreate the shortcut after moving it.

To view the interface without adding a real account, use a separate temporary cache:

```bash
MAILKLIENT_DATABASE_PATH="$(mktemp -d)/mailklient.sqlite3" .venv/bin/mcpMail --demo
```

The demo uses sample messages. The temporary cache is separate from your regular
mail database; `--demo` on its own only adds sample data if the selected cache is empty.

## Data and privacy

The default data directory is `${XDG_DATA_HOME:-~/.local/share}/mailklient/`.

| Data | Storage |
| --- | --- |
| Account and message metadata, message content, drafts and attachment cache | `mailklient.sqlite3` in the data directory |
| Copies of attachments for local drafts | `draft-attachments/` next to the database |
| Passwords, OAuth app settings and tokens | System keyring, not SQLite |
| Window layout and GUI preferences | Qt `QSettings`, separate from the mail database |

Message content in SQLite is **not encrypted by the app**. The keyring does not
protect the email cache itself. Keep this in mind when making backups and managing
access to your machine. Do not put local data, OAuth client files or secrets in Git.

External content is blocked by default. If you allow it, requests to the sender's
servers may also load tracking pixels. Attachments are untrusted content; a
preview or a familiar file type does not guarantee that a file is safe.

Attachments can be fetched on demand. The attachment cache for re-downloadable
content has a cleanup budget of 256 MiB and can be cleared via
**Account > Clear attachment cache**. Drafts and local sent copies are excluded from this
cleanup; files you have saved yourself are not affected.

### Configuration

| Environment variable | Purpose |
| --- | --- |
| `MAILKLIENT_DATABASE_PATH` | Absolute path to a different SQLite cache. Draft attachments are stored next to the database. |
| `XDG_DATA_HOME` | Default location for local app data when no custom database path is set. |
| `MAILKLIENT_GMAIL_CLIENT_ID` | Override the Gmail client ID. |
| `MAILKLIENT_GMAIL_CLIENT_SECRET` | Override the Gmail client secret. |
| `MAILKLIENT_OUTLOOK_CLIENT_ID` | Override the Outlook client ID. |

Prefer the OAuth settings dialog. Environment variables take precedence over the
keyring, and the app **does not read `.env` automatically**. See the
[OAuth guide](mailklient/docs/oauth.md) for details and troubleshooting.

## Updating and uninstalling

For RPM installations, use DNF as described in the [RPM guide](mailklient/docs/rpm.md).
For source installations:

When first upgrading from the old package name **Mailklient**, remove the old
Python distribution before installing the new one in the same venv:

```bash
.venv/bin/python -m pip uninstall mailklient
.venv/bin/python -m pip install .
.venv/bin/mcpMail-launcher
```

The rename does not change the data directory, keyring, `MAILKLIENT_*` environment
variables or saved window layout. The Python module and project directory are
still named `mailklient`. The old terminal commands remain as compatibility aliases.

Close the app, get the desired version from GitHub, and run the installation again:

```bash
.venv/bin/python -m pip install --upgrade .
```

First, back up the **entire data directory with the app closed**, especially local
drafts and draft attachments. Database migrations run at startup. Keyring and
GUI preferences are stored separately and are not included in a data directory backup.

To remove the shortcut and the program:

```bash
.venv/bin/mcpMail-launcher --remove
.venv/bin/python -m pip uninstall mcpmail
```

This preserves local data, GUI preferences and the contents of the keyring.

## Development and testing

Install the project in editable mode to use the source code directly:

```bash
.venv/bin/python -m pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m build
```

The tests use temporary databases, synthetic messages and local loopback servers
for protocol tests, not real accounts. The environment must allow local socket
connections. GUI tests can run without a visible window using `offscreen`.

### Architecture and MCP

```text
src/mailklient/
  ui/        Qt windows and presentation
  domain/    Models, search filters and domain errors
  services/  Reading, syncing, sending, drafts and attachments
  database/  SQLite, repositories and migrations
  mail/      IMAP/SMTP and message parsing
  security/  Keyring, OAuth, TLS and safe file handling
  workers/   Background jobs for the GUI
```

The GUI uses services; network operations run outside the GUI thread. A separate
`MailReadService` provides structured results for recent and unread messages,
combinable search filters, message details, local threads and attachment metadata.

This prepares for a future AI/MCP adapter. **There is no MCP server or active
AI integration yet.** The read layer performs no sync or write operations.
See the [MCP architecture](mailklient/docs/MCP_ARCHITECTURE.md) for the API, security boundaries
and remaining work before an adapter can be exposed.

## Known limitations

- The local cache is not necessarily a complete copy of the server mailbox.
- Drafts are stored locally, not in the provider's drafts folder.
- Old messages without stored thread references cannot be grouped reliably.
- Large inboxes, network interruptions and prolonged use need further stability testing.
- The HTML viewer may not render every element the same way as webmail.
- Automatic backups, a complete MCP server and Flatpak/AppImage distribution are not implemented.
- The project does not yet have a chosen license. RPM packages are unsigned.
- CI builds and tests the Fedora 44 package; this workflow does not cover other Linux versions.

## Troubleshooting

For sign-in problems, start with the [OAuth guide](mailklient/docs/oauth.md), check the keyring
and try **Test IMAP** / **Test SMTP** separately.

For freezes or crashes, start the app from a terminal:

```bash
PYTHONFAULTHANDLER=1 .venv/bin/mcpMail
```

When reporting a bug, include your Linux distribution, Python version, provider,
last status message and brief reproduction steps. Check terminal output and
screenshots before sharing them. Remove tokens, secrets, addresses and private
email content. The app's operation logging is not a complete crash-reporting system.
