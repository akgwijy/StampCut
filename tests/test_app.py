import logging
import logging.handlers
import sys

from stampcut import app


def test_setup_logging_creates_rotating_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = logging.getLogger()
    app.setup_logging()
    app.setup_logging()  # force=True → 두 번 불러도 핸들러가 중복되지 않는다
    files = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(files) == 1
    h = files[0]
    assert h.maxBytes == 1_000_000 and h.backupCount == 3
    assert h.baseFilename.endswith("stampcut.log") and "StampCut" in h.baseFilename
    logging.getLogger("stampcut.test").info("한글 로그")
    h.flush()
    assert "한글 로그" in (tmp_path / "StampCut" / "logs" / "stampcut.log").read_text("utf-8")


def test_log_uncaught_records_and_chains(monkeypatch, caplog):
    called = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: called.append(a))
    with caplog.at_level(logging.CRITICAL, logger="stampcut"):
        try:
            raise ValueError("boom")
        except ValueError:
            app._log_uncaught(*sys.exc_info())
    assert called and "boom" in caplog.text and "처리되지 않은 예외" in caplog.text
