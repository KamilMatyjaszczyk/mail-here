# mcpMail

A desktop email client for Linux with multiple accounts, a unified inbox and a
local SQLite cache. Built in Python with PySide6/Qt, IMAP/SMTP and the system keyring.

**Status: alpha, under active development.** Gmail and personal Outlook/Hotmail
accounts are the main focus. Each user registers their own OAuth app; the project
does not include a shared client ID or credentials.

The Python project is located in [`mailklient/`](mailklient/).

- [Fedora 44: install or build an RPM](mailklient/docs/rpm.md)
- [Main guide: features, installation and usage](mailklient/README.md)
- [OAuth setup for Gmail and Outlook](mailklient/docs/oauth.md)
- [Architecture and preparation for MCP](mailklient/docs/MCP_ARCHITECTURE.md)

The client has a shared read-only service layer for the GUI and a future AI
adapter. No MCP server or AI integration is enabled.

## OAuth setup required

**Each user must register their own OAuth app before signing in to Gmail or
Outlook/Hotmail.** This applies to both RPM and source installations. The
maintainer's client ID and client secret are not bundled with mcpMail.

- **Gmail:** provide your own desktop app's client ID and client secret.
- **Personal Outlook / Hotmail:** provide your own Application (client) ID;
  leave the client secret empty for the public desktop app.

Enter these under **Account > OAuth app settings**. The app stores them in your
system keyring. A client ID identifies your app registration, not your email
password; you still sign in through the provider's browser page afterward.
Follow the [step-by-step OAuth guide](mailklient/docs/oauth.md) before adding an
account. Do not commit secrets, tokens or downloaded OAuth client files to Git.

## Fedora installation

Download a prebuilt `.noarch.rpm` from
[Releases](https://github.com/KamilMatyjaszczyk/mail-here/releases) once a release
has been published, and install it with `sudo dnf install ./filename.rpm`.
The RPM provides an application menu entry and system dependencies without a venv.

To build the package yourself from this repository root:

```bash
sudo dnf install podman python3
python3 mailklient/packaging/rpm/build.py --check-install
sudo dnf install ./mailklient/dist/rpm/mcpmail-0.1.0-1.fc44.noarch.rpm
mcpMail
```

The build runs in a container without installing build dependencies on your
machine. The GitHub workflow builds and tests the package and creates a draft
release for version tags; no RPM binaries are stored in Git.

The app was previously called Mailklient. The internal project directory and
existing data paths are retained; accounts and keyring do not need to be set up again.
