"""Local composer persistence. SMTP retries always require user action."""

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from mailklient.database import connect
from mailklient.security.attachment_files import private_directory, safe_filename
from mailklient.services.mail_send import ComposeDraft
from mailklient.services.mail_store import MailStore


@dataclass(frozen=True)
class SavedDraft:
    id: int
    draft: ComposeDraft
    state: str


class DraftService:
    def __init__(self, store: MailStore) -> None:
        self._path = store.database_path
        self._attachments_root = self._path.parent / "draft-attachments"

    def save(self, draft: ComposeDraft, draft_id: int | None = None) -> int:
        created: list[Path] = []
        old_paths: tuple[str, ...] = ()
        try:
            with connect(self._path) as connection:
                if draft_id is None:
                    cursor = connection.execute(
                        "INSERT INTO drafts(account_id, payload) VALUES (?, ?)",
                        (draft.account_id, "{}"),
                    )
                    assert cursor.lastrowid is not None
                    draft_id = cursor.lastrowid
                else:
                    row = connection.execute(
                        "SELECT payload FROM drafts WHERE id = ?", (draft_id,)
                    ).fetchone()
                    if row is None:
                        raise ValueError("The draft no longer exists.")
                    old_paths = tuple(json.loads(row["payload"])["attachment_paths"])
                paths = []
                if draft.attachment_paths:
                    root = private_directory(self._attachments_root)
                    directory = private_directory(root / str(draft_id))
                    for source_name in draft.attachment_paths:
                        source = Path(source_name)
                        if not source.is_file():
                            raise ValueError(f"Attachment missing: {source.name}")
                        if source.resolve().is_relative_to(directory.resolve()):
                            paths.append(str(source))
                            continue
                        destination_dir = Path(tempfile.mkdtemp(dir=directory))
                        created.append(destination_dir)
                        destination = destination_dir / safe_filename(source.name)
                        fd = os.open(
                            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                        )
                        with (
                            os.fdopen(fd, "wb") as output,
                            source.open("rb") as input_file,
                        ):
                            shutil.copyfileobj(input_file, output)
                        paths.append(str(destination))
                saved = replace(draft, attachment_paths=tuple(paths))
                connection.execute(
                    "UPDATE drafts SET account_id = ?, payload = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (saved.account_id, json.dumps(asdict(saved)), draft_id),
                )
        except Exception:
            for directory in created:
                shutil.rmtree(directory)
            raise
        for path in set(old_paths) - set(paths):
            self._remove_snapshot(Path(path), draft_id)
        return draft_id

    def get(self, draft_id: int) -> SavedDraft:
        with connect(self._path) as connection:
            row = connection.execute(
                "SELECT id, payload, state FROM drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            if row is None:
                raise ValueError("The draft no longer exists.")
            data = json.loads(row["payload"])
            data["attachment_paths"] = tuple(data["attachment_paths"])
            return SavedDraft(row["id"], ComposeDraft(**data), row["state"])

    def _remove_snapshot(self, path: Path, draft_id: int) -> None:
        root = self._attachments_root / str(draft_id)
        if path.parent.parent == root and not root.is_symlink():
            shutil.rmtree(path.parent, ignore_errors=True)

    def list_drafts(self) -> list[SavedDraft]:
        with connect(self._path) as connection:
            rows = connection.execute(
                "SELECT id, payload, state FROM drafts WHERE state != 'sent' ORDER BY updated_at DESC, id DESC"
            )
            result = []
            for row in rows:
                data = json.loads(row["payload"])
                data["attachment_paths"] = tuple(data["attachment_paths"])
                result.append(SavedDraft(row["id"], ComposeDraft(**data), row["state"]))
            return result

    def set_state(self, draft_id: int, state: str) -> None:
        with connect(self._path) as connection:
            connection.execute(
                "UPDATE drafts SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (state, draft_id),
            )

    def delete(self, draft_id: int) -> None:
        draft = self.get(draft_id)
        with connect(self._path) as connection:
            connection.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        for path in draft.draft.attachment_paths:
            self._remove_snapshot(Path(path), draft_id)
