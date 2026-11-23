from pathlib import Path

import pytest

from stampcut.core import renderer
from stampcut.core.ffmpeg import FfmpegPaths, ProbeInfo
from stampcut.core.models import Settings
from stampcut.core.renderer import FINAL_PROFILE, PREVIEW_PROFILE, build_clip_command, compute_frame_geometry, ff_path


def command(tmp_path, clip, settings=None, profile=FINAL_PROFILE, width=1920, height=1080, title='title'):
    clip.final_path = tmp_path / 'source.mp4'
    cmd, out = build_clip_command(
        FfmpegPaths(Path('ffmpeg'), Path('ffprobe')), clip,
        settings if settings is not None else Settings(output_mode='video'), title,
        ProbeInfo(width, height, 18, True), tmp_path, 0, tmp_path / 'font.otf', profile=profile,
    )
    return cmd, out, cmd[cmd.index('-filter_complex') + 1]


def test_layout_exposes_rectangular_frame_and_preserves_legacy_alias():
    from stampcut.core import layouts
    shorts = layouts.layout_for('shorts')
    video = layouts.layout_for('video')
    assert renderer.LAYOUT is shorts
    assert tuple(shorts[k] for k in ('frame_w', 'frame_h', 'frame_x', 'frame_y')) == (1080, 1080, 0, 420)
    assert tuple(video[k] for k in ('canvas_w', 'canvas_h', 'frame_w', 'frame_h', 'frame_x', 'frame_y', 'max_text_width')) == (1920, 1080, 1920, 1080, 0, 0, 1800)
    assert layouts.layout_for('invalid') is shorts


@pytest.mark.parametrize(('width', 'height', 'expected'), [
    (1920, 1080, (1920, 1080, 1920, 1080, 0, 0, 0, 0)),
    (1080, 1920, (608, 1080, 1920, 1080, 656, 0, 0, 0)),
    (640, 480, (1440, 1080, 1920, 1080, 240, 0, 0, 0)),
    (2560, 1080, (1920, 810, 1920, 1080, 0, 136, 0, 0)),
])
def test_contain_keeps_complete_source_at_zoom_one(width, height, expected):
    g = compute_frame_geometry(width, height, 1, .5, .5, 1920, 1080, contain=True)
    assert (g.sw, g.sh, g.pad_w, g.pad_h, g.pad_x, g.pad_y, g.crop_x, g.crop_y) == expected


def test_rectangular_default_retains_height_first_crop():
    g = compute_frame_geometry(2560, 1080, 1, .5, .5, 1920, 1080)
    assert (g.sw, g.sh, g.pad_w, g.pad_h, g.crop_x, g.crop_y) == (2560, 1080, 2560, 1080, 320, 0)
    portrait = compute_frame_geometry(1080, 1920, 1, .5, .5, 1920, 1080)
    assert (portrait.sw, portrait.sh, portrait.pad_x) == (608, 1080, 656)


def test_contain_zoom_and_pan_clamp_to_even_geometry():
    g = compute_frame_geometry(1920, 1080, 2.35, .37, .61, 1920, 1080, contain=True)
    assert (g.sw, g.sh, g.crop_x, g.crop_y) == (4512, 2538, 960, 890)
    edge = compute_frame_geometry(1920, 1080, 99, -1, 2, 1920, 1080, contain=True)
    assert (edge.sw, edge.sh, edge.crop_x, edge.crop_y) == (5760, 3240, 0, 2160)
    small = compute_frame_geometry(1920, 1080, .01, .5, .5, 1920, 1080, contain=True)
    assert (small.sw, small.sh, small.pad_x, small.pad_y) == (960, 540, 480, 270)


@pytest.mark.parametrize('contain', [True, False])
def test_frame_geometry_dimensions_stay_positive_and_even(contain):
    g = compute_frame_geometry(1, 10000, 1, .37, .61, 1920, 1080, contain=contain)
    assert min(g.sw, g.sh, g.pad_w, g.pad_h) >= 2
    assert all(value % 2 == 0 for value in (g.sw, g.sh, g.pad_w, g.pad_h, g.pad_x, g.pad_y, g.crop_x, g.crop_y))


@pytest.mark.parametrize(('profile', 'frame', 'title_pos', 'caption_pos', 'time_y', 'sizes', 'border'), [
    (FINAL_PROFILE, '1920:1080', '960-text_w/2:y=100-text_h/2', '960-text_w/2:y=900', 858, (64, 36, 60), 4),
    (PREVIEW_PROFILE, '960:540', '480-text_w/2:y=50-text_h/2', '480-text_w/2:y=450', 429, (32, 18, 30), 2),
])
def test_video_command_uses_full_frame_and_normalized_text_defaults(tmp_path, make_video, make_clip, profile, frame, title_pos, caption_pos, time_y, sizes, border):
    settings = Settings(output_mode='video')
    cmd, out, fc = command(tmp_path, make_clip(make_video(), t=60), settings, profile)
    assert f'scale={frame},pad={frame}:0:0:color=0x000000,crop={frame}:0:0[sq]' in fc
    assert f's={frame.replace(":", "x")}:r=30:' in fc
    assert '[bg][sq]overlay=0:0[c0]' in fc
    assert f'x={title_pos}:' in fc and f'x={caption_pos}:' in fc
    assert f'y={time_y}:' in fc
    assert all(f'fontsize={size}:' in fc for size in sizes)
    assert f'borderw={border}:' in fc
    assert out == tmp_path / 'clip_000.mp4' and cmd[-1] == str(out)
    assert settings.style_mode == 'shorts' and settings.title_x == 540  # normalization must not mutate callers


@pytest.mark.parametrize(('width', 'height', 'expected'), [
    (1080, 1920, 'scale=608:1080,pad=1920:1080:656:0'),
    (640, 480, 'scale=1440:1080,pad=1920:1080:240:0'),
])
def test_video_command_letterboxes_non_widescreen_sources(tmp_path, make_video, make_clip, width, height, expected):
    _, _, fc = command(tmp_path, make_clip(make_video(), t=60), width=width, height=height)
    assert expected in fc and 'crop=1920:1080:0:0' in fc


def test_video_command_applies_clip_zoom_and_pan(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(), t=60, zoom=2, pan_x=1, pan_y=0)
    _, _, fc = command(tmp_path, clip)
    assert 'scale=3840:2160,pad=3840:2160:0:0:color=0x000000,crop=1920:1080:1920:0' in fc


@pytest.mark.parametrize(('profile', 'tx', 'ty', 'cx', 'cy', 'ts', 'cs', 'time_y'), [
    (FINAL_PROFILE, 1800, 200, 1700, 900, 88, 44, 858),
    (PREVIEW_PROFILE, 900, 100, 850, 450, 44, 22, 429),
])
def test_video_clip_overrides_survive_normalization_and_use_role_fonts(tmp_path, make_video, make_clip, profile, tx, ty, cx, cy, ts, cs, time_y):
    title_font = tmp_path / 'title.otf'
    caption_font = tmp_path / 'caption.otf'
    from stampcut.core.settings import bundled_font_path
    for face in (title_font, caption_font):
        face.write_bytes(bundled_font_path().read_bytes())
    settings = Settings(output_mode='video', title_font_size=88, title_font_path=str(title_font),
                        mode_settings={'video': {'title_x': 1800, 'title_y': 200, 'caption_x': 1000, 'caption_y': 980}})
    clip = make_clip(make_video(), t=60, caption_x=1700, caption_y=900, caption_font_size=44,
                     caption_font_path=str(caption_font), caption_color='#00ff00')
    _, _, fc = command(tmp_path, clip, settings, profile)
    filters = fc.split(';')
    title = next(f for f in filters if f.endswith('[c1]'))
    caption = next(f for f in filters if f.endswith('[c3]'))
    timestamp = next(f for f in filters if f.endswith('[c2]'))
    assert f"fontfile='{ff_path(title_font)}'" in title
    assert f'fontsize={ts}:fontcolor=0xFFFFFF:x={tx}-text_w/2:y={ty}-text_h/2:' in title
    assert f"fontfile='{ff_path(caption_font)}'" in caption
    assert f'fontsize={cs}:fontcolor=0x00ff00:x={cx}-text_w/2:y={cy}:' in caption
    assert f"fontfile='{ff_path(caption_font)}'" in timestamp
    assert f'x={cx}-text_w/2:y={time_y}:' in timestamp
    assert clip.caption_x == 1700 and settings.caption_x == 540


def test_video_text_coordinates_clamp_to_landscape_canvas(tmp_path, make_video, make_clip):
    settings = Settings(output_mode='video', style_mode='video', title_x=5000, title_y=-30,
                        caption_x=-90, caption_y=5000)
    _, _, fc = command(tmp_path, make_clip(make_video(), t=60), settings)
    assert 'x=1920-text_w/2:y=0-text_h/2:' in fc
    assert 'x=0-text_w/2:y=1080:' in fc
    assert 'x=0-text_w/2:y=1038:' in fc


def test_video_preview_and_final_wrap_at_full_resolution_width(tmp_path, make_video, make_clip):
    settings = Settings(output_mode='video', title_font_size=120, caption_font_size=120)
    clip = make_clip(make_video(), t=60, caption='\uac00' * 20)
    expected_caption = '\uac00' * 15 + '\n' + '\uac00' * 5
    expected_title = '\ub098' * 15 + '\n' + '\ub098' * 5
    for index, profile in enumerate((FINAL_PROFILE, PREVIEW_PROFILE)):
        work = tmp_path / str(index)
        work.mkdir()
        command(work, clip, settings, profile, title='\ub098' * 20)
        assert (work / 'clip_000_caption.txt').read_text('utf-8') == expected_caption
        assert (work / 'clip_000_title.txt').read_text('utf-8') == expected_title


def test_multiline_textfile_does_not_create_blank_drawtext_lines(tmp_path):
    from stampcut.core.renderer import _write_text
    path=_write_text(tmp_path/"caption.txt","first line\nsecond line")
    assert path.read_bytes()==b"first line\nsecond line"
