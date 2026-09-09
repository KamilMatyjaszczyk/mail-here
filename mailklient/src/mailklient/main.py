"""Application entry point."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication

from mailklient.config import default_database_path
from mailklient.services import MailStore, seed_demo_data
from mailklient.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    """Start the Qt application."""
    parser = argparse.ArgumentParser(description="mcpMail for Linux")
    parser.add_argument("--version", action="version", version=version("mcpMail"))
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Add local sample messages to an empty cache",
    )
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationDisplayName("mcpMail")
    # A source/venv install may not have a desktop entry for portal registration.
    if QStandardPaths.locate(
        QStandardPaths.StandardLocation.GenericDataLocation,
        "applications/mailklient.desktop",
    ):
        app.setDesktopFileName("mailklient")

    store = MailStore(default_database_path())
    if args.demo:
        seed_demo_data(store)

    window = MainWindow(store)
    window.show()
    QTimer.singleShot(0, window.initialize_bridge)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
