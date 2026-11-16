"""설정 파일과 앱 데이터 경로."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, fields
from pathlib import Path

from stampcut.core.models import Settings
from stampcut.core.output_modes import normalize_output_settings

APP_NAME = "StampCut"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def config_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME


def data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME


def settings_path() -> Path:
    return config_dir() / "settings.json"


def cache_dir() -> Path:
    return data_dir() / "cache"


def render_dir() -> Path:
    return data_dir() / "render"


def preview_dir() -> Path:
    return data_dir() / "preview"


def log_dir() -> Path:
    return data_dir() / "logs"


def bundled_font_path() -> Path:
    p = ASSETS_DIR / "fonts" / "Pretendard-Bold.otf"
    if p.exists():
        return p
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "malgunbd.ttf"


def resolve_font(s: Settings, role: str | None = None, fallback: Path | None = None) -> Path:
    """역할별 폰트. 지워지거나 연결 해제된 폰트는 공통 기본 폰트로 되돌린다."""
    override = s.title_font_path if role == "title" else s.caption_font_path if role == "caption" else ""
    if override and Path(override).is_file():
        return Path(override)
    if fallback is not None:
        return fallback
    return Path(s.font_path) if s.font_path else bundled_font_path()


def resolve_output_dir(s: Settings) -> Path:
    return Path(s.output_dir).expanduser()


def app_dir() -> Path | None:
    """PyInstaller로 묶였을 때만 실행 파일 폴더를 돌려준다 (bin\\ffmpeg.exe 탐색용)."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def load(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    known = {f.name for f in fields(Settings)}
    try:
        return normalize_output_settings(Settings(**{k: v for k, v in data.items() if k in known}))
    except TypeError:
        return Settings()


def save(s: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), "utf-8")
