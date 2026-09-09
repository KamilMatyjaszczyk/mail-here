"""Local deployment configuration shared by GUI and future adapters."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def default_database_path(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    explicit = values.get("MAILKLIENT_DATABASE_PATH")
    if explicit:
        path = Path(explicit).expanduser()
    else:
        data_home = values.get("XDG_DATA_HOME")
        base = (
            Path(data_home).expanduser() if data_home else Path.home() / ".local/share"
        )
        path = base / "mailklient/mailklient.sqlite3"
    if not path.is_absolute():
        raise ValueError("Mail cache configuration must use an absolute path.")
    return path
