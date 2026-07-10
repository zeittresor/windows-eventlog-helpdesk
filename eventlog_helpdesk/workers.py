from __future__ import annotations

import traceback
from threading import Event
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    status = pyqtSignal(str)
    chunk = pyqtSignal(str)
    finished = pyqtSignal()


class TaskWorker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.cancel_event = Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.function(
                *self.args,
                **self.kwargs,
                cancel_event=self.cancel_event,
                progress_callback=self.signals.progress.emit,
                status_callback=self.signals.status.emit,
                chunk_callback=self.signals.chunk.emit,
            )
        except Exception as exc:  # noqa: BLE001 - worker must report all failures
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.signals.error.emit(detail)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
