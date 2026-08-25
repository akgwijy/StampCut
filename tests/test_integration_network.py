"""실제 유튜브·ffmpeg를 쓰는 통합 테스트. 실행: pytest -m network -v"""
from datetime import datetime, timezone

import pytest

from stampcut.core import ffmpeg as ff
from stampcut.core import pipeline
from stampcut.core.downloader import Downloader
from stampcut.core.models import Clip, Project, Settings, VideoInfo
from stampcut.core.renderer import build_clip_command
from stampcut.core.settings import bundled_font_path

pytestmark = pytest.mark.network


@pytest.fixture
def paths():
    p = ff.find_ffmpeg()
    if p is None:
        pytest.skip("ffmpeg가 PATH에 없습니다")
    return p


def _video() -> VideoInfo:
    return VideoInfo(
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


def test_download_and_render_one_clip(tmp_path, paths):
    video = _video()
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


def test_render_two_clips_end_to_end(tmp_path, paths, monkeypatch):
    video = _video()
    s = Settings()
    clips = [
        Clip(video=video, t=642, mentions=[], score=1.0, caption="종범 골"),
        Clip(video=video, t=758, mentions=[], score=1.0, caption="원더골"),
    ]
    project = Project([video.url], "테스트 하이라이트", [video], clips)
    dl = Downloader(tmp_path / "cache", str(paths.ffmpeg))
    render_root = tmp_path / "render"
    monkeypatch.setattr(pipeline, "render_dir", lambda: render_root)

    output = pipeline.render(project, s, tmp_path / "out" / "하이라이트.mp4", dl, paths)

    assert output.exists()
    info = ff.probe(paths, output)
    assert (info.width, info.height) == (1080, 1920)
    expected = sum(c.duration(s) for c in clips)
    assert abs(info.duration - expected) <= 1.0, f"{info.duration} vs {expected}"
    assert info.has_audio
    assert not [d for d in render_root.iterdir() if d.is_dir()]
