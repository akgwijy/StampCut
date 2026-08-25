import threading
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from stampcut.core.downloader import (
    FINAL_FORMAT,
    PREVIEW_FORMAT,
    DownloadCancelled,
    DownloadFailed,
    Downloader,
    final_range,
    preview_covers,
    preview_range,
)
from stampcut.core.models import Settings


class FakeYdl:
    calls: list[dict] = []

    def __init__(self, opts):
        self.opts = opts
        FakeYdl.calls.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        Path(self.opts["outtmpl"]).write_bytes(b"x")


class FailingYdl(FakeYdl):
    def download(self, urls):
        raise DownloadError("boom")


class PartialThenCancelYdl(FakeYdl):
    def download(self, urls):
        out = self.opts["outtmpl"]
        Path(out + ".part").write_bytes(b"partial")
        Path(out).with_suffix("").with_name(Path(out).stem + ".f137.mp4").write_bytes(b"x")
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})


class SilentYdl(FakeYdl):
    def download(self, urls):
        Path(self.opts["outtmpl"] + ".part").write_bytes(b"partial")


@pytest.fixture(autouse=True)
def _reset_calls():
    FakeYdl.calls.clear()


def test_ranges(make_video, make_clip):
    s = Settings()
    clip = make_clip(make_video(duration=1449), t=758)
    assert preview_range(clip, s) == (728, 818)
    assert final_range(clip, s) == (755, 773)
    edge = make_clip(make_video(duration=1449), t=10)
    assert preview_range(edge, s) == (0, 70)
    wide = make_clip(make_video(duration=1449), t=758, pre=40, post=90)
    assert preview_range(wide, s) == (718, 848)


def test_preview_download_sets_fields(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    seen = []
    out = Downloader(tmp_path, "C:/ff", FakeYdl).download_preview(clip, Settings(), on_progress=seen.append)
    assert out == tmp_path / "POZWcyKFvjY" / "preview_728_818.mp4" and out.exists()
    assert (clip.preview_path, clip.preview_start, clip.preview_end) == (out, 728, 818)
    assert preview_covers(clip, Settings()) is True
    opts = FakeYdl.calls[0]
    assert opts["format"] == PREVIEW_FORMAT and opts["ffmpeg_location"] == "C:/ff"
    assert opts["force_keyframes_at_cuts"] is True and opts["merge_output_format"] == "mp4"
    # yt-dlp의 download_range_func.__call__은 info_dict.get(...)을 호출하므로 빈 dict를 넘긴다
    assert list(opts["download_ranges"]({}, None)) == [{"start_time": 728, "end_time": 818}]
    assert seen == [0.5]


def test_final_download_sets_fields(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    out = Downloader(tmp_path, None, FakeYdl).download_final(clip, Settings())
    assert out == tmp_path / "POZWcyKFvjY" / "final_755_773.mp4" and clip.final_path == out
    assert FakeYdl.calls[0]["format"] == FINAL_FORMAT and "ffmpeg_location" not in FakeYdl.calls[0]


def test_cache_hit_skips_download(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    d = Downloader(tmp_path, None, FakeYdl)
    d.download_preview(clip, Settings())
    d.download_preview(clip, Settings())
    assert len(FakeYdl.calls) == 1


def test_preview_covers_false_when_widened(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    Downloader(tmp_path, None, FakeYdl).download_preview(clip, Settings())
    clip.pre = 40
    assert preview_covers(clip, Settings()) is False


def test_cancel_raises_and_removes_partial(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    video_dir = tmp_path / "POZWcyKFvjY"
    video_dir.mkdir(parents=True, exist_ok=True)
    # Create a sibling file that should NOT be deleted
    (video_dir / "preview_728_8180.mp4").write_bytes(b"keep")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        Downloader(tmp_path, None, PartialThenCancelYdl).download_preview(clip, Settings(), cancel=cancel)
    assert not (video_dir / "preview_728_818.mp4").exists()
    # Exactly one file should remain: the sibling file we created
    assert [p.name for p in video_dir.iterdir()] == ["preview_728_8180.mp4"]


def test_download_error_maps_to_download_failed(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    with pytest.raises(DownloadFailed) as ei:
        Downloader(tmp_path, None, FailingYdl).download_final(clip, Settings())
    assert "boom" in str(ei.value)


def test_no_output_raises_download_failed(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(duration=1449), t=758)
    with pytest.raises(DownloadFailed) as ei:
        Downloader(tmp_path, None, SilentYdl).download_final(clip, Settings())
    assert "결과 파일" in str(ei.value)
    # Verify the .part file was cleaned up
    video_dir = tmp_path / "POZWcyKFvjY"
    part_file = video_dir / "final_755_773.mp4.part"
    assert not part_file.exists()
