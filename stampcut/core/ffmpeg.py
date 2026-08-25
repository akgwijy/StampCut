"""ffmpeg/ffprobe 찾기·실행·진행률."""
from __future__ import annotations

import collections
import json
import logging
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# ffmpeg의 out_time_ms는 이름과 달리 마이크로초 단위다.
_PROGRESS_RE = re.compile(r"^out_time_(?:ms|us)=(\d+)")


class FfmpegError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class FfmpegPaths:
    ffmpeg: Path
    ffprobe: Path


@dataclass(frozen=True)
class ProbeInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


def _pair(candidate: Path) -> FfmpegPaths | None:
    if not candidate.is_file():
        return None
    probe_path = candidate.with_name("ffprobe" + candidate.suffix)
    return FfmpegPaths(candidate, probe_path) if probe_path.is_file() else None


def find_ffmpeg(configured: str = "", app_dir: Path | None = None) -> FfmpegPaths | None:
    candidates: list[Path] = []
    if app_dir:
        candidates.append(Path(app_dir) / "bin" / "ffmpeg.exe")
    if configured:
        p = Path(configured)
        candidates.append(p if p.name.lower().startswith("ffmpeg") else p / "ffmpeg.exe")
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        pair = _pair(c)
        if pair:
            return pair
    return None


def parse_probe_json(text: str) -> ProbeInfo:
    data = json.loads(text)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise FfmpegError("비디오 스트림이 없습니다")
    w, h = int(video["width"]), int(video["height"])
    rotation = 0
    for sd in video.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    tags_rot = video.get("tags", {}).get("rotate")
    if tags_rot:
        rotation = int(float(tags_rot))
    if abs(rotation) % 180 == 90:
        w, h = h, w
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return ProbeInfo(w, h, duration, has_audio)


def probe(paths: FfmpegPaths, file: Path) -> ProbeInfo:
    cmd = [str(paths.ffprobe), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(file)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if res.returncode != 0:
        raise FfmpegError(res.stderr[-2000:])
    return parse_probe_json(res.stdout)


def parse_progress_line(line: str) -> float | None:
    m = _PROGRESS_RE.match(line.strip())
    return int(m.group(1)) / 1_000_000 if m else None


def run(
    cmd: list,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    total_seconds: float | None = None,
) -> None:
    full = [str(c) for c in cmd] + ["-progress", "pipe:1", "-nostats", "-y"]
    log.info("run: %s", " ".join(full))
    proc = subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    tail: collections.deque[str] = collections.deque(maxlen=60)

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip())

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            secs = parse_progress_line(line)
            if secs is not None and on_progress and total_seconds:
                on_progress(min(1.0, secs / total_seconds))
    finally:
        proc.wait()
        t.join(timeout=5)
    if proc.returncode != 0:
        raise FfmpegError("\n".join(list(tail)[-30:]))
