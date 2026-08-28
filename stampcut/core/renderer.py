"""출력 레이아웃 상수, 정방형 기하, ffmpeg 명령 생성."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from stampcut.core.ffmpeg import FfmpegPaths, ProbeInfo
from stampcut.core.models import AudioMix, Clip, Settings
from stampcut.core.textwrap_kr import wrap
from stampcut.core.timestamps import format_time

LAYOUT: dict = {
    "canvas_w": 1080,
    "canvas_h": 1920,
    "square": 1080,
    "square_y": 420,
    "title_font": 64,
    "time_font": 36,
    "time_color": "#FFD60A",
    "caption_font": 60,
    "caption_border": 4,
    "line_spacing": 0,
    "max_text_width": 960,
    "max_lines": 2,
    "fps": 30,
}
ZOOM_MIN = 0.5
ZOOM_MAX = 3.0
TIME_GAP = 42  # 시간 표시는 자막 상단에서 이만큼 위


@dataclass(frozen=True)
class RenderProfile:
    scale: float = 1.0  # 캔버스·정방형·폰트·Y좌표·간격 배율
    preset: str = "medium"
    crf: int = 18


FINAL_PROFILE = RenderProfile()
PREVIEW_PROFILE = RenderProfile(scale=0.5, preset="ultrafast", crf=28)  # 540x960 전체 미리보기용


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
    profile: RenderProfile = FINAL_PROFILE,
    source: Path | None = None,
    in_offset: float = 0.0,
) -> tuple[list[str], Path]:
    """클립 하나를 profile.scale 배율의 9:16 중간 파일로 렌더하는 ffmpeg 명령과 출력 경로.

    source가 None이면 clip.final_path. in_offset > 0이면 입력을 그만큼 건너뛴다 (여유분이 있는 미리보기 구간 파일용).
    기본 인자로 부르면 최종 렌더(1080x1920, medium, crf 18) 명령과 완전히 같다.
    """
    L = LAYOUT
    k = profile.scale
    S, W, H, fps = _even(L["square"] * k), _even(L["canvas_w"] * k), _even(L["canvas_h"] * k), L["fps"]
    square_y = _even(L["square_y"] * k)
    title_font, time_font, caption_font = round(L["title_font"] * k), round(L["time_font"] * k), round(L["caption_font"] * k)
    g = compute_square_geometry(probe.width, probe.height, clip.zoom, clip.pan_x, clip.pan_y, S)
    title_y = round(_clamp(settings.title_y, 0, L["canvas_h"]) * k)
    caption_y = round(_clamp(settings.caption_y, 0, L["canvas_h"]) * k)
    time_gap = round(TIME_GAP * k)
    dur = clip.duration(settings)
    bg = ff_color(settings.background_color)
    stem = workdir / f"clip_{index:03d}"
    out = stem.with_suffix(".mp4")
    # 줄바꿈은 배율 적용 전 값으로 계산해 최종 출력과 같은 자리에서 끊는다
    title_txt = _write_text(workdir / f"{stem.name}_title.txt", wrap(title, L["title_font"], L["max_text_width"], L["max_lines"]))
    time_txt = _write_text(workdir / f"{stem.name}_time.txt", format_time(clip.t))
    caption_txt = _write_text(workdir / f"{stem.name}_caption.txt", wrap(clip.caption, L["caption_font"], L["max_text_width"], L["max_lines"]))

    filters = [
        f"[0:v]scale={g.sw}:{g.sh},pad={g.pad_w}:{g.pad_h}:{g.pad_x}:{g.pad_y}:color={bg},crop={S}:{S}:{g.crop_x}:{g.crop_y}[sq]",
        f"color=c={bg}:s={W}x{H}:r={fps}:d={dur}[bg]",
        f"[bg][sq]overlay=0:{square_y}[c0]",
    ]
    last = "c0"
    if title.strip():
        filters.append(_drawtext(last, "c1", title_txt, font_path, title_font, ff_color(settings.title_color), "(w-text_w)/2", f"{title_y}-text_h/2", line_spacing=L["line_spacing"]))
        last = "c1"
    if settings.show_time_in_caption:
        filters.append(_drawtext(last, "c2", time_txt, font_path, time_font, ff_color(L["time_color"]), "(w-text_w)/2", str(caption_y - time_gap)))
        last = "c2"
    if clip.caption.strip():
        filters.append(_drawtext(last, "c3", caption_txt, font_path, caption_font, ff_color(settings.caption_color), "(w-text_w)/2", str(caption_y), border=max(1, round(L["caption_border"] * k)), line_spacing=L["line_spacing"]))
        last = "c3"
    filters.append(f"[{last}]null[v]")
    audio_src = "0:a" if probe.has_audio else "1:a"
    filters.append(f"[{audio_src}]afade=t=in:d=0.2,afade=t=out:st={max(0.0, dur - 0.2):.2f}:d=0.2[a]")

    cmd: list = [paths.ffmpeg, "-hide_banner"]
    if in_offset > 0:
        cmd += ["-ss", f"{in_offset:g}"]
    cmd += ["-i", source if source is not None else clip.final_path]
    if not probe.has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", profile.preset, "-crf", str(profile.crf), "-r", str(fps), "-pix_fmt", "yuv420p",
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


BGM_FADE_IN = 1.0
BGM_FADE_OUT = 2.0
BGM_MIN_SECTION = 0.5  # 이보다 짧은 구간엔 BGM을 넣지 않는다


def build_mix_command(paths: FfmpegPaths, video: Path, audio: AudioMix, total: float, out: Path) -> list[str]:
    """concat 결과(video)에 원본 볼륨·BGM을 적용해 최종 파일을 만드는 명령. 비디오는 재인코딩하지 않는다.

    BGM 반복 규칙: 첫 재생은 bgm_offset부터 곡 끝까지, 이후 곡 처음부터 반복 (bgm_sync.bgm_position과 동일).
    -stream_loop -1로 무한 반복한 스트림에서 앞 offset초를 잘라내면 정확히 이 동작이 된다.
    """
    orig = f"volume={_clamp(audio.original_volume, 0.0, 1.0):.3f}"
    start = _clamp(audio.bgm_start, 0.0, total)
    end = _clamp(audio.bgm_end if audio.bgm_end is not None else total, start, total)
    section = end - start
    cmd: list = [paths.ffmpeg, "-hide_banner", "-i", video]
    if audio.has_bgm() and section >= BGM_MIN_SECTION:
        cmd += ["-stream_loop", "-1", "-i", audio.bgm_path]
        start_ms = int(round(start * 1000))
        bgm = ",".join(
            [
                f"atrim=start={max(0.0, audio.bgm_offset):.3f}",
                "asetpts=PTS-STARTPTS",
                f"atrim=duration={section:.3f}",
                "asetpts=PTS-STARTPTS",
                f"afade=t=in:d={BGM_FADE_IN:g}",
                f"afade=t=out:st={max(0.0, section - BGM_FADE_OUT):.3f}:d={BGM_FADE_OUT:g}",
                f"volume={_clamp(audio.bgm_volume, 0.0, 1.0):.3f}",
                f"adelay={start_ms}|{start_ms}",
                "apad",
            ]
        )
        filters = f"[0:a]{orig}[a0];[1:a]{bgm}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    else:
        filters = f"[0:a]{orig}[a]"
    cmd += [
        "-filter_complex", filters,
        "-map", "0:v", "-c:v", "copy",
        "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{total:.3f}",
        "-movflags", "+faststart",
        out,
    ]
    return [str(c) for c in cmd]
