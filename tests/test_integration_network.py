"""실제 유튜브·ffmpeg를 쓰는 통합 테스트. 실행: pytest -m network -v"""
from datetime import datetime, timezone

import pytest

from stampcut.core import ffmpeg as ff
from stampcut.core.downloader import Downloader
from stampcut.core.models import Clip, Settings, VideoInfo
from stampcut.core.renderer import build_clip_command
from stampcut.core.settings import bundled_font_path

pytestmark = pytest.mark.network


@pytest.fixture
def paths():
    p = ff.find_ffmpeg()
    if p is None:
        pytest.skip("ffmpeg가 PATH에 없습니다")
    return p


def test_download_and_render_one_clip(tmp_path, paths):
    video = VideoInfo(
        index=0,
        video_id="POZWcyKFvjY",
        url="https://www.youtube.com/watch?v=POZWcyKFvjY",
        title="26.08.20 문성FC 3게임(vs 하리보)",
        short_name="3게임",
        channel_title="문성FC",
        published_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        duration=1449,
        comment_count=4,
    )
    clip = Clip(video=video, t=758, mentions=[], score=1.0, caption="원더골")
    s = Settings()
    Downloader(tmp_path / "cache", str(paths.ffmpeg)).download_final(clip, s)
    info = ff.probe(paths, clip.final_path)
    assert info.width > 0 and 17.0 <= info.duration <= 19.5

    cmd, out = build_clip_command(paths, clip, s, "테스트 하이라이트", info, tmp_path, 0, bundled_font_path())
    seen = []
    ff.run(cmd, on_progress=seen.append, total_seconds=clip.duration(s))
    result = ff.probe(paths, out)
    assert (result.width, result.height) == (1080, 1920)
    assert abs(result.duration - 18) < 0.5 and result.has_audio
    assert seen and seen[-1] >= 0.9
