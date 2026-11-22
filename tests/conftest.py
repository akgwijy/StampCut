from datetime import datetime, timezone
from pathlib import Path

import pytest

from stampcut.core.models import Clip, VideoInfo


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    """Keep settings, cache, previews, and logs inside each test's temporary data."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


@pytest.fixture
def make_video():
    def _make(**overrides) -> VideoInfo:
        base = dict(
            index=0,
            video_id="POZWcyKFvjY",
            url="https://www.youtube.com/watch?v=POZWcyKFvjY",
            title="26.08.20 문성FC 3게임(vs 하리보)",
            short_name="3게임",
            channel_title="문성FC",
            published_at=datetime(2026, 8, 20, 13, 36, 52, tzinfo=timezone.utc),
            duration=1449,
            comment_count=4,
        )
        base.update(overrides)
        return VideoInfo(**base)

    return _make


@pytest.fixture
def make_clip():
    def _make(video: VideoInfo, t: int, **overrides) -> Clip:
        base = dict(video=video, t=t, mentions=[], score=1.0, caption="원더골")
        base.update(overrides)
        return Clip(**base)

    return _make


@pytest.fixture(autouse=True)
def _ffmpeg_filters_present(request, monkeypatch):
    """테스트의 가짜 ffmpeg 경로(빈 파일)를 '필수 필터 있음'으로 취급한다.

    실제 검사는 tests/test_ffmpeg.py가 원본(_REAL_MISSING_FILTERS)으로 직접 다루고,
    network 통합 테스트는 진짜 ffmpeg 중 drawtext가 있는 것을 골라야 하므로 건드리지 않는다.
    """
    if "network" in request.keywords:
        return
    monkeypatch.setattr("stampcut.core.ffmpeg.missing_filters", lambda ffmpeg: ())
    monkeypatch.setattr("stampcut.gui.settings_dialog.missing_filters", lambda ffmpeg: ())
