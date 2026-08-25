from datetime import datetime, timezone

import pytest

from stampcut.core.models import Clip, VideoInfo


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
