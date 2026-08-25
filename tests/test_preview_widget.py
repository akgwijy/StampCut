from stampcut.core.models import Settings
from stampcut.core.renderer import compute_square_geometry
from stampcut.gui.preview_widget import PreviewWidget, pan_after_drag, video_item_placement


def test_pan_after_drag_zoom_in_moves_video_with_cursor():
    g = compute_square_geometry(1920, 1080, 1.0, 0.5, 0.5)  # sw=1920 > 1080, sh == 1080
    px, py = pan_after_drag(0.5, 0.5, 84, 0, g)
    assert abs(px - 0.4) < 1e-9 and py == 0.5


def test_pan_after_drag_zoom_out_moves_video_with_cursor():
    g = compute_square_geometry(1920, 1080, 0.5, 0.5, 0.5)  # sw=960, sh=540 (패딩)
    px, py = pan_after_drag(0.5, 0.5, 12, 54, g)
    assert abs(px - 0.6) < 1e-9 and abs(py - 0.6) < 1e-9


def test_pan_clamped():
    g = compute_square_geometry(1920, 1080, 1.0, 0.5, 0.5)
    assert pan_after_drag(0.5, 0.5, -100000, 0, g) == (1.0, 0.5)


def test_video_item_placement():
    assert video_item_placement(compute_square_geometry(1920, 1080, 1.0, 0.5, 0.5)) == (-420, 0, 1920, 1080)
    assert video_item_placement(compute_square_geometry(1920, 1080, 0.5, 0.5, 0.5)) == (60, 270, 960, 540)


def test_widget_controls_update_clip(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758, caption="원더골")
    w.set_clip(clip)
    assert (w.pre_spin.value(), w.post_spin.value(), w.zoom_slider.value()) == (3, 15, 100)
    assert w.caption_edit.text() == "원더골"
    assert not w.pre_spin.keyboardTracking() and not w.post_spin.keyboardTracking()
    with qtbot.waitSignal(w.clip_changed, timeout=1000):
        w.zoom_slider.setValue(200)
    assert clip.zoom == 2.0
    with qtbot.waitSignal(w.clip_changed, timeout=1000):
        w.pre_spin.setValue(5)
    assert clip.pre == 5
    with qtbot.waitSignal(w.clip_changed, timeout=1000):
        w.caption_edit.setText("종범 골")
    assert clip.caption == "종범 골"
    with qtbot.waitSignal(w.clip_changed, timeout=1000):
        w.reset_btn.click()
    assert clip.pre is None and clip.post is None and clip.zoom == 1.0 and (clip.pan_x, clip.pan_y) == (0.5, 0.5)
    w.set_clip(None)
    assert not w.pre_spin.isEnabled()


def test_set_settings_updates_background(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_settings(Settings(background_color="#112233"))
    assert w.square.brush().color().name() == "#112233"
    assert w.bg_item.brush().color().name() == "#112233"


def test_zoom_snaps_to_five_hundredths(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    w.set_clip(clip)
    with qtbot.waitSignal(w.clip_changed, timeout=1000):
        w.zoom_slider.setValue(203)
    assert w.zoom_slider.value() == 205 and abs(clip.zoom - 2.05) < 1e-9
    assert w.zoom_slider.singleStep() == 5


def test_shutdown_is_idempotent_and_detaches(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_clip(make_clip(make_video(), t=758))
    w.shutdown()
    w.shutdown()
    assert w.player.videoOutput() is None
    w.close()  # closeEvent → shutdown again, must not raise


def test_style_controls_work_without_clip(qtbot):
    s = Settings()
    w = PreviewWidget(s, "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_title("문성FC 하이라이트")
    assert w.title_y_spin.isEnabled() and w.caption_y_spin.isEnabled()  # 클립 없어도 활성화
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w.title_y_spin.setValue(300)
    assert s.title_y == 300
    r = w.title_item.boundingRect()
    assert abs(w.title_item.pos().y() - (300 - r.height() / 2)) < 1.0  # 세로 중심 기준
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w._set_title_color("#ff0000")
    assert s.title_color == "#ff0000"
    assert w.title_item.brush().color().name() == "#ff0000"


def test_caption_style_moves_caption_and_time(qtbot, make_video, make_clip):
    s = Settings()
    w = PreviewWidget(s, "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_clip(make_clip(make_video(), t=758, caption="원더골"))
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w.caption_y_spin.setValue(1400)
    assert s.caption_y == 1400
    assert w.caption_item.pos().y() == 1400.0          # 상단 기준
    assert w.time_item.pos().y() == 1358.0             # 자막 위 42px
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w._set_caption_color("#00ff00")
    assert w.caption_item.brush().color().name() == "#00ff00"


def test_set_settings_syncs_style_controls(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_settings(Settings(title_y=111, caption_y=1222, title_color="#123456", caption_color="#654321"))
    assert (w.title_y_spin.value(), w.caption_y_spin.value()) == (111, 1222)
    assert w.title_item.brush().color().name() == "#123456"
    assert w.caption_item.brush().color().name() == "#654321"
