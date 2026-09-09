"""Private, disposable files for opening untrusted email attachments."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("Unsafe directory for local attachments.")
    path.chmod(0o700)
    return path


def safe_filename(filename: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", filename).strip().strip(".")
    return name.encode("utf-8")[:180].decode("utf-8", errors="ignore") or "attachment"


def save_attachment(path: Path, content: bytes, *, overwrite: bool = True) -> None:
    """Write privately and atomically; never follow a destination symlink."""
    fd, temporary = tempfile.mkstemp(prefix=".mailklient-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        if overwrite:
            os.replace(temporary_path, path)
        else:
            os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class AttachmentWorkspace:
    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="mailklient-attachments-")

    def write(self, filename: str, content: bytes) -> Path:
        directory = Path(tempfile.mkdtemp(dir=self._directory.name))
        path = directory / safe_filename(filename)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        return path

    def close(self) -> None:
        self._directory.cleanup()
