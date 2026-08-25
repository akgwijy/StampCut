"""출력 레이아웃 상수, 정방형 기하, ffmpeg 명령 생성."""
from __future__ import annotations

from dataclasses import dataclass

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
