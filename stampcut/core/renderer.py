"""출력 레이아웃 상수, 정방형 기하, ffmpeg 명령 생성."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from stampcut.core.ffmpeg import FfmpegPaths, ProbeInfo
from stampcut.core.models import Clip, Settings
from stampcut.core.textwrap_kr import wrap
from stampcut.core.timestamps import format_time

LAYOUT: dict = {
    "canvas_w": 1080,
    "canvas_h": 1920,
    "square": 1080,
    "square_y": 420,
    "title_font": 64,
    "title_band_h": 420,
    "time_font": 36,
    "time_y": 1510,
    "time_color": "#FFD60A",
    "caption_font": 60,
    "caption_y": 1560,
    "caption_border": 4,
    "line_spacing": 8,
    "max_text_width": 960,
    "max_lines": 2,
    "fps": 30,
}
ZOOM_MIN = 0.5
ZOOM_MAX = 3.0


@dataclass(frozen=True)
class SquareGeometry:
    sw: int
    sh: int
    pad_w: int
    pad_h: int
    pad_x: int
    pad_y: int
    crop_x: int
    crop_y: int


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def compute_square_geometry(width: int, height: int, zoom: float, pan_x: float, pan_y: float, square: int = 1080) -> SquareGeometry:
    """원본을 높이 square*zoom으로 스케일 → 부족하면 패딩 → square×square 크롭."""
    zoom = _clamp(zoom, ZOOM_MIN, ZOOM_MAX)
    pan_x = _clamp(pan_x, 0.0, 1.0)
    pan_y = _clamp(pan_y, 0.0, 1.0)
    sh = _even(square * zoom)
    sw = _even(width * sh / height)
    pad_w, pad_h = max(sw, square), max(sh, square)
    pad_x = round((pad_w - sw) * pan_x)
    pad_y = round((pad_h - sh) * pan_y)
    crop_x = round((pad_w - square) * pan_x)
    crop_y = round((pad_h - square) * pan_y)
    return SquareGeometry(sw, sh, pad_w, pad_h, pad_x, pad_y, crop_x, crop_y)


_BAD_FILENAME = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    cleaned = _BAD_FILENAME.sub("_", name).strip().rstrip(".")
    return cleaned or "highlight"


def unique_output_path(output_dir: Path, title: str) -> Path:
    base = sanitize_filename(title)
    p = output_dir / f"{base}.mp4"
    n = 2
    while p.exists():
        p = output_dir / f"{base} ({n}).mp4"
        n += 1
    return p


def ff_path(p: Path) -> str:
    """drawtext 등 필터 옵션 값에 넣는 경로: 역슬래시→슬래시, 콜론 이스케이프."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def ff_color(hex_color: str) -> str:
    return "0x" + hex_color.lstrip("#")


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, "utf-8")
    return path


def _drawtext(label_in: str, label_out: str, textfile: Path, font: Path, size: int, color: str, x: str, y: str, border: int = 0, line_spacing: int = 0) -> str:
    opts = [
        f"textfile='{ff_path(textfile)}'",
        f"fontfile='{ff_path(font)}'",
        f"fontsize={size}",
        f"fontcolor={color}",
        f"x={x}",
        f"y={y}",
        f"line_spacing={line_spacing}",
    ]
    if border:
        opts += [f"borderw={border}", "bordercolor=black"]
    return f"[{label_in}]drawtext=" + ":".join(opts) + f"[{label_out}]"


def build_clip_command(
    paths: FfmpegPaths,
    clip: Clip,
    settings: Settings,
    title: str,
    probe: ProbeInfo,
    workdir: Path,
    index: int,
    font_path: Path,
) -> tuple[list[str], Path]:
    """클립 하나를 1080x1920 중간 파일로 렌더하는 ffmpeg 명령과 출력 경로."""
    L = LAYOUT
    S, W, H, fps = L["square"], L["canvas_w"], L["canvas_h"], L["fps"]
    g = compute_square_geometry(probe.width, probe.height, clip.zoom, clip.pan_x, clip.pan_y, S)
    dur = clip.duration(settings)
    bg = ff_color(settings.background_color)
    stem = workdir / f"clip_{index:03d}"
    out = stem.with_suffix(".mp4")
    title_txt = _write_text(workdir / f"{stem.name}_title.txt", wrap(title, L["title_font"], L["max_text_width"], L["max_lines"]))
    time_txt = _write_text(workdir / f"{stem.name}_time.txt", format_time(clip.t))
    caption_txt = _write_text(workdir / f"{stem.name}_caption.txt", wrap(clip.caption, L["caption_font"], L["max_text_width"], L["max_lines"]))

    filters = [
        f"[0:v]scale={g.sw}:{g.sh},pad={g.pad_w}:{g.pad_h}:{g.pad_x}:{g.pad_y}:color={bg},crop={S}:{S}:{g.crop_x}:{g.crop_y}[sq]",
        f"color=c={bg}:s={W}x{H}:r={fps}:d={dur}[bg]",
        f"[bg][sq]overlay=0:{L['square_y']}[c0]",
    ]
    last = "c0"
    if title.strip():
        filters.append(_drawtext(last, "c1", title_txt, font_path, L["title_font"], "white", "(w-text_w)/2", f"({L['title_band_h']}-text_h)/2", line_spacing=L["line_spacing"]))
        last = "c1"
    if settings.show_time_in_caption:
        filters.append(_drawtext(last, "c2", time_txt, font_path, L["time_font"], ff_color(L["time_color"]), "(w-text_w)/2", str(L["time_y"])))
        last = "c2"
    if clip.caption.strip():
        filters.append(_drawtext(last, "c3", caption_txt, font_path, L["caption_font"], "white", "(w-text_w)/2", str(L["caption_y"]), border=L["caption_border"], line_spacing=L["line_spacing"]))
        last = "c3"
    filters.append(f"[{last}]null[v]")
    audio_src = "0:a" if probe.has_audio else "1:a"
    filters.append(f"[{audio_src}]afade=t=in:d=0.2,afade=t=out:st={max(0.0, dur - 0.2):.2f}:d=0.2[a]")

    cmd: list = [paths.ffmpeg, "-hide_banner", "-i", clip.final_path]
    if not probe.has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out,
    ]
    return [str(c) for c in cmd], out


def write_concat_list(files: list[Path], workdir: Path) -> Path:
    p = workdir / "concat.txt"
    lines = ["file '" + str(f).replace("\\", "/").replace("'", "'\\''") + "'" for f in files]
    p.write_text("\n".join(lines) + "\n", "utf-8")
    return p


def build_concat_command(paths: FfmpegPaths, list_path: Path, output: Path) -> list[str]:
    return [str(paths.ffmpeg), "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", "-movflags", "+faststart", str(output)]
