"""분석 → 미리보기 → 렌더. GUI는 이 함수들을 워커 스레드에서 호출한다.

fetch_previews의 on_clip은 여러 워커 스레드에서 동시에 호출되므로 GUI는 시그널로 마샬링해야 한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
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
from stampcut.core.renderer import PREVIEW_PROFILE, build_clip_command, build_concat_command, build_mix_command, write_concat_list
from stampcut.core.settings import preview_dir, render_dir, resolve_font
from stampcut.core.timestamps import extract_mentions, format_time
from stampcut.core.youtube_api import YouTubeClient, parse_video_id

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
    videos = client.fetch_video_infos(urls, strict=False)
    found = {v.video_id for v in videos}
    missing = [u for u in urls if parse_video_id(u) not in found]
    mentions: list[Mention] = []
    for i, v in enumerate(videos):
        _check(cancel)
        progress("analyze", i + 1, total, f"{v.short_name} 댓글 수집 중")
        for c in client.fetch_all_comments(v.video_id):
            mentions.extend(extract_mentions(v, c))
    clips = build_clips(mentions, videos, s)
    log.info("analyze: %d videos, %d mentions, %d clips", len(videos), len(mentions), len(clips))
    progress("analyze", total, total, f"후보 {len(clips)}개")
    return Project(
        urls=list(urls),
        title=title.strip() or default_title(videos, s),
        videos=videos,
        clips=clips,
        warnings=[f"영상을 찾을 수 없어 건너뜀: {u}" for u in missing],
    )


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
    audio = project.audio
    if audio.has_bgm() and not Path(audio.bgm_path).is_file():
        raise ValueError(f"배경 음악 파일을 찾을 수 없습니다: {audio.bgm_path}")
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

    if not kept:
        raise ValueError("모든 클립의 다운로드에 실패했습니다")

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
    concat_out = job / "concat.mp4"
    ff.run(build_concat_command(paths, write_concat_list(outputs, job), concat_out), cancel=cancel)
    final = concat_out
    if not audio.is_default():
        _check(cancel)
        progress("mix", 0, 1, "배경 음악 섞는 중")
        total = ff.probe(paths, concat_out).duration
        final = job / "output.mp4"
        ff.run(build_mix_command(paths, concat_out, audio, total, final), cancel=cancel, total_seconds=total)
    # 성공했을 때만 결과 파일이 생기도록 임시 파일을 옮긴다 (취소/실패는 output_path를 남기지 않는다).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(final, output_path)
    for d in render_dir().iterdir():  # 이번 작업 폴더와 이전에 남은 찌꺼기를 함께 지운다
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    progress("concat" if audio.is_default() else "mix", 1, 1, "완료")
    return output_path


def preview_signature(project: Project, s: Settings) -> str:
    """전체 미리보기 파일이 지금 편집 상태와 맞는지 비교하기 위한 해시. 화면에 보이는 것만 (BGM·끈 클립 제외)."""
    payload = {
        "title": project.title,
        "clips": [
            [c.video.video_id, c.t, c.effective_pre(s), c.effective_post(s), c.zoom, c.pan_x, c.pan_y, c.caption]
            for c in project.enabled_clips()
        ],
        "style": [s.title_y, s.title_color, s.caption_y, s.caption_color, s.background_color, s.show_time_in_caption, s.font_path],
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def render_preview(
    project: Project,
    s: Settings,
    paths: ff.FfmpegPaths,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
) -> Path:
    """켜진 클립의 360p 미리보기 구간으로 540x960 전체 미리보기 mp4를 만든다. 고화질 다운로드·BGM 없음."""
    clips = project.enabled_clips()
    if not clips:
        raise ValueError("켜진 클립이 없습니다")
    missing = [c for c in clips if not preview_covers(c, s)]
    if missing:
        raise ValueError("미리보기가 준비되지 않은 클립: " + ", ".join(_label(c) for c in missing))
    _check(cancel)
    root = preview_dir()
    job = root / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    font = resolve_font(s)
    n = len(clips)
    try:
        outputs: list[Path] = []
        for i, c in enumerate(clips):
            _check(cancel)
            label = _label(c)
            progress("preview_render", i * 100, n * 100, f"{label} 미리보기 렌더 중")
            info = ff.probe(paths, c.preview_path)
            cmd, out = build_clip_command(
                paths, c, s, project.title, info, job, i, font,
                profile=PREVIEW_PROFILE, source=c.preview_path, in_offset=c.start(s) - c.preview_start,
            )

            def _on_fraction(f: float, i: int = i, label: str = label) -> None:
                progress("preview_render", i * 100 + int(f * 100), n * 100, f"{label} 미리보기 렌더 중")

            ff.run(cmd, on_progress=_on_fraction, cancel=cancel, total_seconds=c.duration(s))
            outputs.append(out)
        _check(cancel)
        progress("preview_render", n * 100, n * 100, "합치는 중")
        tmp = job / "full.mp4"
        ff.run(build_concat_command(paths, write_concat_list(outputs, job), tmp), cancel=cancel)
        result = root / f"full_{job.name}.mp4"
        os.replace(tmp, result)
    finally:
        shutil.rmtree(job, ignore_errors=True)
    for old in root.iterdir():  # 이전 결과 파일과 비정상 종료로 남은 작업 폴더를 함께 지운다
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        elif old != result and old.name.startswith("full_") and old.suffix == ".mp4":
            try:
                old.unlink()
            except OSError:  # 플레이어가 잡고 있는 파일 — 다음에 다시 시도
                pass
    progress("preview_render", n * 100, n * 100, "전체 미리보기 준비됨")
    return result
