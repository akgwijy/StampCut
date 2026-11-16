from dataclasses import replace
import json
from pathlib import Path

import pytest

from stampcut.core import project_io
from stampcut.core.models import Project, Settings
from stampcut.core.pipeline import preview_signature
from stampcut.core.ffmpeg import FfmpegPaths, ProbeInfo
from stampcut.core.renderer import FINAL_PROFILE, PREVIEW_PROFILE, build_clip_command, ff_path
from stampcut.core.settings import bundled_font_path
from stampcut.gui.preview_widget import PreviewWidget

FIELDS = ("caption_x", "caption_y", "caption_font_size", "caption_font_path", "caption_color")


def test_per_field_inheritance_does_not_mutate_common_settings(make_video, make_clip):
    settings = Settings(caption_x=600, caption_y=1500, caption_font_size=70, caption_font_path="shared.ttf")
    a = make_clip(make_video(), 60, caption_x=0, caption_font_path="")
    b = make_clip(make_video(), 90)
    effective = a.caption_settings(settings)
    assert (effective.caption_x, effective.caption_y, effective.caption_font_size, effective.caption_font_path) == (0, 1500, 70, "")
    assert settings.caption_x == 600 and settings.caption_font_path == "shared.ttf"
    assert b.caption_settings(settings).caption_x == 600
    settings.caption_y = 1450
    assert a.caption_settings(settings).caption_y == 1450


def test_project_roundtrip_preserves_per_clip_styles_and_explicit_empty_font(tmp_path, make_video, make_clip):
    video = make_video()
    a = make_clip(video, 60, caption_x=0, caption_y=1300, caption_font_size=40,
                  caption_font_path="", caption_color="#123456")
    b = make_clip(video, 90)
    path = tmp_path / "project.json"
    project_io.save(Project([], "title", [video], [a, b]), path)
    restored = project_io.load(path)
    assert restored is not None
    a, b = restored.clips
    assert (a.caption_x, a.caption_y, a.caption_font_size, a.caption_font_path, a.caption_color) == (0, 1300, 40, "", "#123456")
    assert all(getattr(b, field) is None for field in FIELDS)
    assert a.caption_settings(Settings(caption_font_path="shared.ttf")).caption_font_path == ""


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_projects_inherit_common_caption_style(tmp_path, make_video, make_clip, version):
    video = make_video()
    path = tmp_path / "legacy.json"
    project_io.save(Project([], "title", [video], [make_clip(video, 60)]), path)
    data = json.loads(path.read_text("utf-8"))
    data["version"] = version
    for field in FIELDS:
        data["clips"][0].pop(field, None)
    path.write_text(json.dumps(data), encoding="utf-8")
    restored = project_io.load(path)
    assert restored is not None
    settings = Settings(caption_x=620, caption_y=1400)
    assert restored.clips[0].caption_settings(settings).caption_x == 620
    assert restored.clips[0].caption_settings(settings).caption_y == 1400


@pytest.mark.parametrize("profile", [FINAL_PROFILE, PREVIEW_PROFILE])
def test_renderer_uses_selected_clip_style_without_leaking_to_next(tmp_path, make_video, make_clip, profile):
    font = tmp_path / "clip.otf"
    font.write_bytes(b"font")
    settings = Settings()
    video = make_video()
    clips = [make_clip(video, 60, caption_x=400, caption_y=1200, caption_font_size=80,
                       caption_font_path=str(font), caption_color="#123456"), make_clip(video, 90)]
    filters = []
    for index, clip in enumerate(clips):
        clip.final_path = tmp_path / "source.mp4"
        cmd, _ = build_clip_command(FfmpegPaths(Path("ffmpeg"), Path("ffprobe")), clip, settings,
                                    "title", ProbeInfo(1920, 1080, 18, True), tmp_path, index,
                                    tmp_path / "base.otf", profile=profile)
        filters.append(cmd[cmd.index("-filter_complex") + 1])
    scale = profile.scale
    assert f"fontfile='{ff_path(font)}'" in filters[0]
    assert f"x={round(400 * scale)}-text_w/2" in filters[0]
    assert f"y={round(1200 * scale)}:" in filters[0]
    assert f"fontsize={round(80 * scale)}:" in filters[0]
    assert "fontcolor=0x123456" in filters[0]
    assert str(font.name) not in filters[1]
    assert f"x={round(540 * scale)}-text_w/2" in filters[1]
    assert f"y={round(1552 * scale)}:" in filters[1]
    assert settings.caption_x == 540


@pytest.mark.parametrize(("field", "value"), [
    ("caption_x", 300), ("caption_y", 1300), ("caption_font_size", 40),
    ("caption_font_path", "clip.otf"), ("caption_color", "#123456"),
])
def test_each_clip_override_invalidates_full_preview(make_video, make_clip, field, value):
    video = make_video()
    clip = make_clip(video, 60)
    project = Project([], "title", [video], [clip])
    before = preview_signature(project, Settings())
    setattr(clip, field, value)
    assert preview_signature(project, Settings()) != before


def test_edit_switch_and_restore_only_selected_clip(qtbot, make_video, make_clip):
    settings = Settings()
    widget = PreviewWidget(settings, "Malgun Gothic")
    qtbot.addWidget(widget)
    a, b = make_clip(make_video(), 60), make_clip(make_video(), 90)
    clip_changes, common_changes = [], []
    widget.clip_changed.connect(clip_changes.append)
    widget.style_changed.connect(lambda: common_changes.append(1))
    widget.set_clip(a)
    widget.caption_x_spin.setValue(300)
    widget.caption_y_spin.setValue(1250)
    widget.caption_size_spin.setValue(42)
    widget._set_caption_color("#123456")
    assert (settings.caption_x, settings.caption_y, settings.caption_font_size, settings.caption_color) == (540, 1552, 60, "#FFFFFF")
    assert (a.caption_x, a.caption_y, a.caption_font_size, a.caption_color) == (300, 1250, 42, "#123456")
    picker = widget.caption_font_picker
    font = str(bundled_font_path())
    picker.combo.setCurrentIndex(picker.combo.findData(font))
    assert a.caption_font_path == font
    widget.set_clip(b)
    assert (widget.caption_x_spin.value(), widget.caption_y_spin.value(), widget.caption_size_spin.value()) == (540, 1552, 60)
    assert widget.caption_font_picker.path() == ""
    widget.set_clip(a)
    assert (widget.caption_x_spin.value(), widget.caption_y_spin.value(), widget.caption_size_spin.value()) == (300, 1250, 42)
    assert widget.caption_font_picker.path() == font
    assert widget.caption_item.sceneBoundingRect().center().x() == pytest.approx(300)
    assert common_changes == [] and len(clip_changes) == 5
    assert all(clip is a for clip in clip_changes)
    assert b.caption_x is None


def test_reset_caption_only_restores_inherited_values(qtbot, make_video, make_clip):
    widget = PreviewWidget(Settings(caption_y=1500), "Malgun Gothic")
    qtbot.addWidget(widget)
    clip = make_clip(make_video(), 60, caption_x=300, caption_y=1200, caption_font_size=40,
                     caption_font_path="", caption_color="#123456", pre=8, zoom=2.0)
    widget.set_clip(clip)
    with qtbot.waitSignal(widget.clip_changed, timeout=1000):
        widget.caption_reset_btn.click()
    assert all(getattr(clip, field) is None for field in FIELDS)
    assert (widget.caption_x_spin.value(), widget.caption_y_spin.value()) == (540, 1500)
    assert clip.pre == 8 and clip.zoom == 2.0
    assert not widget.caption_reset_btn.isEnabled()


def test_common_scope_still_edits_defaults_without_overwriting_clip(qtbot, make_video, make_clip):
    settings = Settings()
    widget = PreviewWidget(settings, "Malgun Gothic")
    qtbot.addWidget(widget)
    clip = make_clip(make_video(), 60, caption_x=300)
    widget.set_clip(clip)
    widget.caption_scope_combo.setCurrentIndex(1)
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        widget.caption_x_spin.setValue(650)
    assert settings.caption_x == 650 and clip.caption_x == 300
    widget.caption_scope_combo.setCurrentIndex(0)
    assert widget.caption_x_spin.value() == 300
    with qtbot.waitSignal(widget.clip_changed, timeout=1000):
        widget.caption_reset_btn.click()
    assert widget.caption_x_spin.value() == 650


def test_caption_drag_saves_selected_clip_on_release(qtbot, make_video, make_clip):
    settings = Settings()
    widget = PreviewWidget(settings, "Malgun Gothic")
    qtbot.addWidget(widget)
    clip = make_clip(make_video(), 60)
    widget.set_clip(clip)
    changes, globals_ = [], []
    widget.clip_changed.connect(changes.append)
    widget.style_changed.connect(lambda: globals_.append(1))
    widget._on_text_drag("caption", 100, -100)
    assert (clip.caption_x, clip.caption_y) == (640, 1452)
    assert (settings.caption_x, settings.caption_y) == (540, 1552)
    assert changes == [] and globals_ == []
    widget._on_text_drag_end()
    assert changes == [clip] and globals_ == []


def test_clip_caption_edits_autosave_project_not_user_defaults(qtbot, monkeypatch, tmp_path, make_video, make_clip):
    from stampcut.gui import main_window
    saved_settings = []
    monkeypatch.setattr(main_window.settings_mod, "save", lambda *a, **k: saved_settings.append(1))
    path = tmp_path / "project.json"
    window = main_window.MainWindow(Settings(), project_file=path)
    qtbot.addWidget(window)
    video = make_video()
    clip = make_clip(video, 60)
    window.project = Project([], "title", [video], [clip])
    window.model.set_clips([clip])
    window.preview.set_clip(clip)
    window.preview.caption_x_spin.setValue(300)
    window._flush_autosave()
    restored = project_io.load(path)
    assert restored is not None and restored.clips[0].caption_x == 300
    assert window.settings.caption_x == 540 and saved_settings == []


@pytest.mark.parametrize("deselect", [False, True])
def test_new_clip_selection_returns_to_clip_scope(qtbot, make_video, make_clip, deselect):
    settings = Settings()
    widget = PreviewWidget(settings, "Malgun Gothic")
    qtbot.addWidget(widget)
    a, b = make_clip(make_video(), 60), make_clip(make_video(), 90)
    widget.set_clip(a)
    widget.caption_scope_combo.setCurrentIndex(1)
    if deselect:
        widget.set_clip(None)
    widget.set_clip(b)
    widget.caption_x_spin.setValue(300)
    assert settings.caption_x == 540
    assert b.caption_x == 300 and a.caption_x is None
    assert widget.caption_scope_combo.currentIndex() == 0


@pytest.mark.parametrize("common", [False, True])
def test_accepting_current_caption_color_keeps_active_scope_color(qtbot, monkeypatch, make_video, make_clip, common):
    from PySide6.QtWidgets import QColorDialog
    settings = Settings(caption_color="#abcdef")
    widget = PreviewWidget(settings, "Malgun Gothic")
    qtbot.addWidget(widget)
    clip = make_clip(make_video(), 60, caption_color="#123456")
    widget.set_clip(clip)
    if common:
        widget.caption_scope_combo.setCurrentIndex(1)
    monkeypatch.setattr(QColorDialog, "getColor", lambda current, *args, **kwargs: current)
    widget._on_caption_color()
    assert clip.caption_color == "#123456"
    assert settings.caption_color == "#abcdef"
