"""Run one blocking operation and deliver its result on the GUI thread."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot


class _TaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object], parent: QObject) -> None:
        super().__init__(parent)
        self._operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation())
        except Exception as error:  # noqa: BLE001 - Deliver worker failures to the GUI.
            self.failed.emit(str(error) or type(error).__name__)


class TaskRunner(QObject):
    busyChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._result: object = None
        self._error: str | None = None
        self._success: Callable[[object], None] | None = None
        self._failure: Callable[[str], None] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(
        self,
        operation: Callable[[], object],
        success: Callable[[object], None],
        failure: Callable[[str], None],
    ) -> bool:
        if self.busy:
            return False
        self._success, self._failure = success, failure
        self._result, self._error = None, None
        thread = _TaskThread(operation, self)
        thread.succeeded.connect(self._receive_result)
        thread.failed.connect(self._receive_error)
        thread.finished.connect(self._finish)
        self._thread = thread
        self.busyChanged.emit(True)
        thread.start()
        return True

    @Slot(object)
    def _receive_result(self, result: object) -> None:
        self._result = result

    @Slot(str)
    def _receive_error(self, error: str) -> None:
        self._error = error

    @Slot()
    def _finish(self) -> None:
        success, failure = self._success, self._failure
        result, error = self._result, self._error
        thread = self._thread
        assert thread is not None
        # Qt's finished signal can precede thread-local cleanup; retain ownership.
        thread.wait()
        self._thread = None
        self._result, self._error = None, None
        self._success, self._failure = None, None
        thread.deleteLater()
        self.busyChanged.emit(False)
        assert success is not None and failure is not None
        if error is None:
            success(result)
        else:
            failure(error)
