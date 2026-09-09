"""Check the installed RPM without real accounts or the source checkout."""

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

import mailklient
from mailklient import main
from mailklient.config import default_database_path
from mailklient.services import MailStore

assert Path(mailklient.__file__).is_relative_to("/usr/lib")
desktop = Path("/usr/share/applications/mailklient.desktop").read_text()
assert "Name=mcpMail\n" in desktop
assert "Exec=mcpMail\n" in desktop
assert ".venv" not in desktop


# Exercise the real entry point and event loop, with an empty isolated cache.
class TimedApplication(main.QApplication):
    def exec(self):
        assert self.applicationDisplayName() == "mcpMail"
        assert self.desktopFileName() == "mailklient"
        QTimer.singleShot(300, self.quit)
        return super().exec()


main.QApplication = TimedApplication
assert main.main([]) == 0
assert default_database_path().is_file()
assert not MailStore(default_database_path()).list_accounts()
print("Installed RPM: imports, schema, entry point and GUI event loop OK.")
