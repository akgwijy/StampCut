from stampcut.core.renderer import LAYOUT, compute_square_geometry, _even


def test_layout_constants_match_spec():
    assert (LAYOUT["canvas_w"], LAYOUT["canvas_h"], LAYOUT["square"], LAYOUT["square_y"]) == (1080, 1920, 1080, 420)
    assert (LAYOUT["title_font"], LAYOUT["time_font"], LAYOUT["caption_font"]) == (64, 36, 60)
    assert (LAYOUT["time_y"], LAYOUT["caption_y"], LAYOUT["caption_border"]) == (1510, 1560, 4)
    assert LAYOUT["time_color"] == "#FFD60A" and LAYOUT["fps"] == 30


def test_zoom_one_centered_landscape():
    g = compute_square_geometry(1920, 1080, 1.0, 0.5, 0.5)
    assert (g.sw, g.sh, g.pad_w, g.pad_h) == (1920, 1080, 1920, 1080)
    assert (g.pad_x, g.pad_y, g.crop_x, g.crop_y) == (0, 0, 420, 0)


def test_zoom_two_pan_corners():
    g = compute_square_geometry(1920, 1080, 2.0, 0.0, 1.0)
    assert (g.sw, g.sh) == (3840, 2160)
    assert (g.crop_x, g.crop_y) == (0, 1080)


def test_zoom_out_pads_with_centered_offsets():
    g = compute_square_geometry(1920, 1080, 0.5, 0.5, 0.5)
    assert (g.sw, g.sh, g.pad_w, g.pad_h) == (960, 540, 1080, 1080)
    assert (g.pad_x, g.pad_y, g.crop_x, g.crop_y) == (60, 270, 0, 0)


def test_portrait_source_pads_horizontally():
    g = compute_square_geometry(1080, 1920, 1.0, 0.5, 0.5)
    assert (g.sw, g.sh, g.pad_w, g.pad_h) == (608, 1080, 1080, 1080)
    assert (g.pad_x, g.crop_x) == (236, 0)


def test_values_are_clamped_and_even():
    g = compute_square_geometry(1920, 1080, 9.0, -1.0, 2.0)
    assert g.sh == 3240 and g.crop_x == 0 and g.crop_y == 3240 - 1080
    g2 = compute_square_geometry(1280, 720, 1.0, 0.5, 0.5)
    assert g2.sw % 2 == 0 and g2.sh % 2 == 0


def test_even_rounds_to_nearest_even():
    assert _even(550.8) == 550
    assert _even(551.2) == 552
    assert _even(1080.0) == 1080
    assert _even(607.5) == 608


def test_zoom_051_uses_nearest_even_height():
    g = compute_square_geometry(1920, 1080, 0.51, 0.5, 0.5)
    assert g.sh == 550 and g.sw % 2 == 0
