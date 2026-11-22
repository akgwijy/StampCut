"""Preserve independent output-mode styles without changing shared API/audio preferences."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
from math import isfinite
from typing import Iterable
from stampcut.core.models import CAPTION_STYLE_FIELDS, Clip, Settings

MODES = ("shorts", "video")
STYLE_FIELDS = ("title_x", "title_y", "title_font_size", "title_font_path", "title_color",
                *CAPTION_STYLE_FIELDS, "max_total_seconds", "background_color", "show_time_in_caption")
CLIP_FIELDS = (*CAPTION_STYLE_FIELDS, "zoom", "pan_x", "pan_y")
COORDINATES = {
    "shorts": {"title_x": 540, "title_y": 210, "caption_x": 540, "caption_y": 1552},
    "video": {"title_x": 960, "title_y": 100, "caption_x": 960, "caption_y": 900},
}
_INT_FIELDS = {"title_x", "title_y", "title_font_size", "caption_x", "caption_y", "caption_font_size", "max_total_seconds"}
_FLOAT_FIELDS = {"zoom", "pan_x", "pan_y"}


def _profiles(value) -> dict:
    return deepcopy(value) if isinstance(value, dict) else {}


def _valid_profile(value, fields, *, clip=False) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for name in fields:
        if name not in value:
            continue
        item = value[name]
        if clip and name in CAPTION_STYLE_FIELDS and item is None:
            result[name] = item
        elif name in _INT_FIELDS and type(item) is int:
            result[name] = item
        elif name in _FLOAT_FIELDS and type(item) in (int, float) and isfinite(item):
            result[name] = item
        elif name == "show_time_in_caption" and type(item) is bool:
            result[name] = item
        elif name not in _INT_FIELDS | _FLOAT_FIELDS | {"show_time_in_caption"} and isinstance(item, str):
            result[name] = item
    return result


def switch_output_mode(settings: Settings, mode: str, clips: Iterable[Clip] = ()) -> Settings:
    """Switch active layout, preserving both modes' common styles and clip edits."""
    mode = mode if mode in MODES else "shorts"
    old = settings.style_mode if settings.style_mode in MODES else "shorts"
    profiles = _profiles(settings.mode_settings)
    if old == mode:
        if settings.output_mode == mode and settings.style_mode == mode and isinstance(settings.mode_settings, dict):
            return settings
        return replace(settings, output_mode=mode, style_mode=mode, mode_settings=profiles)
    profiles[old] = {name: getattr(settings, name) for name in STYLE_FIELDS}
    values = {name: getattr(settings, name) for name in STYLE_FIELDS}
    values.update(COORDINATES[mode])
    values.update(_valid_profile(profiles.get(mode), STYLE_FIELDS))
    result = replace(settings, output_mode=mode, style_mode=mode, mode_settings=profiles, **values)
    for clip in clips:
        saved = _profiles(clip.mode_settings)
        saved[old] = {name: getattr(clip, name) for name in CLIP_FIELDS}
        active = {name: getattr(clip, name) for name in CAPTION_STYLE_FIELDS}
        active.update(caption_x=None, caption_y=None, zoom=1.0, pan_x=0.5, pan_y=0.5)
        active.update(_valid_profile(saved.get(mode), CLIP_FIELDS, clip=True))
        for name, value in active.items():
            setattr(clip, name, value)
        clip.mode_settings = saved
    return result


def normalize_output_settings(settings: Settings) -> Settings:
    return switch_output_mode(settings, settings.output_mode)
