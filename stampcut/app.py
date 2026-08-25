"""앱 진입점."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from stampcut import __version__
from stampcut.core import settings as settings_mod


def setup_logging() -> None:
    d = settings_mod.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(d / "stampcut.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
        force=True,
    )


def _log_uncaught(exc_type, exc, tb) -> None:
    logging.getLogger("stampcut").critical("처리되지 않은 예외", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    sys.excepthook = _log_uncaught
    logging.getLogger(__name__).info("StampCut %s 시작", __version__)
    from PySide6.QtWidgets import QApplication

    from stampcut.gui.main_window import MainWindow

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("StampCut")
    win = MainWindow()
    win.show()
    return app.exec()
