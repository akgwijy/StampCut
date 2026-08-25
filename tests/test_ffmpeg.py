import json
import sys
import threading

import pytest

from stampcut.core import ffmpeg as ff


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def test_find_prefers_app_dir_then_configured_then_path(tmp_path, monkeypatch):
    app = tmp_path / "app"
    cfg = tmp_path / "cfg"
    on_path = tmp_path / "path"
    for d in (app / "bin", cfg, on_path):
        _touch(d / "ffmpeg.exe")
        _touch(d / "ffprobe.exe")
    monkeypatch.setattr(ff.shutil, "which", lambda name: str(on_path / "ffmpeg.exe"))
    assert ff.find_ffmpeg(str(cfg), app).ffmpeg == app / "bin" / "ffmpeg.exe"
    assert ff.find_ffmpeg(str(cfg), tmp_path / "nope").ffmpeg == cfg / "ffmpeg.exe"
    assert ff.find_ffmpeg(str(cfg / "ffmpeg.exe"), None).ffprobe == cfg / "ffprobe.exe"
    assert ff.find_ffmpeg("", None).ffmpeg == on_path / "ffmpeg.exe"


def test_find_requires_ffprobe_next_to_ffmpeg(tmp_path, monkeypatch):
    _touch(tmp_path / "ffmpeg.exe")
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    assert ff.find_ffmpeg(str(tmp_path), None) is None


def test_parse_progress_line():
    assert ff.parse_progress_line("out_time_ms=1500000\n") == 1.5
    assert ff.parse_progress_line("out_time_us=1500000") == 1.5
    assert ff.parse_progress_line("frame=10") is None


def _probe_json(rotation=None, audio=True):
    v = {"codec_type": "video", "width": 1920, "height": 1080}
    if rotation is not None:
        v["side_data_list"] = [{"rotation": rotation}]
    streams = [v] + ([{"codec_type": "audio"}] if audio else [])
    return json.dumps({"streams": streams, "format": {"duration": "25.04"}})


def test_parse_probe_json():
    info = ff.parse_probe_json(_probe_json())
    assert (info.width, info.height, info.has_audio) == (1920, 1080, True)
    assert abs(info.duration - 25.04) < 1e-6
    rotated = ff.parse_probe_json(_probe_json(rotation=-90))
    assert (rotated.width, rotated.height) == (1080, 1920)
    assert ff.parse_probe_json(_probe_json(audio=False)).has_audio is False
    with pytest.raises(ff.FfmpegError):
        ff.parse_probe_json(json.dumps({"streams": [], "format": {}}))


def test_run_reports_progress():
    seen = []
    ff.run([sys.executable, "-c", "print('out_time_ms=500000');print('out_time_ms=1000000')"], on_progress=seen.append, total_seconds=1.0)
    assert seen == [0.5, 1.0]


def test_run_raises_with_stderr_tail():
    with pytest.raises(ff.FfmpegError) as ei:
        ff.run([sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(1)"])
    assert "boom" in str(ei.value)


def test_run_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ff.Cancelled):
        ff.run([sys.executable, "-c", "print('out_time_ms=1')"], cancel=cancel)
