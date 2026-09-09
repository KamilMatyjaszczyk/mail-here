from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from mailklient.services import ComposeDraft, CORE_SYNC_FOLDER_NAMES, HeaderSyncResult
from mailklient.workers import MailSendWorker, MailSyncWorker


def _get_qapplication() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeMailSyncService:
    def __init__(self) -> None:
        self.calls: list[
            tuple[int, int, tuple[str, ...] | None, tuple[str, ...] | None]
        ] = []

    def fetch_imap_headers(
        self,
        account_id: int,
        *,
        limit_per_folder: int,
        folder_names: tuple[str, ...] | None,
        message_folder_names: tuple[str, ...] | None,
        progress=None,
    ) -> HeaderSyncResult:
        self.calls.append(
            (account_id, limit_per_folder, folder_names, message_folder_names)
        )
        return HeaderSyncResult(folders_seen=2, messages_seen=3)


class FailingMailSyncService:
    def fetch_imap_headers(self, *_args, **_kwargs) -> HeaderSyncResult:
        raise RuntimeError("Noe gikk galt")


class FakeMailSendService:
    def __init__(self) -> None:
        self.sent_drafts: list[ComposeDraft] = []

    def send_draft(self, draft: ComposeDraft) -> bool:
        self.sent_drafts.append(draft)
        return True


class FailingMailSendService:
    def send_draft(self, _draft: ComposeDraft) -> bool:
        raise RuntimeError("Sending gikk galt")


def test_mail_sync_worker_emits_result() -> None:
    _get_qapplication()
    service = FakeMailSyncService()
    worker = MailSyncWorker(service, 7, limit_per_folder=10)
    results: list[tuple[int, HeaderSyncResult]] = []
    done: list[bool] = []

    worker.synced.connect(
        lambda account_id, result: results.append((account_id, result))
    )
    worker.done.connect(lambda: done.append(True))

    worker.run()

    assert service.calls == [(7, 10, None, CORE_SYNC_FOLDER_NAMES)]
    assert results == [(7, HeaderSyncResult(folders_seen=2, messages_seen=3))]
    assert done == [True]


def test_mail_sync_worker_emits_failure() -> None:
    _get_qapplication()
    worker = MailSyncWorker(FailingMailSyncService(), 7)
    failures: list[tuple[int, str]] = []
    done: list[bool] = []

    worker.failed.connect(lambda account_id, error: failures.append((account_id, error)))
    worker.done.connect(lambda: done.append(True))

    worker.run()

    assert failures == [(7, "Noe gikk galt")]
    assert done == [True]


def test_mail_send_worker_emits_result() -> None:
    _get_qapplication()
    service = FakeMailSendService()
    draft = ComposeDraft(account_id=7, recipients="friend@example.com")
    worker = MailSendWorker(service, draft)
    results: list[bool] = []
    done: list[bool] = []

    worker.finished.connect(results.append)
    worker.done.connect(lambda: done.append(True))

    worker.run()

    assert service.sent_drafts == [draft]
    assert results == [True]
    assert done == [True]


def test_mail_send_worker_emits_failure() -> None:
    _get_qapplication()
    draft = ComposeDraft(account_id=7, recipients="friend@example.com")
    worker = MailSendWorker(FailingMailSendService(), draft)
    failures: list[str] = []
    done: list[bool] = []

    worker.failed.connect(failures.append)
    worker.done.connect(lambda: done.append(True))

    worker.run()

    assert failures == ["Sending gikk galt"]
    assert done == [True]
