"""ffmpeg/ffprobe 찾기·실행·진행률."""
from __future__ import annotations

import collections
import functools
import json
import logging
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from stampcut.core import platforms

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# 렌더에 꼭 필요한 필터. Homebrew의 슬림 `ffmpeg`(2026-01~)에는 drawtext가 없다 → `ffmpeg-full` 필요.
REQUIRED_FILTERS = ("drawtext",)
# `ffmpeg -filters` 한 줄: 플래그 열(FFmpeg ≤8은 "T.C" 3글자, 9부터 "TS" 2글자) → 이름 → 입출력 → 설명
_FILTER_LINE_RE = re.compile(r"^\s*[TSC.]{2,3}\s+(\w+)\s+[AVN|]+->[AVN|]+", re.M)
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


def exe_name(base: str) -> str:
    """Windows에서만 .exe를 붙인다."""
    return f"{base}.exe" if platforms.current() == "windows" else base


def fallback_dirs() -> list[Path]:
    """PATH에 없을 때 뒤지는 OS별 기본 설치 폴더.

    macOS: Finder/Dock에서 실행하면 PATH에 Homebrew가 없고, `ffmpeg-full`은 keg-only라 PATH에 아예 안 잡힌다.
    """
    match platforms.current():
        case "macos":
            return [
                Path("/opt/homebrew/opt/ffmpeg-full/bin"),  # Apple Silicon
                Path("/usr/local/opt/ffmpeg-full/bin"),  # Intel
                Path("/opt/homebrew/bin"),
                Path("/usr/local/bin"),
                Path("/opt/local/bin"),
            ]
        case "linux":
            return [Path("/usr/bin"), Path("/usr/local/bin")]
        case _:
            return []


def install_hint() -> str:
    match platforms.current():
        case "windows":
            return "winget install Gyan.FFmpeg"
        case "macos":
            return "brew install ffmpeg-full"
        case _:
            return "sudo apt install ffmpeg"


@functools.lru_cache(maxsize=16)
def missing_filters(ffmpeg: Path) -> tuple[str, ...]:
    """이 ffmpeg에 빠진 필수 필터. 실행이 안 되면 전부 빠진 것으로 본다. 경로별로 캐시."""
    try:
        res = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-filters"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return REQUIRED_FILTERS
    names = set(_FILTER_LINE_RE.findall(res.stdout))
    return tuple(f for f in REQUIRED_FILTERS if f not in names)


def find_ffmpeg(configured: str = "", app_dir: Path | None = None) -> FfmpegPaths | None:
    """탐색 순서: 앱 폴더 bin → 설정 경로(파일 또는 폴더) → PATH → OS별 기본 설치 폴더.

    그중 필수 필터를 갖춘 첫 후보를 고른다. 갖춘 게 없으면 첫 후보를 돌려주고, 호출자가 missing_filters()로 안내한다.
    """
    exe = exe_name("ffmpeg")
    candidates: list[Path] = []
    if app_dir:
        candidates.append(Path(app_dir) / "bin" / exe)
    if configured:
        p = Path(configured)
        candidates.append(p if p.name.lower().startswith("ffmpeg") else p / exe)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))
    candidates.extend(d / exe for d in fallback_dirs())
    pairs = [pair for c in candidates if (pair := _pair(c))]
    if not pairs:
        return None
    return next((p for p in pairs if not missing_filters(p.ffmpeg)), pairs[0])


def parse_probe_json(text: str) -> ProbeInfo:
    data = json.loads(text)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise FfmpegError("비디오 스트림이 없습니다")
    w, h = int(video["width"]), int(video["height"])
    rotation = 0
    side_data_rotation = None
    for sd in video.get("side_data_list", []):
        if "rotation" in sd:
            side_data_rotation = int(float(sd["rotation"]))
    if side_data_rotation is not None:
        rotation = side_data_rotation
    else:
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
    tail: collections.deque[str] = collections.deque(maxlen=30)

    with subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    ) as proc:
        lines: queue.Queue[str | None] = queue.Queue()

        def _read_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        def _drain() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                tail.append(line.rstrip())

        reader = threading.Thread(target=_read_stdout, daemon=True)
        drainer = threading.Thread(target=_drain, daemon=True)
        reader.start()
        drainer.start()
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    raise Cancelled()
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                secs = parse_progress_line(line)
                if secs is not None and on_progress and total_seconds:
                    on_progress(min(1.0, secs / total_seconds))
        finally:
            proc.wait()
            reader.join(timeout=5)
            drainer.join(timeout=5)
    if proc.returncode != 0:
        raise FfmpegError("\n".join(tail))
