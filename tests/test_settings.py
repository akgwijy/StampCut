import json
import sys
from pathlib import Path

from stampcut.core import settings as sm
from stampcut.core.models import Settings


def test_dirs_windows_follow_env(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert sm.settings_path() == tmp_path / "roaming" / "StampCut" / "settings.json"
    assert sm.cache_dir() == tmp_path / "local" / "StampCut" / "cache"
    assert sm.render_dir() == tmp_path / "local" / "StampCut" / "render"
    assert sm.log_dir() == tmp_path / "local" / "StampCut" / "logs"
    assert sm.preview_dir() == tmp_path / "local" / "StampCut" / "preview"


def test_dirs_windows_fall_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert sm.config_dir() == tmp_path / "StampCut"
    assert sm.data_dir() == tmp_path / "StampCut"


def test_dirs_macos_under_application_support(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("APPDATA", str(tmp_path / "ignored"))  # Windows 변수가 있어도 무시
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    base = tmp_path / "Library" / "Application Support" / "StampCut"
    assert sm.settings_path() == base / "settings.json"
    assert sm.cache_dir() == base / "cache"
    assert sm.log_dir() == base / "logs"


def test_dirs_linux_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert sm.config_dir() == tmp_path / "cfg" / "StampCut"
    assert sm.data_dir() == tmp_path / "data" / "StampCut"


def test_dirs_linux_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert sm.config_dir() == tmp_path / ".config" / "StampCut"
    assert sm.data_dir() == tmp_path / ".local" / "share" / "StampCut"


def test_default_output_dir_follows_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert Settings().output_dir == "~/Movies/StampCut"
    monkeypatch.setattr(sys, "platform", "win32")
    assert Settings().output_dir == "~/Videos/StampCut"


def test_font_fallback_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "ASSETS_DIR", tmp_path)  # 동봉 폰트가 없는 상황
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", str(tmp_path / "win"))
    assert sm.bundled_font_path() == tmp_path / "win" / "Fonts" / "malgunbd.ttf"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sm.bundled_font_path() == Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
    monkeypatch.setattr(sys, "platform", "linux")
    assert sm.bundled_font_path().suffix == ".ttc"


def test_load_missing_returns_defaults(tmp_path):
    assert sm.load(tmp_path / "none.json") == Settings()


def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(api_key="KEY", pre_seconds=5, show_time_in_caption=False)
    sm.save(s, p)
    assert sm.load(p) == s
    assert json.loads(p.read_text("utf-8"))["pre_seconds"] == 5


def test_unknown_and_missing_keys(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"post_seconds": 20, "bogus": 1}), "utf-8")
    s = sm.load(p)
    assert s.post_seconds == 20 and s.pre_seconds == 3


def test_corrupt_file_returns_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", "utf-8")
    assert sm.load(p) == Settings()


def test_resolve_font_and_output(tmp_path):
    s = Settings(font_path=str(tmp_path / "f.otf"), output_dir="~/Videos/X")
    assert sm.resolve_font(s) == tmp_path / "f.otf"
    assert sm.resolve_output_dir(s).name == "X" and "~" not in str(sm.resolve_output_dir(s))
    assert sm.resolve_font(Settings()) == sm.bundled_font_path()


def test_app_dir_only_when_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert sm.app_dir() is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe = Path(__file__).resolve().parent / "apps" / "StampCut" / "StampCut.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    assert sm.app_dir() == exe.parent


def test_style_fields_roundtrip_and_legacy_defaults(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(title_y=300, title_color="#ff0000", caption_y=1400, caption_color="#00ff00")
    sm.save(s, p)
    assert sm.load(p) == s
    # 스타일 필드가 없는 구버전 설정 파일 → 기본값 (기존 출력과 동일한 배치)
    p.write_text(json.dumps({"api_key": "K"}), "utf-8")
    old = sm.load(p)
    assert (old.title_y, old.title_color) == (210, "#FFFFFF")
    assert (old.caption_y, old.caption_color) == (1552, "#FFFFFF")


def test_bgm_dir_roundtrip_and_legacy_default(tmp_path):
    p = tmp_path / "s.json"
    sm.save(Settings(bgm_dir=r"D:\music"), p)
    assert sm.load(p).bgm_dir == r"D:\music"
    p.write_text(json.dumps({"api_key": "K"}), "utf-8")
    assert sm.load(p).bgm_dir == ""
