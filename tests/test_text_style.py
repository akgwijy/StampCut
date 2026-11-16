from dataclasses import replace
from pathlib import Path

import pytest

from stampcut.core import settings as sm
from stampcut.core.ffmpeg import FfmpegPaths, ProbeInfo
from stampcut.core.models import Project, Settings
from stampcut.core.pipeline import preview_signature
from stampcut.core.renderer import FINAL_PROFILE, PREVIEW_PROFILE, build_clip_command, ff_path
from stampcut.gui.preview_widget import PreviewWidget


def test_independent_styles_save_and_legacy_font_fallback(tmp_path):
    font = tmp_path / 'legacy.otf'
    font.write_bytes(b'font')
    settings = Settings(title_x=420, caption_x=620, title_font_size=88, caption_font_size=44,
                        title_font_path=str(font), caption_font_path=str(font))
    path = tmp_path / 'settings.json'
    sm.save(settings, path)
    assert sm.load(path) == settings
    path.write_text('{"font_path": "legacy.otf"}', encoding='utf-8')
    old = sm.load(path)
    assert (old.title_x, old.caption_x, old.title_font_size, old.caption_font_size) == (540, 540, 64, 60)
    assert sm.resolve_font(old, 'title') == Path('legacy.otf')
    assert sm.resolve_font(old, 'caption') == Path('legacy.otf')
    missing = replace(old, title_font_path=str(tmp_path / 'missing.ttf'))
    assert sm.resolve_font(missing, 'title') == Path('legacy.otf')


@pytest.mark.parametrize(('profile', 'title_size', 'caption_size', 'title_x', 'caption_x'), [
    (FINAL_PROFILE, 88, 44, 420, 620),
    (PREVIEW_PROFILE, 44, 22, 210, 310),
])
def test_render_uses_independent_fonts_sizes_and_positions(tmp_path, make_video, make_clip, profile, title_size, caption_size, title_x, caption_x):
    title_font, caption_font = tmp_path / 'title.otf', tmp_path / 'caption.ttf'
    title_font.write_bytes(b'font')
    caption_font.write_bytes(b'font')
    settings = Settings(title_x=420, caption_x=620, title_font_size=88, caption_font_size=44,
                        title_font_path=str(title_font), caption_font_path=str(caption_font))
    clip = make_clip(make_video(), t=60, final_path=tmp_path / 'source.mp4')
    cmd, _ = build_clip_command(FfmpegPaths(Path('ffmpeg'), Path('ffprobe')), clip, settings,
                                'title', ProbeInfo(1920, 1080, 18, True), tmp_path, 0,
                                tmp_path / 'fallback.otf', profile=profile)
    filters = cmd[cmd.index('-filter_complex') + 1].split(';')
    title = next(f for f in filters if f.endswith('[c1]'))
    caption = next(f for f in filters if f.endswith('[c3]'))
    timestamp = next(f for f in filters if f.endswith('[c2]'))
    assert f"fontfile='{ff_path(title_font)}'" in title
    assert f'fontsize={title_size}:' in title and f'x={title_x}-text_w/2:' in title
    assert f"fontfile='{ff_path(caption_font)}'" in caption
    assert f'fontsize={caption_size}:' in caption and f'x={caption_x}-text_w/2:' in caption
    assert f"fontfile='{ff_path(caption_font)}'" in timestamp and f'x={caption_x}-text_w/2:' in timestamp


@pytest.mark.parametrize('profile', [FINAL_PROFILE, PREVIEW_PROFILE])
def test_wrap_uses_configured_full_resolution_size(tmp_path, make_video, make_clip, profile):
    settings = Settings(title_font_size=120, caption_font_size=120)
    clip = make_clip(make_video(), t=60, caption='가' * 12, final_path=tmp_path / 'source.mp4')
    build_clip_command(FfmpegPaths(Path('ffmpeg'), Path('ffprobe')), clip, settings, '나' * 12,
                       ProbeInfo(1920, 1080, 18, True), tmp_path, 0, tmp_path / 'font.otf', profile=profile)
    assert (tmp_path / 'clip_000_caption.txt').read_text('utf-8') == '가' * 8 + chr(10) + '가' * 4
    assert (tmp_path / 'clip_000_title.txt').read_text('utf-8') == '나' * 8 + chr(10) + '나' * 4


@pytest.mark.parametrize(('field', 'value'), [
    ('title_x', 400), ('caption_x', 620), ('title_font_size', 88),
    ('caption_font_size', 44), ('title_font_path', 'title.otf'), ('caption_font_path', 'caption.otf'),
])
def test_full_preview_invalidates_each_independent_style(make_video, make_clip, field, value):
    video = make_video()
    project = Project([video.url], 'title', [video], [make_clip(video, t=60)])
    assert preview_signature(project, replace(Settings(), **{field: value})) != preview_signature(project, Settings())


def test_controls_update_positions_and_sizes_independently(qtbot, make_video, make_clip):
    settings = Settings()
    widget = PreviewWidget(settings, 'Malgun Gothic')
    qtbot.addWidget(widget)
    widget.set_title('제목')
    widget.set_clip(make_clip(make_video(), t=60))
    widget.caption_scope_combo.setCurrentIndex(1)
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        widget.title_x_spin.setValue(420)
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        widget.caption_x_spin.setValue(620)
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        widget.title_size_spin.setValue(88)
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        widget.caption_size_spin.setValue(44)
    assert (settings.title_x, settings.caption_x) == (420, 620)
    assert (settings.title_font_size, settings.caption_font_size) == (88, 44)
    assert widget.title_item.sceneBoundingRect().center().x() == pytest.approx(420)
    assert widget.caption_item.sceneBoundingRect().center().x() == pytest.approx(620)
    assert widget.time_item.sceneBoundingRect().center().x() == pytest.approx(620)
    assert widget.title_item.font().pixelSize() == 88
    assert widget.caption_item.font().pixelSize() == 44


@pytest.mark.parametrize("face", ["bundled", "malgunbd.ttf"])
def test_independent_font_selection_updates_preview_and_settings(qtbot, make_video, make_clip, face):
    from PySide6.QtGui import QRawFont
    font = sm.bundled_font_path()
    widget = PreviewWidget(Settings(), 'Malgun Gothic')
    qtbot.addWidget(widget)
    widget.set_title('제목')
    widget.set_clip(make_clip(make_video(), t=60))
    picker = widget.title_font_picker
    if face != "bundled":
        index = next((i for i in range(1, picker.combo.count()) if Path(picker.combo.itemData(i)).name.lower() == face), -1)
        if index < 0:
            pytest.skip("Windows font is not installed")
        font = Path(picker.combo.itemData(index))
    else:
        index = picker.combo.findData(str(font))
    assert index > 0
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        picker.combo.setCurrentIndex(index)
    assert widget.settings.title_font_path == str(font)
    assert widget.settings.caption_font_path == ''
    assert QRawFont.fromFont(widget.title_item.font()).weight() == QRawFont(str(font), 64).weight()
    with qtbot.waitSignal(widget.style_changed, timeout=1000):
        picker.combo.setCurrentIndex(0)
    assert widget.settings.title_font_path == ''


def test_text_drag_moves_both_axes_and_clamps(qtbot):
    settings = Settings()
    widget = PreviewWidget(settings, 'Malgun Gothic')
    qtbot.addWidget(widget)
    widget.set_title('title')
    widget._on_text_drag('title', 90, 35)
    assert (settings.title_x, settings.title_y) == (630, 245)
    assert (widget.title_x_spin.value(), widget.title_y_spin.value()) == (630, 245)
    widget._on_text_drag('caption', -3000, -3000)
    assert (settings.caption_x, settings.caption_y) == (0, 0)


def test_settings_sync_new_style_controls_without_emitting(qtbot):
    widget = PreviewWidget(Settings(), 'Malgun Gothic')
    qtbot.addWidget(widget)
    changes = []
    widget.style_changed.connect(lambda: changes.append(1))
    widget.set_settings(Settings(title_x=400, caption_x=650, title_font_size=80, caption_font_size=40))
    assert (widget.title_x_spin.value(), widget.caption_x_spin.value()) == (400, 650)
    assert (widget.title_size_spin.value(), widget.caption_size_spin.value()) == (80, 40)
    assert changes == []


def test_font_file_picker_accepts_file_and_cancels_without_change(qtbot, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from stampcut.gui.font_picker import FontPicker
    picker = FontPicker()
    qtbot.addWidget(picker)
    font = str(sm.bundled_font_path())
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (font, ''))
    with qtbot.waitSignal(picker.path_changed, timeout=1000):
        picker.browse_btn.click()
    assert picker.path() == font
    changes = []
    picker.path_changed.connect(changes.append)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: ('', ''))
    picker.browse_btn.click()
    assert picker.path() == font and changes == []


def test_font_list_distinguishes_regular_and_bold_faces(qtbot):
    from stampcut.gui.font_picker import FontPicker
    picker = FontPicker()
    qtbot.addWidget(picker)
    labels = {Path(picker.combo.itemData(i)).name.lower(): picker.combo.itemText(i) for i in range(1, picker.combo.count())}
    if not {"malgun.ttf", "malgunbd.ttf"}.issubset(labels):
        pytest.skip("Windows font is not installed")
    assert labels["malgun.ttf"] != labels["malgunbd.ttf"]


def test_preview_and_renderer_clamp_sizes_and_x_together(qtbot, tmp_path, make_video, make_clip):
    settings = Settings(title_x=-20, caption_x=10000, title_font_size=0, caption_font_size=999)
    widget = PreviewWidget(settings, "Malgun Gothic")
    qtbot.addWidget(widget)
    widget.set_title("title")
    clip = make_clip(make_video(), t=60, final_path=tmp_path / "source.mp4")
    widget.set_clip(clip)
    assert widget.title_item.font().pixelSize() == 8
    assert widget.caption_item.font().pixelSize() == 240
    assert widget.title_item.sceneBoundingRect().center().x() == pytest.approx(0)
    assert widget.caption_item.sceneBoundingRect().center().x() == pytest.approx(1080)
    cmd, _ = build_clip_command(FfmpegPaths(Path("ffmpeg"), Path("ffprobe")), clip, settings, "title",
                                ProbeInfo(1920, 1080, 18, True), tmp_path, 0, tmp_path / "font.otf")
    filters = cmd[cmd.index("-filter_complex") + 1].split(";")
    title = next(f for f in filters if f.endswith("[c1]"))
    caption = next(f for f in filters if f.endswith("[c3]"))
    assert "fontsize=8:" in title and "x=0-text_w/2:" in title
    assert "fontsize=240:" in caption and "x=1080-text_w/2:" in caption
