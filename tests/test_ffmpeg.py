import json
import sys
import threading
import time
from pathlib import Path

import pytest

from stampcut.core import ffmpeg as ff

_REAL_MISSING_FILTERS = ff.missing_filters  # conftest의 autouse 픽스처가 바꿔치기하기 전 원본


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def test_find_prefers_app_dir_then_configured_then_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
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
    monkeypatch.setattr(sys, "platform", "win32")
    _touch(tmp_path / "ffmpeg.exe")
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    assert ff.find_ffmpeg(str(tmp_path), None) is None


def test_exe_name_per_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert ff.exe_name("ffmpeg") == "ffmpeg.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ff.exe_name("ffprobe") == "ffprobe"
    monkeypatch.setattr(sys, "platform", "linux")
    assert ff.exe_name("ffmpeg") == "ffmpeg"


def test_find_on_macos_uses_plain_names_and_fallback_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    app, cfg, on_path, brew = (tmp_path / n for n in ("app", "cfg", "path", "brew"))
    for d in (app / "bin", cfg, on_path, brew):
        _touch(d / "ffmpeg")
        _touch(d / "ffprobe")
    monkeypatch.setattr(ff, "fallback_dirs", lambda: [tmp_path / "nope", brew])
    monkeypatch.setattr(ff.shutil, "which", lambda name: str(on_path / "ffmpeg"))
    assert ff.find_ffmpeg(str(cfg), app).ffmpeg == app / "bin" / "ffmpeg"
    assert ff.find_ffmpeg(str(cfg), None).ffmpeg == cfg / "ffmpeg"
    assert ff.find_ffmpeg(str(cfg / "ffmpeg"), None).ffprobe == cfg / "ffprobe"
    assert ff.find_ffmpeg("", None).ffmpeg == on_path / "ffmpeg"  # PATH가 기본 설치 폴더보다 먼저
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    assert ff.find_ffmpeg("", None).ffmpeg == brew / "ffmpeg"  # Finder로 실행돼 PATH에 Homebrew가 없을 때


def test_fallback_dirs_per_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    dirs = ff.fallback_dirs()
    assert Path("/opt/homebrew/bin") in dirs and Path("/usr/local/bin") in dirs
    # keg-only ffmpeg-full(drawtext 포함)이 슬림 ffmpeg가 있는 /opt/homebrew/bin보다 먼저
    assert dirs.index(Path("/opt/homebrew/opt/ffmpeg-full/bin")) < dirs.index(Path("/opt/homebrew/bin"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert Path("/usr/bin") in ff.fallback_dirs()
    monkeypatch.setattr(sys, "platform", "win32")
    assert ff.fallback_dirs() == []


@pytest.mark.parametrize(
    "sys_platform, hint",
    [("win32", "winget install Gyan.FFmpeg"), ("darwin", "brew install ffmpeg-full"), ("linux", "sudo apt install ffmpeg")],
)
def test_install_hint_per_platform(monkeypatch, sys_platform, hint):
    monkeypatch.setattr(sys, "platform", sys_platform)
    assert ff.install_hint() == hint


def test_find_prefers_candidate_with_required_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    slim, full = tmp_path / "slim", tmp_path / "full"
    for d in (slim, full):
        _touch(d / "ffmpeg")
        _touch(d / "ffprobe")
    monkeypatch.setattr(ff.shutil, "which", lambda name: str(slim / "ffmpeg"))
    monkeypatch.setattr(ff, "fallback_dirs", lambda: [full])
    monkeypatch.setattr(ff, "missing_filters", lambda p: () if p == full / "ffmpeg" else ("drawtext",))
    assert ff.find_ffmpeg("", None).ffmpeg == full / "ffmpeg"  # PATH의 슬림 ffmpeg보다 drawtext 있는 쪽
    monkeypatch.setattr(ff, "missing_filters", lambda p: ("drawtext",))
    assert ff.find_ffmpeg("", None).ffmpeg == slim / "ffmpeg"  # 아무 데도 없으면 첫 후보 (호출자가 안내)


def test_missing_filters_parses_filter_list(monkeypatch):
    class Result:  # FFmpeg 8 이하 형식: 플래그 3글자
        stdout = "Filters:\n  T.. = Timeline support\n  ..C = Command support\n ... drawbox           V->V       Draw a colored box on the input video.\n"

    monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k: Result())
    real = _REAL_MISSING_FILTERS.__wrapped__  # lru_cache 우회
    assert real(Path("/x/ffmpeg")) == ("drawtext",)
    Result.stdout += " ... drawtext          V->V       Draw text on top of video frames using libfreetype library.\n"
    assert real(Path("/x/ffmpeg")) == ()
    # FFmpeg 9 형식: 플래그 2글자
    Result.stdout = "Filters:\n  T. = Timeline support\n  .S = Slice threading\n .. anull             A->A       Pass the source unchanged to the output.\n"
    assert real(Path("/x/ffmpeg")) == ("drawtext",)
    Result.stdout += " T. drawtext          V->V       Draw text on top of video frames using libfreetype library.\n"
    assert real(Path("/x/ffmpeg")) == ()


def test_missing_filters_when_ffmpeg_cannot_run(tmp_path):
    assert _REAL_MISSING_FILTERS.__wrapped__(tmp_path / "nope") == ("drawtext",)


def test_parse_progress_line():
    assert ff.parse_progress_line("out_time_ms=1500000\n") == 1.5
    assert ff.parse_progress_line("out_time_us=1500000") == 1.5
    assert ff.parse_progress_line("frame=10") is None


def _probe_json(rotation=None, audio=True, tags=None):
    v = {"codec_type": "video", "width": 1920, "height": 1080}
    if rotation is not None:
        v["side_data_list"] = [{"rotation": rotation}]
    if tags is not None:
        v["tags"] = tags
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


def test_run_cancel_while_stalled():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(ff.Cancelled):
        ff.run([sys.executable, "-c", "import time; time.sleep(10)"], cancel=cancel)
    assert time.monotonic() - t0 < 5


def test_run_error_tail_keeps_last_30_lines():
    code = (
        "import sys\n"
        "for i in range(40):\n"
        "    sys.stderr.write('line %d\\n' % i)\n"
        "sys.exit(1)\n"
    )
    with pytest.raises(ff.FfmpegError) as ei:
        ff.run([sys.executable, "-c", code])
    msg = str(ei.value)
    assert "line 39" in msg
    assert "line 5" not in msg


def test_parse_probe_json_prefers_side_data_rotation_over_tags():
    info = ff.parse_probe_json(_probe_json(rotation=90, tags={"rotate": "0"}))
    assert (info.width, info.height) == (1080, 1920)


def test_parse_probe_json_falls_back_to_tags_rotate():
    info = ff.parse_probe_json(_probe_json(tags={"rotate": "90"}))
    assert (info.width, info.height) == (1080, 1920)


def test_parse_probe_json_tags_rotate_180():
    info = ff.parse_probe_json(_probe_json(tags={"rotate": "180"}))
    assert (info.width, info.height) == (1920, 1080)
