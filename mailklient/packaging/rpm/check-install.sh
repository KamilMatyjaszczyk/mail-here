#!/bin/bash
# Run only inside a disposable Fedora 44 container, not on the host.
set -euo pipefail

if [[ ! -f /run/.containerenv && ! -f /.dockerenv ]]; then
    echo "This install check must run inside a disposable container." >&2
    exit 1
fi

dnf -y --setopt=install_weak_deps=False install /artifacts/mcpmail-*.noarch.rpm
rpm -q mcpmail
rpm -V mcpmail
/usr/bin/mcpMail --version
/usr/bin/mailklient --version

# No host home, keyring or mailbox is mounted in this container.
export QT_QPA_PLATFORM=offscreen
export PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring
export XDG_DATA_HOME=/tmp/mailklient-smoke/data
export XDG_CONFIG_HOME=/tmp/mailklient-smoke/config
export MAILKLIENT_DATABASE_PATH=/tmp/mailklient-smoke/data/mailklient.sqlite3
python3 /checks/smoke_test.py

dnf -y remove mcpmail
test ! -f /usr/bin/mcpMail
test ! -f /usr/bin/mailklient
test ! -f /usr/share/applications/mailklient.desktop
test -f "$MAILKLIENT_DATABASE_PATH"
