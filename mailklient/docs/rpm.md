# mcpMail: Fedora RPM

The RPM build targets **Fedora 44** with the system Python 3.14 and Qt/PySide6.
It is not a universal RPM for all Linux distributions. `noarch` means the app's
code is architecture-independent, not that the Python or Fedora version does
not matter. Other distributions can use the source installation in the README.

## Install a prebuilt package

Download the file ending in **`.noarch.rpm`** from the project's
[GitHub Releases](https://github.com/KamilMatyjaszczyk/mail-here/releases)
once a release has been published. `.src.rpm` is the source package, not the app.
RPM files are not stored in Git; a regular clone contains the build configuration.

From the directory containing the RPM file, for version 1.0.0:

```bash
sudo dnf install ./mcpmail-1.0.0-1.fc44.noarch.rpm
mcpMail
```

DNF installs the dependencies. The app appears in the application menu with the
system email icon, without a venv, pip or a project directory. Run the app as a
regular user, never with `sudo`. You still need an unlocked keyring, a browser and
your own [OAuth app registration](oauth.md).

If you have an old venv shortcut, remove it with
`/usr/bin/mcpMail-launcher --remove`. Otherwise, it may override the new
system shortcut. This command does not remove accounts or email.

Install a newer RPM using the same `dnf install ./file.rpm` command. Back up
the data directory with the app closed first. `sudo dnf remove mcpmail` uninstalls
the program but preserves the cache, drafts, keyring and user settings.

The RPM replaces the old `mailklient` package during an upgrade. The app name is
`mcpMail`; the package name is `mcpmail`. Data paths, keyring, Qt settings and
the desktop ID `mailklient.desktop` are retained for compatibility.

## Build from the GitHub source

Run from the repository root, which contains the `mailklient/` subdirectory:

```bash
sudo dnf install podman python3
python3 mailklient/packaging/rpm/build.py --check-install
```

Do **not** run the build script with sudo. It uses a rootless Fedora 44 container.
The first build downloads Qt and build dependencies and requires network access,
time and a few GB of free disk space. The actual `rpmbuild` and pytest runs are
offline, with loopback available. No host user data or keyring is mounted.

`--check-install` also tests DNF installation in a fresh, empty container,
GUI/event loop startup without a visible window, and uninstallation. This extra
check needs network access to fetch RPM dependencies. Omit the flag for a faster
build; pytest still runs. Docker can be used with `--engine docker`.

The output is placed in `mailklient/dist/rpm/`:

- `mcpmail-1.0.0-1.fc44.noarch.rpm`: installable app.
- `mcpmail-1.0.0-1.fc44.src.rpm`: source code and RPM spec.
- `SHA256SUMS`: checksums for both files.

If both RPM files and the checksum file are in the same directory, run
`sha256sum --check SHA256SUMS` there. The packages are currently **unsigned**;
checksums do not replace signatures or a trusted download source.

The build includes only code, the SQL schema, documentation, synthetic tests and
packaging files. `.venv`, `.git`, `.env`, local databases and OAuth JSON files are
not included in the source archive. Do not hardcode secrets in source code.

## GitHub release

The **mcpMail Fedora RPM** workflow tests pull requests and can be started manually
under **Actions > mcpMail Fedora RPM > Run workflow** once it has been pushed to
the default branch. Successful builds provide a downloadable Actions artifact for 14 days.

For a release, update the version in both `pyproject.toml` and
`packaging/rpm/mcpmail.spec`, commit and push the changes, and create a matching
tag. For example, for version 1.0.0:

```bash
git tag v1.0.0
git push origin v1.0.0
```

A successful tag build creates a **draft GitHub Release** with RPM files and
checksums. Review the draft and publish it on GitHub to make the files available
to others. An existing tag/release is not overwritten automatically.
No DNF repository or automatic app updates are configured.

The project does not yet have a chosen license. `LicenseRef-Unspecified` and
`LICENSE-NOTICE` in the package document this without granting a license.
The project owner should choose a license for further distribution. This is not
an official Fedora package.
