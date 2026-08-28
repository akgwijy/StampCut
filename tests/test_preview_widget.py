from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QGraphicsScene

from stampcut.core.models import AudioMix, Settings
from stampcut.core.renderer import compute_square_geometry
from stampcut.gui.preview_widget import PreviewWidget, _DragView, pan_after_drag, seek_target, video_item_placement


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


def test_controls_panel_is_detachable(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    # 편집 컨트롤은 부모 없는 controls_panel 안에 (MainWindow가 왼쪽 열로 가져감)
    assert w.controls_panel.parent() is None
    for widget in (w.zoom_slider, w.pre_spin, w.post_spin, w.caption_edit, w.title_y_spin, w.caption_y_spin, w.reset_btn):
        assert widget.parent() is w.controls_panel or widget.parentWidget() is w.controls_panel
    # 영상 뷰·재생 컨트롤은 PreviewWidget 자신에
    assert w.view.parentWidget() is w
    assert w.play_btn.parentWidget() is w and w.pos_label.parentWidget() is w


def _mouse(type_, pos):
    p = QPointF(pos)
    return QMouseEvent(type_, p, p, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_dragview_routes_text_drag_vs_video_pan(qtbot):
    scene = QGraphicsScene(0, 0, 100, 100)
    calls = []
    target = {"v": "title"}
    view = _DragView(
        scene,
        lambda dx, dy: calls.append(("pan", dx, dy)),
        hit_test=lambda pos: target["v"],
        on_text_drag=lambda kind, dy: calls.append((kind, dy)),
        on_drag_end=lambda: calls.append(("end",)),
    )
    qtbot.addWidget(view)
    view.mousePressEvent(_mouse(QEvent.MouseButtonPress, QPoint(10, 10)))
    view.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(10, 25)))
    view.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, QPoint(10, 25)))
    assert ("title", 15.0) in calls and ("end",) in calls
    assert not [c for c in calls if c[0] == "pan"]
    calls.clear()
    target["v"] = None  # 텍스트 밖 → 영상 팬
    view.mousePressEvent(_mouse(QEvent.MouseButtonPress, QPoint(10, 80)))
    view.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(15, 90)))
    view.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, QPoint(15, 90)))
    assert ("pan", 5.0, 10.0) in calls and ("end",) not in calls


def test_hit_text_finds_title_and_caption(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_title("문성FC 하이라이트")
    w.set_clip(make_clip(make_video(), t=758, caption="원더골"))
    assert w._hit_text(w.title_item.sceneBoundingRect().center()) == "title"
    assert w._hit_text(w.caption_item.sceneBoundingRect().center()) == "caption"
    assert w._hit_text(QPointF(540, 900)) is None  # 정방형 한가운데 = 영상 영역


def test_text_drag_updates_settings_and_emits_on_release(qtbot):
    s = Settings()
    w = PreviewWidget(s, "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_title("문성FC 하이라이트")
    w._on_text_drag("title", 50.0)
    assert s.title_y == 260 and w.title_y_spin.value() == 260  # 스핀박스 동기화
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w._on_text_drag_end()
    w._on_text_drag("caption", -100000.0)
    assert s.caption_y == 0  # 클램프


def test_seek_target_clamps_to_window():
    assert seek_target(5000, 5, 0, 18000) == 10000
    assert seek_target(1000, -5, 0, 18000) == 0
    assert seek_target(17500, 5, 0, 18000) == 17800  # 끝 200ms 앞에서 멈춤
    assert seek_target(60000, -5, 55000, 73000) == 55000
    assert seek_target(0, -1, 0, 0) == 0  # 빈 구간


def test_seek_controls_disabled_without_clip(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    for widget in (w.seek_slider, w.back5_btn, w.back1_btn, w.fwd1_btn, w.fwd5_btn):
        assert not widget.isEnabled()


def test_seek_slider_and_buttons_drive_player(qtbot, monkeypatch, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    clip.preview_start = 700  # 구간: (755-700)s..(773-700)s → 55000..73000ms, 길이 18000
    w.set_clip(clip)
    assert w.seek_slider.maximum() == 18000
    assert w.seek_slider.isEnabled()
    calls = []
    monkeypatch.setattr(w.player, "setPosition", lambda v: calls.append(v))
    w.seek_slider.setValue(3000)  # 사용자 조작 → 구간 시작 + 3초
    assert calls[-1] == 58000
    monkeypatch.setattr(w.player, "position", lambda: 60000)
    w._seek_by(5)
    assert calls[-1] == 65000
    w._seek_by(-5)
    assert calls[-1] == 55000  # 시작 클램프
    w.pre_spin.setValue(5)  # 앞 5초 → 구간 53000..73000, 길이 20000
    assert w.seek_slider.maximum() == 20000


def test_on_position_syncs_slider_without_feedback(qtbot, monkeypatch, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    clip.preview_start = 700
    w.set_clip(clip)
    calls = []
    monkeypatch.setattr(w.player, "setPosition", lambda v: calls.append(v))
    w._on_position(58000)  # 재생 위치 갱신 → 슬라이더만 움직이고 시크(되먹임)는 없어야 함
    assert w.seek_slider.value() == 3000
    assert calls == []
    assert w.pos_label.text() == "0:03 / 0:18"


def test_set_settings_refreshes_seek_range(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    clip.preview_start = 700  # 구간 55000..73000ms
    w.set_clip(clip)
    assert w.seek_slider.maximum() == 18000
    w.set_settings(Settings(pre_seconds=5))  # 전역 앞 5초 → 구간 53000..73000
    assert w.seek_slider.maximum() == 20000


def test_seek_slider_steps_are_in_seconds(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    assert w.seek_slider.singleStep() == 1000
    assert w.seek_slider.pageStep() == 5000


def test_on_position_leaves_slider_alone_while_user_drags(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    clip.preview_start = 700
    w.set_clip(clip)
    w.seek_slider.setSliderDown(True)  # 사용자가 핸들을 잡고 있는 상태
    w.seek_slider.blockSignals(True)
    w.seek_slider.setValue(5000)
    w.seek_slider.blockSignals(False)
    w._on_position(58000)  # 재생 틱이 와도 핸들을 빼앗지 않는다
    assert w.seek_slider.value() == 5000
    assert w.pos_label.text() == "0:03 / 0:18"  # 라벨은 계속 갱신
    w.seek_slider.setSliderDown(False)
    w._on_position(58000)
    assert w.seek_slider.value() == 3000


def _full_file(tmp_path):
    p = tmp_path / "full_x.mp4"
    p.write_bytes(b"x")
    return p


def _widget(qtbot, monkeypatch):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    # 가짜 파일을 실제로 열지 않는다
    monkeypatch.setattr(w.player, "setSource", lambda url: None)
    monkeypatch.setattr(w.player, "play", lambda: None)
    monkeypatch.setattr(w.bgm_player, "setSource", lambda url: None)
    monkeypatch.setattr(w.bgm_player, "play", lambda: None)
    return w


def test_full_mode_switches_output_and_hides_overlays(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    clip = make_clip(make_video(), t=758, caption="원더골")
    w.set_title("제목")
    w.set_clip(clip)
    assert not w.full_mode_btn.isEnabled() and w.mode() == "clip" and w.clip_mode_btn.isChecked()
    w.set_full_preview(_full_file(tmp_path), "sig1")
    assert w.mode() == "full" and w.full_mode_btn.isChecked() and w.full_mode_btn.isEnabled()
    assert w.player.videoOutput() is w.full_item and w.full_item.isVisible() and not w.square.isVisible()
    assert not w.title_item.isVisible() and not w.caption_item.isVisible() and not w.time_item.isVisible()
    assert not w.controls_panel.isEnabled() and w.play_btn.isEnabled() and w.seek_slider.isEnabled()
    assert w.full_signature() == "sig1" and w.full_status.text() == "전체 미리보기 최신"
    w.set_title("다른 제목")  # relayout이 오버레이를 다시 켜면 안 된다
    assert not w.title_item.isVisible()
    w.set_clip(clip)  # 전체 모드에선 행 선택이 재생을 바꾸지 않는다
    assert w.mode() == "full" and w.clip is clip
    w.set_mode("clip")
    assert w.mode() == "clip" and w.player.videoOutput() is w.video_item and w.clip_mode_btn.isChecked()
    assert w.title_item.isVisible() and w.caption_item.isVisible() and w.square.isVisible() and not w.full_item.isVisible()
    assert w.controls_panel.isEnabled()


def test_full_mode_seek_range_follows_duration(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    w.set_clip(make_clip(make_video(), t=758))
    w.set_full_preview(_full_file(tmp_path), "sig")
    w._on_duration(125_000)
    assert w.seek_slider.maximum() == 125_000
    w._on_position(61_000)
    assert w.seek_slider.value() == 61_000 and w.pos_label.text() == "1:01 / 2:05"


def test_stale_and_clear(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    w.set_clip(make_clip(make_video(), t=758))
    w.mark_full_preview_stale()  # 파일 없을 땐 아무 일도 없다
    assert w.full_status.text() == ""
    w.set_full_preview(_full_file(tmp_path), "sig")
    w.mark_full_preview_stale()
    assert "다시 만들기" in w.full_status.text() and w.mode() == "full"
    w.set_full_preview(_full_file(tmp_path), "sig2")  # 다시 만들면 최신
    assert w.full_status.text() == "전체 미리보기 최신" and w.full_signature() == "sig2"
    w.clear_full_preview()
    assert w.mode() == "clip" and not w.full_mode_btn.isEnabled() and w.full_signature() is None and w.full_status.text() == ""


def test_set_audio_mix_applies_volumes_in_full_mode(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    mix = AudioMix(original_volume=0.5, bgm_path=str(song), bgm_volume=0.2)
    w.set_clip(make_clip(make_video(), t=758))
    w.set_audio_mix(mix)
    assert abs(w.audio.volume() - 1.0) < 1e-6  # 클립 모드에선 원본 100%
    w.set_full_preview(_full_file(tmp_path), "sig")
    assert abs(w.audio.volume() - 0.5) < 1e-6 and abs(w.bgm_audio.volume() - 0.2) < 1e-6
    mix.original_volume = 0.8
    mix.bgm_volume = 0.6
    w.set_audio_mix(mix)
    assert abs(w.audio.volume() - 0.8) < 1e-6 and abs(w.bgm_audio.volume() - 0.6) < 1e-6
    w.set_mode("clip")
    assert abs(w.audio.volume() - 1.0) < 1e-6


def test_full_preview_button_emits_request(qtbot, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    with qtbot.waitSignal(w.full_preview_requested, timeout=1000):
        w.make_full_btn.click()


def test_shutdown_stops_bgm_player(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.shutdown()
    assert w.bgm_player.source().isEmpty()


def _sync_setup(qtbot, monkeypatch, tmp_path, bgm_state=QMediaPlayer.PlaybackState.PlayingState, bgm_pos=0):
    """전체 모드 + 재생 중 + BGM 로드 완료 상태를 흉내 낸다. seeks에 setPosition 인자가 쌓인다."""
    w = _widget(qtbot, monkeypatch)
    seeks, plays, pauses = [], [], []
    monkeypatch.setattr(w.bgm_player, "setPosition", lambda ms: seeks.append(ms))
    monkeypatch.setattr(w.bgm_player, "play", lambda: plays.append(1))
    monkeypatch.setattr(w.bgm_player, "pause", lambda: pauses.append(1))
    monkeypatch.setattr(w.bgm_player, "playbackState", lambda: bgm_state)
    monkeypatch.setattr(w.bgm_player, "position", lambda: bgm_pos)
    monkeypatch.setattr(w.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    w.set_clip(make_clip_for_sync(w))
    w.set_audio_mix(AudioMix(bgm_path=str(song), bgm_offset=30.0, bgm_start=10.0, bgm_end=None))
    w.set_full_preview(_full_file(tmp_path), "sig")
    w._on_duration(200_000)      # 전체 미리보기 200초
    w._on_bgm_duration(120_000)  # 곡 120초
    seeks.clear(); plays.clear(); pauses.clear()
    return w, seeks, plays, pauses


def make_clip_for_sync(w):
    from stampcut.core.models import Clip, VideoInfo
    from datetime import datetime, timezone
    v = VideoInfo(0, "POZWcyKFvjY", "https://youtu.be/POZWcyKFvjY", "t", "3게임", "ch", datetime(2026, 8, 20, tzinfo=timezone.utc), 1449, 4)
    return Clip(video=v, t=758, mentions=[], score=1.0, caption="원더골")


def test_sync_starts_bgm_at_mapped_position(qtbot, monkeypatch, tmp_path):
    w, seeks, plays, pauses = _sync_setup(qtbot, monkeypatch, tmp_path, bgm_state=QMediaPlayer.PlaybackState.StoppedState)
    w._sync_bgm(15_000)  # 영상 15초 = 구간 5초 → 곡 35초
    assert seeks == [35_000] and plays == [1] and pauses == []


def test_sync_pauses_outside_section_and_in_clip_mode(qtbot, monkeypatch, tmp_path):
    w, seeks, plays, pauses = _sync_setup(qtbot, monkeypatch, tmp_path)
    w._sync_bgm(5_000)  # bgm_start(10초) 이전
    assert pauses == [1] and seeks == []
    w.set_mode("clip")
    pauses.clear()
    w._sync_bgm(15_000)
    assert pauses == [1] and seeks == []


def test_sync_reseeks_only_beyond_drift_and_after_cooldown(qtbot, monkeypatch, tmp_path):
    from stampcut.gui import preview_widget as pw
    w, seeks, plays, pauses = _sync_setup(qtbot, monkeypatch, tmp_path, bgm_pos=35_000)
    w._sync_bgm(15_100)  # 목표 35.1초, 오차 100ms → 유지
    assert seeks == []
    clock = [1000.0]
    monkeypatch.setattr(pw.time, "monotonic", lambda: clock[0])
    w._sync_bgm(16_000)  # 목표 36초, 오차 1000ms → 재동기
    assert seeks == [36_000]
    clock[0] += 0.1
    w._sync_bgm(16_100)  # 100ms 뒤, seek 착지 전 → 쿨다운으로 재시도 안 함
    assert seeks == [36_000]
    clock[0] += 1.0
    w._sync_bgm(17_200)  # 쿨다운 지남, 오차 여전히 큼 → 재동기
    assert seeks == [36_000, 37_200]


def test_position_tick_in_full_mode_drives_sync(qtbot, monkeypatch, tmp_path):
    w, seeks, plays, pauses = _sync_setup(qtbot, monkeypatch, tmp_path, bgm_state=QMediaPlayer.PlaybackState.StoppedState)
    w._on_position(20_000)
    assert seeks == [40_000] and plays == [1]
