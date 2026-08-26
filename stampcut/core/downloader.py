"""yt-dlp로 필요한 구간만 받는다 (미리보기 360p / 최종 ≤1080p)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, download_range_func

from stampcut.core.models import Clip, Settings

log = logging.getLogger(__name__)
PREVIEW_FORMAT = "bv*[height<=360]+ba/b[height<=360]/bv*+ba/b"
FINAL_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"


class DownloadFailed(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def _remove_partials(out: Path) -> None:
    """out 자체와 yt-dlp가 남긴 .part / .fNNN 중간 파일을 지운다."""
    for p in out.parent.glob(out.stem + ".*"):
        if p.is_file():
            try:
                p.unlink()
            except OSError as e:  # 잠긴 파일 등 — 정리 실패가 취소/실패 예외를 가리면 안 된다
                log.warning("partial cleanup failed for %s: %s", p, e)


def preview_range(clip: Clip, s: Settings) -> tuple[int, int]:
    a = min(max(0, clip.t - s.preview_margin_pre), clip.start(s))
    b = max(min(clip.video.duration, clip.t + s.preview_margin_post), clip.end(s))
    return a, b


def final_range(clip: Clip, s: Settings) -> tuple[int, int]:
    return clip.start(s), clip.end(s)


def preview_covers(clip: Clip, s: Settings) -> bool:
    return (
        clip.preview_path is not None
        and clip.preview_start is not None
        and clip.preview_end is not None
        and clip.preview_path.exists()
        and clip.preview_start <= clip.start(s)
        and clip.preview_end >= clip.end(s)
    )


class Downloader:
    def __init__(self, cache_dir: Path, ffmpeg_location: str | None = None, ydl_factory: Callable = YoutubeDL):
        self.cache_dir = Path(cache_dir)
        self.ffmpeg_location = ffmpeg_location
        self.ydl_factory = ydl_factory

    def _download(
        self,
        url: str,
        start: int,
        end: int,
        fmt: str,
        out: Path,
        on_progress: Callable[[float], None] | None,
        cancel: threading.Event | None,
    ) -> Path:
        if out.exists() and out.stat().st_size > 0:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)

        def hook(d: dict) -> None:
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled()
            if on_progress and d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

        opts: dict = {
            "format": fmt,
            "outtmpl": str(out),
            "merge_output_format": "mp4",
            "download_ranges": download_range_func(None, [(start, end)]),
            "force_keyframes_at_cuts": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [hook],
            "overwrites": True,
            "retries": 3,
        }
        if self.ffmpeg_location:
            opts["ffmpeg_location"] = self.ffmpeg_location
        log.info("download %s [%s-%s] -> %s", url, start, end, out)
        try:
            with self.ydl_factory(opts) as ydl:
                ydl.download([url])
        except DownloadCancelled:
            _remove_partials(out)
            raise
        except DownloadError as e:
            raise DownloadFailed(str(e)) from e
        if not out.exists() or out.stat().st_size == 0:
            _remove_partials(out)
            raise DownloadFailed("다운로드 결과 파일이 없습니다")
        return out

    def download_preview(self, clip: Clip, s: Settings, on_progress=None, cancel=None) -> Path:
        a, b = preview_range(clip, s)
        out = self.cache_dir / clip.video.video_id / f"preview_{a}_{b}.mp4"
        self._download(clip.video.url, a, b, PREVIEW_FORMAT, out, on_progress, cancel)
        clip.preview_start, clip.preview_end, clip.preview_path = a, b, out
        return out

    def download_final(self, clip: Clip, s: Settings, on_progress=None, cancel=None) -> Path:
        a, b = final_range(clip, s)
        out = self.cache_dir / clip.video.video_id / f"final_{a}_{b}.mp4"
        self._download(clip.video.url, a, b, FINAL_FORMAT, out, on_progress, cancel)
        clip.final_path = out
        return out
