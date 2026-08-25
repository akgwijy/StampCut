from PySide6.QtWidgets import QDialog, QMessageBox

from stampcut.core.models import Settings
from stampcut.gui.settings_dialog import SettingsDialog, ytdlp_version


def test_dialog_roundtrip(qtbot):
    s = Settings(pre_seconds=7, api_key="K", output_dir="E:/out", ffmpeg_path="C:/ff")
    d = SettingsDialog(s)
    qtbot.addWidget(d)
    assert d.pre.value() == 7 and d.api_key.text() == "K" and d.output_dir.text() == "E:/out" and d.ffmpeg_path.text() == "C:/ff"
    d.post.setValue(20)
    d.show_time.setChecked(False)
    d.background.setText("#112233")
    d.parallel.setValue(3)
    d.cluster.setValue(8.5)
    r = d.result_settings()
    assert (r.post_seconds, r.show_time_in_caption, r.background_color, r.parallel_downloads) == (20, False, "#112233", 3)
    assert (r.pre_seconds, r.api_key, r.cluster_window_seconds, r.output_dir) == (7, "K", 8.5, "E:/out")


def test_dialog_rejects_bad_color(qtbot, monkeypatch):
    d = SettingsDialog(Settings())
    qtbot.addWidget(d)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(1))
    d.background.setText("red")
    d.accept()
    assert warned and d.result() != QDialog.Accepted
    d.background.setText("#000000")
    d.accept()
    assert d.result() == QDialog.Accepted


def test_ytdlp_version_is_string():
    assert isinstance(ytdlp_version(), str) and ytdlp_version()
