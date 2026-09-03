"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mailklient.services import MailStore, seed_demo_data
from mailklient.ui.main_window import MainWindow


def main() -> int:
    """Start the Qt application."""
    app = QApplication(sys.argv)

    store = MailStore(default_database_path())
    seed_demo_data(store)

    window = MainWindow(store)
    window.show()

    return app.exec()


def default_database_path() -> Path:
    """Return the default local cache path for the application."""
    data_home = os.environ.get("XDG_DATA_HOME")
    base_dir = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base_dir / "mailklient" / "mailklient.sqlite3"


if __name__ == "__main__":
    raise SystemExit(main())
