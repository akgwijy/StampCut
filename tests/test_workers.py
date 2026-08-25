from PySide6.QtCore import QThreadPool

from stampcut.core.ffmpeg import Cancelled
from stampcut.gui.workers import Worker


def _run(qtbot, worker, signal):
    with qtbot.waitSignal(signal, timeout=5000) as blocker:
        QThreadPool.globalInstance().start(worker)
    return blocker.args


def test_worker_finished_with_result(qtbot):
    def fn(a, b, progress, cancel):
        progress("s", 1, 2, "m")
        return a + b

    w = Worker(fn, 1, b=2)
    seen = []
    w.signals.progress.connect(lambda *a: seen.append(a))
    assert _run(qtbot, w, w.signals.finished) == [3]
    assert seen == [("s", 1, 2, "m")]


def test_worker_failed(qtbot):
    def fn(progress, cancel):
        raise ValueError("bad")

    w = Worker(fn)
    assert _run(qtbot, w, w.signals.failed) == ["bad"]


def test_worker_cancelled(qtbot):
    def fn(progress, cancel):
        raise Cancelled()

    w = Worker(fn)
    _run(qtbot, w, w.signals.cancelled)
