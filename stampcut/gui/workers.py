"""core 작업을 QThreadPool에서 실행하고 시그널로 알린다."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from stampcut.core.downloader import DownloadCancelled
from stampcut.core.ffmpeg import Cancelled

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class Worker(QRunnable):
    """fn(*args, progress=..., cancel=..., **kwargs)를 백그라운드에서 실행한다."""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()
        self.cancel = threading.Event()
        self.done = False
        self.setAutoDelete(False)  # MainWindow가 목록으로 관리한다

    def _progress(self, stage: str, done: int, total: int, message: str) -> None:
        self.signals.progress.emit(stage, done, total, message)

    def run(self) -> None:
        try:
            result = self.fn(*self.args, progress=self._progress, cancel=self.cancel, **self.kwargs)
        except (Cancelled, DownloadCancelled):
            self.done = True
            self.signals.cancelled.emit()
        except Exception as e:  # noqa: BLE001 — 모든 실패를 GUI로 전달
            log.exception("worker failed")
            self.done = True
            self.signals.failed.emit(str(e))
        else:
            self.done = True
            self.signals.finished.emit(result)
