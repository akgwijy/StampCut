from PySide6.QtWidgets import QDialog, QMessageBox

from stampcut.core.models import Settings
from stampcut.gui.settings_dialog import SettingsDialog, ytdlp_version


def test_dialog_roundtrip(qtbot):
    s = Settings(pre_seconds=7, api_key="K", output_dir="E:/out", ffmpeg_path="C:/ff")
    d = SettingsDialog(s)
    qtbot.addWidget(d)
    assert d.pre.value() == -7 and d.api_key.text() == "K" and d.output_dir.text() == "E:/out" and d.ffmpeg_path.text() == "C:/ff"
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


def test_ffmpeg_status_checks_app_dir(qtbot, tmp_path, monkeypatch):
    from stampcut.core.ffmpeg import exe_name
    from stampcut.gui import settings_dialog as sd

    (tmp_path / "bin").mkdir()
    ffmpeg = tmp_path / "bin" / exe_name("ffmpeg")  # 실행 중인 OS 기준 이름 (Windows만 .exe)
    ffmpeg.write_bytes(b"")
    (tmp_path / "bin" / exe_name("ffprobe")).write_bytes(b"")
    monkeypatch.setattr(sd.settings_mod, "app_dir", lambda: tmp_path)
    d = sd.SettingsDialog(Settings(ffmpeg_path=""))
    qtbot.addWidget(d)
    assert "감지됨" in d.ffmpeg_status.text() and str(ffmpeg) in d.ffmpeg_status.text()


def test_ffmpeg_status_hint_follows_platform(qtbot, monkeypatch):
    from stampcut.gui import settings_dialog as sd

    monkeypatch.setattr(sd, "find_ffmpeg", lambda *a: None)
    monkeypatch.setattr("sys.platform", "darwin")
    d = sd.SettingsDialog(Settings(ffmpeg_path=""))
    qtbot.addWidget(d)
    assert "brew install ffmpeg-full" in d.ffmpeg_status.text() and "winget" not in d.ffmpeg_status.text()
    assert "bin/ffmpeg " in d.ffmpeg_path.placeholderText() and "기본 설치 폴더" in d.ffmpeg_path.placeholderText()


def test_ffmpeg_status_warns_when_drawtext_missing(qtbot, monkeypatch):
    from pathlib import Path

    from stampcut.core.ffmpeg import FfmpegPaths
    from stampcut.gui import settings_dialog as sd

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(sd, "find_ffmpeg", lambda *a: FfmpegPaths(Path("/opt/homebrew/bin/ffmpeg"), Path("/opt/homebrew/bin/ffprobe")))
    monkeypatch.setattr(sd, "missing_filters", lambda p: ("drawtext",))
    d = sd.SettingsDialog(Settings(ffmpeg_path=""))
    qtbot.addWidget(d)
    text = d.ffmpeg_status.text()
    assert "감지됨" in text and "/opt/homebrew/bin/ffmpeg" in text and "drawtext" in text and "brew install ffmpeg-full" in text
    assert "#c06000" in d.ffmpeg_status.styleSheet()  # 빨강(없음)도 초록(정상)도 아닌 경고색


def test_cache_button_disabled_while_busy(qtbot):
    d = SettingsDialog(Settings(), busy=True)
    qtbot.addWidget(d)
    assert d.cache_btn.isEnabled() is False
    assert "캐시" in d.cache_btn.toolTip()
    idle = SettingsDialog(Settings())
    qtbot.addWidget(idle)
    assert idle.cache_btn.isEnabled() is True


def test_negative_default_start_offset_applies_to_clip(qtbot, tmp_path, make_video, make_clip):
    from stampcut.core import settings as settings_mod

    dialog = SettingsDialog(Settings())
    qtbot.addWidget(dialog)
    dialog.pre.setValue(-5)
    path = tmp_path / 'settings.json'
    settings_mod.save(dialog.result_settings(), path)
    settings = settings_mod.load(path)
    clip = make_clip(make_video(), t=60)
    assert clip.start(settings) == 55
    restored_dialog = SettingsDialog(settings)
    qtbot.addWidget(restored_dialog)
    assert restored_dialog.pre.value() == -5
