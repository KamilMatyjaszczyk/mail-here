"""Install a per-user desktop launcher for the current Python environment."""

from __future__ import annotations

import argparse
import configparser
import io
import os
import sys
from pathlib import Path

from mailklient.security.attachment_files import save_attachment


def install_launcher(data_home: Path | None = None) -> Path:
    root = data_home or Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    )
    directory = root / "applications"
    directory.mkdir(parents=True, exist_ok=True)
    executable = str(Path(sys.executable).absolute())
    if any(character in executable for character in "\n\r\t\x00="):
        raise ValueError("Invalid Python path.")
    quoted = (
        executable.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("%", "%%")
    )
    # Desktop string escaping is decoded before Exec argument quoting.
    quoted = quoted.replace("\\", "\\\\")
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.optionxform = lambda optionstr: optionstr  # type: ignore[method-assign]
    desktop["Desktop Entry"] = {
        "Type": "Application",
        "Name": "mcpMail",
        "Exec": f'"{quoted}" -m mailklient.main',
        "Icon": "mail-message-new",
        "Terminal": "false",
        "Categories": "Network;Email;",
        "StartupNotify": "true",
    }
    output = io.StringIO()
    desktop.write(output, space_around_delimiters=False)
    destination = directory / "mailklient.desktop"
    save_attachment(destination, output.getvalue().encode("utf-8"))
    return destination


def remove_launcher() -> bool:
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    path = root / "applications/mailklient.desktop"
    if path.is_symlink():
        raise ValueError("The shortcut is a symbolic link and was not removed.")
    if not path.exists():
        return False
    config = configparser.ConfigParser(interpolation=None)
    config.read(path, encoding="utf-8")
    if not config.get("Desktop Entry", "Exec", fallback="").endswith(
        " -m mailklient.main"
    ):
        raise ValueError("The shortcut does not belong to this app.")
    path.unlink()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Application menu shortcut for mcpMail")
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the shortcut without deleting local data",
    )
    args = parser.parse_args(argv)
    if args.remove:
        print("Shortcut removed." if remove_launcher() else "No shortcut found.")
    else:
        print(install_launcher())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
