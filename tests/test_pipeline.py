import threading
from pathlib import Path

import pytest

from stampcut.core import pipeline
from stampcut.core.downloader import DownloadFailed
from stampcut.core.ffmpeg import Cancelled, FfmpegPaths, ProbeInfo
from stampcut.core.models import ClipStatus, Project, RawComment, Settings


class FakeClient:
    def __init__(self, videos, comments):
        self.videos, self.comments = videos, comments

    def fetch_video_infos(self, urls):
        return self.videos

    def fetch_all_comments(self, video_id):
        return self.comments.get(video_id, [])


class FakeDownloader:
    def __init__(self, root, fail_ids=()):
        self.root, self.fail_ids, self.calls = root, set(fail_ids), []

    def download_preview(self, clip, s, on_progress=None, cancel=None):
        self.calls.append(("preview", clip.t))
        if clip.id in self.fail_ids:
            raise DownloadFailed("nope")
        clip.preview_path = self.root / f"p{clip.t}.mp4"
        clip.preview_path.write_bytes(b"x")
        clip.preview_start, clip.preview_end = clip.start(s), clip.end(s)
        return clip.preview_path

    def download_final(self, clip, s, on_progress=None, cancel=None):
        self.calls.append(("final", clip.t))
        if clip.id in self.fail_ids:
            raise DownloadFailed("nope")
        clip.final_path = self.root / f"f{clip.t}.mp4"
        return clip.final_path


def comment(cid, text, author="@a"):
    return RawComment(cid, text, author, 0, False)


def test_analyze_builds_project_and_default_title(make_video):
    v = make_video()
    client = FakeClient([v], {v.video_id: [comment("c1", "7:05 기훈 선방"), comment("c2", "원더골 12:38", "@b")]})
    calls = []
    p = pipeline.analyze([v.url], "", Settings(), client, progress=lambda *a: calls.append(a))
    assert p.title == "26.08.20 문성FC 하이라이트"
    assert [(c.t, c.caption) for c in p.clips] == [(425, "기훈 선방"), (758, "원더골")]
    assert calls[0][0] == "analyze" and calls[-1][1] == 1


def test_analyze_keeps_given_title_and_cancel(make_video):
    v = make_video()
    client = FakeClient([v], {})
    assert pipeline.analyze([v.url], " 내 제목 ", Settings(), client).title == "내 제목"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        pipeline.analyze([v.url], "", Settings(), client, cancel=cancel)


def test_fetch_previews_updates_status(tmp_path, make_video, make_clip):
    v = make_video()
    a, b = make_clip(v, 100), make_clip(v, 500)
    dl = FakeDownloader(tmp_path, fail_ids={b.id})
    p = Project([v.url], "t", [v], [a, b])
    seen = []
    pipeline.fetch_previews(p, Settings(), dl, on_clip=lambda c: seen.append((c.t, c.status)))
    assert a.status is ClipStatus.READY and b.status is ClipStatus.ERROR and b.error == "nope"
    assert (100, ClipStatus.DOWNLOADING) in seen and (100, ClipStatus.READY) in seen


def test_fetch_previews_skips_already_covered(tmp_path, make_video, make_clip):
    v = make_video()
    a = make_clip(v, 100)
    dl = FakeDownloader(tmp_path)
    p = Project([v.url], "t", [v], [a])
    pipeline.fetch_previews(p, Settings(), dl, on_clip=lambda c: None)
    pipeline.fetch_previews(p, Settings(), dl, on_clip=lambda c: None)
    assert dl.calls == [("preview", 100)]


@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(pipeline, "render_dir", lambda: tmp_path / "render")
    monkeypatch.setattr(pipeline.ff, "probe", lambda paths, f: ProbeInfo(1920, 1080, 18.0, True))

    def fake_run(cmd, on_progress=None, cancel=None, total_seconds=None):
        runs.append(cmd)
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")
        if on_progress:
            on_progress(1.0)

    monkeypatch.setattr(pipeline.ff, "run", fake_run)
    return runs


def _paths(tmp_path):
    return FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")


def test_render_flow(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    a, b, c = make_clip(v, 100), make_clip(v, 500, enabled=False), make_clip(v, 900)
    dl = FakeDownloader(tmp_path)
    p = Project([v.url], "제목", [v], [a, b, c])
    out = tmp_path / "out" / "제목.mp4"
    prog = []
    result = pipeline.render(p, Settings(), out, dl, _paths(tmp_path), progress=lambda *x: prog.append(x))
    assert result == out and out.exists()
    assert [t for kind, t in dl.calls if kind == "final"] == [100, 900]
    assert len(fake_ffmpeg) == 3 and "concat" in fake_ffmpeg[-1]
    assert not any((tmp_path / "render").iterdir())
    stages = [x[0] for x in prog]
    assert stages[0] == "download" and "render" in stages and prog[-1][:2] == ("concat", 1)


def test_render_skips_failed_clip_when_allowed(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    a, c = make_clip(v, 100), make_clip(v, 900)
    dl = FakeDownloader(tmp_path, fail_ids={c.id})
    p = Project([v.url], "제목", [v], [a, c])
    asked = []
    pipeline.render(p, Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path), on_clip_failed=lambda clip, why: asked.append(why) or True)
    assert asked == ["nope"] and c.enabled is False and c.status is ClipStatus.ERROR
    assert len(fake_ffmpeg) == 2


def test_render_raises_when_failure_not_allowed(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    c = make_clip(v, 900)
    dl = FakeDownloader(tmp_path, fail_ids={c.id})
    with pytest.raises(DownloadFailed):
        pipeline.render(Project([v.url], "제목", [v], [c]), Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path))


def test_render_without_enabled_clips(tmp_path, make_video, make_clip):
    v = make_video()
    with pytest.raises(ValueError):
        pipeline.render(Project([v.url], "t", [v], [make_clip(v, 1, enabled=False)]), Settings(), tmp_path / "o.mp4", FakeDownloader(tmp_path), _paths(tmp_path))
