"""Background bridge startup and health checks."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from mailklient.services.bridge_runtime import BridgeRuntimeService, BridgeStartError


class BridgeWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        service: BridgeRuntimeService,
        certificate_path: Path | None,
        *,
        start: bool = False,
    ) -> None:
        super().__init__()
        self._service = service
        self._certificate_path = certificate_path
        self._start = start

    @Slot()
    def run(self) -> None:
        try:
            operation = (
                self._service.ensure_started if self._start else self._service.check
            )
            self.finished.emit(operation(self._certificate_path))
        except BridgeStartError as error:
            self.failed.emit(str(error))
        except Exception:  # noqa: BLE001 - Keep unexpected details out of the GUI.
            self.failed.emit("Could not check TutaBridge. Check the bridge window.")
        finally:
            self.done.emit()
