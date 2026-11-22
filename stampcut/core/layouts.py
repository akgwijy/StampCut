"""Canvas and video-frame layouts shared by rendering and preview controls."""
from __future__ import annotations

_SHORTS = {
    "canvas_w": 1080,
    "canvas_h": 1920,
    "square": 1080,
    "square_y": 420,
    "frame_w": 1080,
    "frame_h": 1080,
    "frame_x": 0,
    "frame_y": 420,
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
_VIDEO = {
    **_SHORTS,
    "canvas_w": 1920,
    "canvas_h": 1080,
    "frame_w": 1920,
    "frame_h": 1080,
    "frame_x": 0,
    "frame_y": 0,
    "max_text_width": 1800,
}


def layout_for(mode: str) -> dict:
    """Return the active layout, falling back to the legacy Shorts layout."""
    return _VIDEO if mode == "video" else _SHORTS
