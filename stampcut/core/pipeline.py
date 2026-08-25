"""분석 → 미리보기 → 렌더. GUI는 이 함수들을 워커 스레드에서 호출한다.

fetch_previews의 on_clip은 여러 워커 스레드에서 동시에 호출되므로 GUI는 시그널로 마샬링해야 한다.
"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta, timezone
from pathlib import Path
from typing import Callable

from stampcut.core import ffmpeg as ff
from stampcut.core.downloader import DownloadCancelled, DownloadFailed, Downloader, preview_covers
from stampcut.core.highlights import build_clips
from stampcut.core.models import Clip, ClipStatus, Mention, Project, Settings, VideoInfo
from stampcut.core.renderer import build_clip_command, build_concat_command, write_concat_list
from stampcut.core.settings import render_dir, resolve_font
from stampcut.core.timestamps import extract_mentions, format_time
from stampcut.core.youtube_api import YouTubeClient

log = logging.getLogger(__name__)
ProgressFn = Callable[[str, int, int, str], None]
KST = timezone(timedelta(hours=9))


def _noop(stage: str, done: int, total: int, message: str) -> None:
    pass


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ff.Cancelled()


def default_title(videos: list[VideoInfo], s: Settings) -> str:
    v = videos[0]
    return s.title_template.format(date=v.published_at.astimezone(KST).strftime("%y.%m.%d"), channel=v.channel_title)


def analyze(
    urls: list[str],
    title: str,
    s: Settings,
    client: YouTubeClient,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
) -> Project:
    total = len(urls) + 1
    progress("analyze", 0, total, "영상 정보 가져오는 중")
    _check(cancel)
    videos = client.fetch_video_infos(urls)
    mentions: list[Mention] = []
    for i, v in enumerate(videos):
        _check(cancel)
        progress("analyze", i + 1, total, f"{v.short_name} 댓글 수집 중")
        for c in client.fetch_all_comments(v.video_id):
            mentions.extend(extract_mentions(v, c))
    clips = build_clips(mentions, videos, s)
    log.info("analyze: %d videos, %d mentions, %d clips", len(videos), len(mentions), len(clips))
    progress("analyze", total, total, f"후보 {len(clips)}개")
    return Project(urls=list(urls), title=title.strip() or default_title(videos, s), videos=videos, clips=clips)


def _preview_one(clip: Clip, s: Settings, downloader: Downloader, on_clip: Callable[[Clip], None], cancel) -> None:
    if cancel is not None and cancel.is_set():
        return
    clip.status, clip.error = ClipStatus.DOWNLOADING, ""
    on_clip(clip)
    try:
        downloader.download_preview(clip, s, cancel=cancel)
        clip.status = ClipStatus.READY
    except DownloadCancelled:
        clip.status = ClipStatus.PENDING
    except DownloadFailed as e:
        clip.status, clip.error = ClipStatus.ERROR, str(e)
        log.warning("preview failed for %s@%s: %s", clip.video.video_id, clip.t, e)
    on_clip(clip)


def fetch_previews(
    project: Project,
    s: Settings,
    downloader: Downloader,
    on_clip: Callable[[Clip], None],
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
    clips: list[Clip] | None = None,
) -> None:
    targets = [c for c in (clips if clips is not None else project.clips) if not preview_covers(c, s)]
    if not targets:
        progress("preview", 0, 0, "미리보기 준비됨")
        return
    n = len(targets)
    progress("preview", 0, n, f"미리보기용 구간 받는 중 0 / {n}")
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, s.parallel_downloads)) as ex:
        futures = [ex.submit(_preview_one, c, s, downloader, on_clip, cancel) for c in targets]
        for _ in as_completed(futures):
            done += 1
            progress("preview", done, n, f"미리보기용 구간 받는 중 {done} / {n}")


def _label(c: Clip) -> str:
    return f"{c.video.short_name} {format_time(c.t)}"


def render(
    project: Project,
    s: Settings,
    output_path: Path,
    downloader: Downloader,
    paths: ff.FfmpegPaths,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
    on_clip_failed: Callable[[Clip, str], bool] | None = None,
) -> Path:
    clips = project.enabled_clips()
    if not clips:
        raise ValueError("켜진 클립이 없습니다")
    _check(cancel)
    job = render_dir() / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    font = resolve_font(s)
    n = len(clips)

    kept: list[Clip] = []
    for i, c in enumerate(clips):
        _check(cancel)
        label = _label(c)
        progress("download", i, n, f"{label} 고화질 받는 중")
        try:
            downloader.download_final(c, s, cancel=cancel)
            kept.append(c)
            progress("download", i + 1, n, f"{label} 받기 완료")
        except DownloadCancelled:
            raise ff.Cancelled()
        except DownloadFailed as e:
            c.status, c.error = ClipStatus.ERROR, str(e)
            if on_clip_failed and on_clip_failed(c, str(e)):
                c.enabled = False
                continue
            raise

    outputs: list[Path] = []
    total_units = len(kept) * 100
    for i, c in enumerate(kept):
        _check(cancel)
        label = _label(c)
        progress("render", i * 100, total_units, f"{label} 렌더링 중")
        info = ff.probe(paths, c.final_path)
        cmd, out = build_clip_command(paths, c, s, project.title, info, job, i, font)

        def _on_fraction(f: float, i: int = i, label: str = label) -> None:
            progress("render", i * 100 + int(f * 100), total_units, f"{label} 렌더링 중")

        ff.run(cmd, on_progress=_on_fraction, cancel=cancel, total_seconds=c.duration(s))
        outputs.append(out)

    progress("concat", 0, 1, "합치는 중")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ff.run(build_concat_command(paths, write_concat_list(outputs, job), output_path), cancel=cancel)
    shutil.rmtree(job, ignore_errors=True)
    progress("concat", 1, 1, "완료")
    return output_path
