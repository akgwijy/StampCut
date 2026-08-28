import threading
from pathlib import Path

import pytest

from stampcut.core import pipeline
from stampcut.core.downloader import DownloadCancelled, DownloadFailed
from stampcut.core.ffmpeg import Cancelled, FfmpegPaths, ProbeInfo
from stampcut.core.models import AudioMix, ClipStatus, Project, RawComment, Settings


class FakeClient:
    def __init__(self, videos, comments):
        self.videos, self.comments = videos, comments

    def fetch_video_infos(self, urls, strict=True):
        return self.videos

    def fetch_all_comments(self, video_id):
        return self.comments.get(video_id, [])


class FakeDownloader:
    def __init__(self, root, fail_ids=(), cancel_ids=()):
        self.root, self.fail_ids, self.cancel_ids, self.calls = root, set(fail_ids), set(cancel_ids), []

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
        if clip.id in self.cancel_ids:
            raise DownloadCancelled()
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
    assert calls[0][0] == "analyze" and calls[-1][1] == calls[-1][2] == 2


def test_analyze_keeps_given_title_and_cancel(make_video):
    v = make_video()
    client = FakeClient([v], {})
    assert pipeline.analyze([v.url], " 내 제목 ", Settings(), client).title == "내 제목"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        pipeline.analyze([v.url], "", Settings(), client, cancel=cancel)


def test_analyze_warns_on_missing_video(make_video):
    v = make_video()
    client = FakeClient([v], {v.video_id: [comment("c1", "7:05 기훈 선방")]})
    missing_url = "https://youtu.be/" + "B" * 11
    p = pipeline.analyze([v.url, missing_url], "", Settings(), client)
    assert len(p.warnings) == 1 and missing_url in p.warnings[0]
    assert [c.t for c in p.clips] == [425]


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


def test_fetch_previews_respects_preset_cancel(tmp_path, make_video, make_clip):
    v = make_video()
    a = make_clip(v, 100)
    dl = FakeDownloader(tmp_path)
    cancel = threading.Event()
    cancel.set()
    pipeline.fetch_previews(Project([v.url], "t", [v], [a]), Settings(), dl, on_clip=lambda c: None, cancel=cancel)
    assert dl.calls == [] and a.status is ClipStatus.PENDING


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


def test_render_preset_cancel_raises_before_any_work(tmp_path, make_video, make_clip, monkeypatch):
    monkeypatch.setattr(pipeline, "render_dir", lambda: tmp_path / "render")
    v = make_video()
    dl = FakeDownloader(tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        pipeline.render(Project([v.url], "t", [v], [make_clip(v, 100)]), Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path), cancel=cancel)
    assert dl.calls == [] and not (tmp_path / "render").exists()


def test_render_download_cancelled_becomes_cancelled(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    c = make_clip(v, 100)
    dl = FakeDownloader(tmp_path, cancel_ids={c.id})
    with pytest.raises(Cancelled):
        pipeline.render(Project([v.url], "t", [v], [c]), Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path))
    assert fake_ffmpeg == []


def test_render_flow(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    a, b, c = make_clip(v, 100), make_clip(v, 500, enabled=False), make_clip(v, 900)
    dl = FakeDownloader(tmp_path)
    p = Project([v.url], "제목", [v], [a, b, c])
    out = tmp_path / "out" / "제목.mp4"
    stale = tmp_path / "render" / "old-job"
    stale.mkdir(parents=True)
    (stale / "x.txt").write_text("남은 찌꺼기", "utf-8")
    prog = []
    result = pipeline.render(p, Settings(), out, dl, _paths(tmp_path), progress=lambda *x: prog.append(x))
    assert result == out and out.exists()
    assert [t for kind, t in dl.calls if kind == "final"] == [100, 900]
    assert len(fake_ffmpeg) == 3 and "concat" in fake_ffmpeg[-1]
    assert not stale.exists()
    assert not any((tmp_path / "render").iterdir())
    stages = [x[0] for x in prog]
    assert stages[0] == "download" and "render" in stages and prog[-1][:2] == ("concat", 1)
    assert any(x[0] == "download" and x[1] == 2 and x[2] == 2 for x in prog)


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
    assert not (tmp_path / "o.mp4").exists()
    assert any((tmp_path / "render").iterdir())


def test_render_all_clips_skipped_raises(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    a, c = make_clip(v, 100), make_clip(v, 900)
    dl = FakeDownloader(tmp_path, fail_ids={a.id, c.id})
    p = Project([v.url], "제목", [v], [a, c])
    with pytest.raises(ValueError):
        pipeline.render(p, Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path), on_clip_failed=lambda clip, why: True)
    assert fake_ffmpeg == [] and not (tmp_path / "o.mp4").exists()


def test_render_cancel_leaves_no_output(tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(pipeline, "render_dir", lambda: tmp_path / "render")
    monkeypatch.setattr(pipeline.ff, "probe", lambda paths, f: ProbeInfo(1920, 1080, 18.0, True))

    def fake_run(cmd, on_progress=None, cancel=None, total_seconds=None):
        if "concat" in cmd:
            raise Cancelled()
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")

    monkeypatch.setattr(pipeline.ff, "run", fake_run)
    v = make_video()
    c = make_clip(v, 100)
    output = tmp_path / "out" / "o.mp4"
    with pytest.raises(Cancelled):
        pipeline.render(Project([v.url], "제목", [v], [c]), Settings(), output, FakeDownloader(tmp_path), _paths(tmp_path))
    assert not output.exists()


def test_render_without_enabled_clips(tmp_path, make_video, make_clip):
    v = make_video()
    with pytest.raises(ValueError):
        pipeline.render(Project([v.url], "t", [v], [make_clip(v, 1, enabled=False)]), Settings(), tmp_path / "o.mp4", FakeDownloader(tmp_path), _paths(tmp_path))


def test_render_mixes_bgm_after_concat(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    p.audio = AudioMix(bgm_path=str(song), bgm_volume=0.2)
    out = tmp_path / "out" / "제목.mp4"
    prog = []
    pipeline.render(p, Settings(), out, FakeDownloader(tmp_path), _paths(tmp_path), progress=lambda *x: prog.append(x))
    assert out.exists() and len(fake_ffmpeg) == 3  # 클립 렌더 + concat + 믹스
    mix = fake_ffmpeg[-1]
    assert str(song) in mix and "-stream_loop" in mix and mix[mix.index("-c:v") + 1] == "copy"
    assert mix[mix.index("-t") + 1] == "18.000"  # probe 길이
    assert "concat" in " ".join(fake_ffmpeg[-2])
    assert [x[0] for x in prog if x[0] == "mix"] == ["mix", "mix"] and prog[-1][:2] == ("mix", 1)
    assert not any((tmp_path / "render").iterdir())


def test_render_skips_mix_for_default_audio(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    pipeline.render(p, Settings(), tmp_path / "o.mp4", FakeDownloader(tmp_path), _paths(tmp_path))
    assert len(fake_ffmpeg) == 2 and not any("-stream_loop" in c for c in fake_ffmpeg)


def test_render_original_volume_only_still_mixes(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    p.audio = AudioMix(original_volume=0.5)
    pipeline.render(p, Settings(), tmp_path / "o.mp4", FakeDownloader(tmp_path), _paths(tmp_path))
    mix = fake_ffmpeg[-1]
    assert len(fake_ffmpeg) == 3 and "volume=0.500" in mix[mix.index("-filter_complex") + 1]


def test_render_missing_bgm_fails_before_download(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    p.audio = AudioMix(bgm_path=str(tmp_path / "gone.mp3"))
    dl = FakeDownloader(tmp_path)
    with pytest.raises(ValueError, match="배경 음악"):
        pipeline.render(p, Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path))
    assert dl.calls == [] and fake_ffmpeg == []


@pytest.fixture
def fake_preview_ffmpeg(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(pipeline, "preview_dir", lambda: tmp_path / "preview")
    monkeypatch.setattr(pipeline.ff, "probe", lambda paths, f: ProbeInfo(640, 360, 90.0, True))

    def fake_run(cmd, on_progress=None, cancel=None, total_seconds=None):
        runs.append(cmd)
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")
        if on_progress:
            on_progress(1.0)

    monkeypatch.setattr(pipeline.ff, "run", fake_run)
    return runs


def _ready(clip, s, root):
    """여유분(앞 30초/뒤 60초)이 있는 미리보기 구간 파일이 받아진 상태."""
    clip.preview_start, clip.preview_end = max(0, clip.t - 30), clip.t + 60
    clip.preview_path = root / f"p{clip.t}.mp4"
    clip.preview_path.write_bytes(b"x")
    clip.status = ClipStatus.READY
    return clip


def test_render_preview_uses_preview_files_with_offsets(fake_preview_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    s = Settings()
    a, off, c = _ready(make_clip(v, 100), s, tmp_path), make_clip(v, 500, enabled=False), _ready(make_clip(v, 900), s, tmp_path)
    p = Project([v.url], "제목", [v], [a, off, c])
    prog = []
    result = pipeline.render_preview(p, s, _paths(tmp_path), progress=lambda *x: prog.append(x))
    assert result.parent == tmp_path / "preview" and result.name.startswith("full_") and result.exists()
    assert len(fake_preview_ffmpeg) == 3  # 켜진 클립 2개 + concat
    first = fake_preview_ffmpeg[0]
    assert first[first.index("-ss") + 1] == "27"  # start(97) - preview_start(70)
    assert first[first.index("-i") + 1] == str(a.preview_path)
    assert first[first.index("-preset") + 1] == "ultrafast" and "s=540x960" in first[first.index("-filter_complex") + 1]
    assert "concat" in fake_preview_ffmpeg[-1]
    assert not any(d.is_dir() for d in (tmp_path / "preview").iterdir())  # 작업 폴더 정리
    assert prog[0][0] == "preview_render" and prog[-1][3] == "전체 미리보기 준비됨"


def test_render_preview_replaces_previous_file(fake_preview_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    s = Settings()
    p = Project([v.url], "제목", [v], [_ready(make_clip(v, 100), s, tmp_path)])
    old = tmp_path / "preview" / "full_old.mp4"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    result = pipeline.render_preview(p, s, _paths(tmp_path))
    assert not old.exists() and result.exists()


def test_render_preview_requires_ready_previews(tmp_path, make_video, make_clip, monkeypatch):
    monkeypatch.setattr(pipeline, "preview_dir", lambda: tmp_path / "preview")
    v = make_video()
    s = Settings()
    a, b = _ready(make_clip(v, 100), s, tmp_path), make_clip(v, 758)
    with pytest.raises(ValueError, match="3게임 12:38"):
        pipeline.render_preview(Project([v.url], "제목", [v], [a, b]), s, _paths(tmp_path))
    assert not (tmp_path / "preview").exists()
    with pytest.raises(ValueError, match="켜진 클립"):
        pipeline.render_preview(Project([v.url], "제목", [v], [make_clip(v, 1, enabled=False)]), s, _paths(tmp_path))


def test_render_preview_cancel_cleans_up(tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(pipeline, "preview_dir", lambda: tmp_path / "preview")
    monkeypatch.setattr(pipeline.ff, "probe", lambda paths, f: ProbeInfo(640, 360, 90.0, True))

    def fake_run(cmd, on_progress=None, cancel=None, total_seconds=None):
        if "concat" in cmd:
            raise Cancelled()
        Path(cmd[-1]).write_bytes(b"x")

    monkeypatch.setattr(pipeline.ff, "run", fake_run)
    v = make_video()
    s = Settings()
    p = Project([v.url], "제목", [v], [_ready(make_clip(v, 100), s, tmp_path)])
    with pytest.raises(Cancelled):
        pipeline.render_preview(p, s, _paths(tmp_path))
    assert list((tmp_path / "preview").iterdir()) == []


def test_preview_signature_tracks_visible_edits_only(make_video, make_clip):
    v = make_video()
    s = Settings()
    a, b = make_clip(v, 100), make_clip(v, 500, enabled=False)
    p = Project([v.url], "제목", [v], [a, b])
    base = pipeline.preview_signature(p, s)
    assert base == pipeline.preview_signature(p, s)
    b.caption = "끈 클립 자막"
    p.audio = AudioMix(bgm_path="x.mp3")
    assert pipeline.preview_signature(p, s) == base  # 끈 클립·BGM은 화면에 안 보임
    a.caption = "바뀐 자막"
    changed = pipeline.preview_signature(p, s)
    assert changed != base
    b.enabled = True
    assert pipeline.preview_signature(p, s) != changed
    p.title = "다른 제목"
    t = pipeline.preview_signature(p, s)
    assert t != changed
    assert pipeline.preview_signature(p, Settings(title_y=100)) != t
