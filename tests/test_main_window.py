from dataclasses import replace

import pytest

from stampcut.core.models import AudioMix, Project, Settings
from stampcut.gui import main_window
from stampcut.gui.main_window import MainWindow


def test_main_window_constructs(qtbot):
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.show()
    assert w.windowTitle().startswith("StampCut")
    assert not w.status_panel.render_btn.isEnabled()


def test_analyze_fills_table_and_preview(qtbot, monkeypatch, make_video, make_clip):
    v = make_video()
    clip = make_clip(v, 758, caption="원더골")
    project = Project([v.url], "26.08.20 문성FC 하이라이트", [v], [clip])
    monkeypatch.setattr(main_window.pipeline, "analyze", lambda urls, title, s, client, progress, cancel: project)
    monkeypatch.setattr(main_window.pipeline, "fetch_previews", lambda *a, **k: None)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.url_panel.urls_edit.setPlainText(v.url)
    with qtbot.waitSignal(w.analysis_done, timeout=5000):
        w.start_analysis()
    assert w.model.rowCount() == 1
    assert w.url_panel.title() == "26.08.20 문성FC 하이라이트"
    assert w.preview.clip is clip
    assert "클립 1개" in w.status_panel.summary.text()
    assert w.status_panel.render_btn.isEnabled()


def test_analyze_refuses_invalid_url(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.url_panel.urls_edit.setPlainText("not a url")
    w.start_analysis()
    assert warned and w.url_panel.urls_edit.extraSelections()


def _project_with_clip(make_video, make_clip):
    v = make_video()
    clip = make_clip(v, 758, caption="원더골")
    return Project([v.url], "제목", [v], [clip]), clip


def _load_project(w, project):
    w.project = project
    w.model.set_clips(project.clips)
    w.status_panel.update_summary(project, w.settings)


def test_render_warns_without_ffmpeg(qtbot, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.ffpaths = None
    monkeypatch.setattr(w, "open_settings", lambda: None)
    project, _ = _project_with_clip(make_video, make_clip)
    _load_project(w, project)
    w.start_render()
    assert warned and "ffmpeg" in warned[0]


def test_render_happy_path_and_preview_finish_does_not_clobber(qtbot, monkeypatch, tmp_path, make_video, make_clip):
    from stampcut.core.ffmpeg import FfmpegPaths
    project, clip = _project_with_clip(make_video, make_clip)
    out_calls = []

    def fake_render(proj, s, output_path, downloader, paths, progress, cancel, on_clip_failed=None):
        progress("download", 1, 1, "받기 완료")
        progress("concat", 1, 1, "완료")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        out_calls.append(output_path)
        return output_path

    monkeypatch.setattr(main_window.pipeline, "render", fake_render)
    monkeypatch.setattr(main_window.pipeline, "fetch_previews", lambda *a, **k: None)
    w = MainWindow(Settings(api_key="TEST", output_dir=str(tmp_path / "out")))
    qtbot.addWidget(w)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    w.status_panel.set_output_dir(tmp_path / "out")
    _load_project(w, project)
    with qtbot.waitSignal(w.render_done, timeout=5000):
        w.start_render()
    assert out_calls and out_calls[0].name == "제목.mp4"
    assert w.status_panel.progress.value() == 100 and not w.status_panel.open_folder_btn.isHidden()
    # a preview worker finishing now must not reset the finished render's status
    w._on_previews_finished(None)
    assert w.status_panel.progress.value() == 100 and not w.status_panel.open_folder_btn.isHidden()


def test_preview_progress_is_ignored_while_rendering(qtbot, monkeypatch, make_video, make_clip):
    from stampcut.gui.workers import Worker
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    render_worker = Worker(lambda progress, cancel: None)  # 아직 실행 전 → done=False → 렌더 중으로 간주
    preview_worker = Worker(lambda progress, cancel: None)
    w._render_worker = render_worker
    w.status_panel.set_progress("download", 1, 4, "받는 중")
    assert w.status_panel.progress.value() == 10
    w._on_worker_progress(preview_worker, "preview", 4, 4, "미리보기 완료")
    assert w.status_panel.progress.value() == 10 and w.status_panel.message.text() == "받는 중"
    w._on_worker_progress(render_worker, "render", 50, 100, "렌더링 중")
    assert w.status_panel.progress.value() == 65
    render_worker.done = True
    w._on_worker_progress(preview_worker, "preview", 4, 4, "미리보기 완료")
    assert w.status_panel.progress.value() == 100


def test_settings_action_disabled_while_busy(qtbot):
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    assert w.settings_action.isEnabled()
    w._set_busy(True)
    assert not w.settings_action.isEnabled()
    w._set_busy(False)
    assert w.settings_action.isEnabled()


def test_missing_ffmpeg_hints_and_skips_previews(qtbot, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(main_window, "find_ffmpeg", lambda *a, **k: None)
    fetched = []
    monkeypatch.setattr(main_window.pipeline, "fetch_previews", lambda *a, **k: fetched.append(1))
    project, _ = _project_with_clip(make_video, make_clip)
    monkeypatch.setattr(main_window.pipeline, "analyze", lambda urls, title, s, client, progress, cancel: project)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    assert "ffmpeg" in w.status_panel.message.text()
    w.url_panel.urls_edit.setPlainText(project.videos[0].url)
    with qtbot.waitSignal(w.analysis_done, timeout=5000):
        w.start_analysis()
    assert fetched == []
    assert "ffmpeg" in w.status_panel.message.text()


def test_analyze_reports_skipped_videos(qtbot, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    project, _ = _project_with_clip(make_video, make_clip)
    project.warnings = ["영상을 찾을 수 없어 건너뜀: https://youtu.be/BBBBBBBBBBB"]
    monkeypatch.setattr(main_window.pipeline, "analyze", lambda urls, title, s, client, progress, cancel: project)
    monkeypatch.setattr(main_window.pipeline, "fetch_previews", lambda *a, **k: None)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.url_panel.urls_edit.setPlainText(project.videos[0].url)
    with qtbot.waitSignal(w.analysis_done, timeout=5000):
        w.start_analysis()
    assert infos and "건너뜀" in infos[0]


def test_render_refuses_zero_length_clip(qtbot, monkeypatch, tmp_path, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    from stampcut.core.ffmpeg import FfmpegPaths

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    rendered = []
    monkeypatch.setattr(main_window.pipeline, "render", lambda *a, **k: rendered.append(1))
    v = make_video()
    clip = make_clip(v, 758, caption="원더골", pre=0, post=0)
    w = MainWindow(Settings(api_key="TEST", output_dir=str(tmp_path / "out")))
    qtbot.addWidget(w)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    _load_project(w, Project([v.url], "제목", [v], [clip]))
    w.start_render()
    assert warned and "0초" in warned[0] and rendered == []


def test_left_column_layout_and_full_width_table(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHeaderView
    from stampcut.gui.clip_table import COL_CAPTION, COL_POST, COL_PRE, COL_TIME

    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    left = w.url_panel.parentWidget()
    assert w.table.parentWidget() is left  # URL과 표가 같은 왼쪽 열에
    assert left.minimumWidth() == 420
    assert w.preview.controls_panel.window() is w  # 세부 설정이 메인 창 안에 배치됨
    assert w.preview.parentWidget() is not left  # 미리보기는 오른쪽
    header = w.table.horizontalHeader()
    # 시간·앞·뒤는 편집 가능한 넉넉한 고정 폭, 자막이 남는 폭을 흡수, 나머지는 내용 크기
    fixed_widths = {COL_TIME: 76, COL_PRE: 64, COL_POST: 64}
    for col in range(w.model.columnCount()):
        if col == COL_CAPTION:
            assert header.sectionResizeMode(col) == QHeaderView.Stretch
        elif col in fixed_widths:
            assert header.sectionResizeMode(col) == QHeaderView.Fixed
            assert header.sectionSize(col) == fixed_widths[col]
        else:
            assert header.sectionResizeMode(col) == QHeaderView.ResizeToContents
    assert w.table.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


def test_busy_locks_detached_controls_panel(qtbot):
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w._set_busy(True)
    assert not w.preview.controls_panel.isEnabled()
    w._set_busy(False)
    assert w.preview.controls_panel.isEnabled()


def test_autosave_and_restore_roundtrip(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)  # 실제 다운로드 금지
    pf = tmp_path / "project.json"
    v = make_video()
    clip = make_clip(v, 758, caption="원더골")
    project = Project([v.url], "제목", [v], [clip])

    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    assert w.project is None and not pf.exists()  # 저장 파일이 없으면 새 작업
    w.project = project
    w.model.set_clips(project.clips)
    clip.caption = "수정된 자막"
    clip.pre = 7
    clip.zoom = 1.5
    w._on_clip_edited(clip)  # 편집 → 자동 저장 예약(디바운스)
    assert w._autosave_timer.isActive()
    w._flush_autosave()  # 테스트에서는 즉시 저장
    assert pf.exists()

    w2 = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w2)
    assert w2.project is not None and w2.model.rowCount() == 1
    restored = w2.model.clip_at(0)
    assert (restored.caption, restored.pre, restored.zoom) == ("수정된 자막", 7, 1.5)
    assert w2.url_panel.title() == "제목"
    assert w2.url_panel.urls() == [v.url]
    assert "불러왔" in w2.status_panel.message.text() or "ffmpeg" in w2.status_panel.message.text()


def test_no_project_file_disables_autosave(qtbot, make_video, make_clip):
    v = make_video()
    clip = make_clip(v, 758)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.project = Project([v.url], "제목", [v], [clip])
    w._on_clip_edited(clip)
    assert not w._autosave_timer.isActive()  # project_file 없음 → 예약 자체를 안 함
    w._flush_autosave()  # 예외 없이 무시


def test_close_event_flushes_autosave(qtbot, tmp_path, make_video, make_clip):
    pf = tmp_path / "project.json"
    v = make_video()
    clip = make_clip(v, 758)
    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    w.project = Project([v.url], "제목", [v], [clip])
    w._schedule_autosave()  # 디바운스 대기 중 종료
    w.close()
    assert pf.exists()


def test_style_change_saves_settings(qtbot, monkeypatch):
    saved = []
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: saved.append(s))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.preview.title_y_spin.setValue(300)
    assert saved and saved[-1] is w.settings and w.settings.title_y == 300
    saved.clear()
    w.preview._on_text_drag("caption", -30.0)
    assert not saved  # 드래그 중에는 저장하지 않음
    w.preview._on_text_drag_end()
    assert saved and w.settings.caption_y == 1522


def test_url_edits_are_autosaved(qtbot, tmp_path, make_video, make_clip):
    pf = tmp_path / "project.json"
    v = make_video()
    clip = make_clip(v, 758)
    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    w.project = Project([v.url], "제목", [v], [clip])
    w.url_panel.urls_edit.setPlainText(v.url + "\nhttps://www.youtube.com/watch?v=AAAAAAAAAAA")
    assert w.project.urls == [v.url, "https://www.youtube.com/watch?v=AAAAAAAAAAA"]
    assert w._autosave_timer.isActive()


@pytest.mark.parametrize("fire", ["table", "title", "clip_updated", "rendered"])
def test_every_edit_hook_schedules_autosave(qtbot, tmp_path, monkeypatch, make_video, make_clip, fire):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    pf = tmp_path / "project.json"
    v = make_video()
    clip = make_clip(v, 758)
    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    w.project = Project([v.url], "제목", [v], [clip])
    w.model.set_clips([clip])
    out = tmp_path / "제목.mp4"
    out.write_bytes(b"x")
    if fire == "table":
        w._on_table_changed()
    elif fire == "title":
        w._on_title_changed("새 제목")
    elif fire == "clip_updated":
        w._on_clip_updated(clip)
    else:
        w._render_candidates = {clip.id}
        w._on_rendered(out)
    assert w._autosave_timer.isActive()


def test_bgm_panel_wired_to_project_and_autosave(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    project, clip = _project_with_clip(make_video, make_clip)
    w = MainWindow(Settings(api_key="TEST"), project_file=tmp_path / "project.json")
    qtbot.addWidget(w)
    assert not w.bgm_panel.isEnabled()
    assert w.bgm_panel.parentWidget() is w.url_panel.parentWidget()  # 왼쪽 열
    w._adopt_project(project)
    assert w.bgm_panel.isEnabled() and w.bgm_panel.mix is project.audio
    w._autosave_timer.stop()
    with qtbot.waitSignal(w.bgm_panel.changed, timeout=1000):
        w.bgm_panel.original_slider.setValue(50)
    assert project.audio.original_volume == 0.5 and w._autosave_timer.isActive()
    assert w.preview.audio_mix is project.audio


def test_bgm_dir_change_saves_settings(qtbot, tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: saved.append(s.bgm_dir))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.bgm_panel.bgm_dir_changed.emit(str(tmp_path))
    assert w.settings.bgm_dir == str(tmp_path) and saved == [str(tmp_path)]


def test_full_preview_flow_and_stale_marking(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    from stampcut.core.ffmpeg import FfmpegPaths

    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    project, clip = _project_with_clip(make_video, make_clip)
    full = tmp_path / "full_1.mp4"

    def fake_render_preview(proj, s, paths, progress, cancel):
        progress("preview_render", 100, 100, "합치는 중")
        full.write_bytes(b"x")
        return full

    monkeypatch.setattr(main_window.pipeline, "render_preview", fake_render_preview)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    monkeypatch.setattr(w.preview.player, "setSource", lambda url: None)
    monkeypatch.setattr(w.preview.player, "play", lambda: None)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    w._adopt_project(project)
    with qtbot.waitSignal(w.full_preview_done, timeout=5000):
        w.preview.make_full_btn.click()
    assert w.preview.mode() == "full"
    assert w.preview.full_signature() == main_window.pipeline.preview_signature(project, w.settings)
    assert w.bgm_panel.isEnabled() and not w.preview.controls_panel.isEnabled()  # busy 해제 후에도 전체 모드에선 편집 잠금
    assert "준비됨" in w.status_panel.message.text()
    clip.caption = "바뀐 자막"
    w._on_clip_edited(clip)
    assert "다시 만들기" in w.preview.full_status.text()
    w._adopt_project(_project_with_clip(make_video, make_clip)[0])  # 새 분석 → 전체 미리보기 해제
    assert w.preview.mode() == "clip" and w.preview.full_signature() is None


def test_full_preview_failure_warns_with_hint(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    from stampcut.core.ffmpeg import FfmpegPaths

    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    def failing(proj, s, paths, progress, cancel):
        raise ValueError("미리보기가 준비되지 않은 클립: 3게임 12:38")

    monkeypatch.setattr(main_window.pipeline, "render_preview", failing)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    w._adopt_project(_project_with_clip(make_video, make_clip)[0])
    w.start_full_preview()
    qtbot.waitUntil(lambda: bool(warned), timeout=5000)
    assert "12:38" in warned[0] and "다시 받기" in warned[0]
    assert w.bgm_panel.isEnabled()  # busy 해제됨


def test_full_preview_refused_without_clips_or_ffmpeg(qtbot, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    started = []
    monkeypatch.setattr(main_window.pipeline, "render_preview", lambda *a, **k: started.append(1))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.start_full_preview()
    assert len(warned) == 1 and "클립" in warned[0]
    w.ffpaths = None
    _load_project(w, _project_with_clip(make_video, make_clip)[0])
    w.start_full_preview()
    assert len(warned) == 2 and "ffmpeg" in warned[1] and started == []


def test_render_refuses_missing_bgm_file(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    from stampcut.core.ffmpeg import FfmpegPaths

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    rendered = []
    monkeypatch.setattr(main_window.pipeline, "render", lambda *a, **k: rendered.append(1))
    project, _ = _project_with_clip(make_video, make_clip)
    project.audio = AudioMix(bgm_path=str(tmp_path / "gone.mp3"))
    w = MainWindow(Settings(api_key="TEST", output_dir=str(tmp_path / "out")))
    qtbot.addWidget(w)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    _load_project(w, project)
    w.start_render()
    assert warned and "배경 음악" in warned[0] and rendered == []


def test_restore_fills_bgm_panel_and_busy_locks_it(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    pf = tmp_path / "project.json"
    project, _ = _project_with_clip(make_video, make_clip)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    project.audio = AudioMix(bgm_path=str(song), bgm_volume=0.4, bgm_offset=12.0)
    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    w._adopt_project(project)
    w._flush_autosave()
    w2 = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w2)
    assert w2.project.audio == project.audio
    assert w2.bgm_panel.file_combo.currentText() == "song.mp3" and w2.bgm_panel.bgm_slider.value() == 40
    assert w2.bgm_panel.offset_spin.value() == 12.0
    w2._set_busy(True)
    assert not w2.bgm_panel.isEnabled()
    w2._set_busy(False)
    assert w2.bgm_panel.isEnabled()


def test_listen_and_preview_playback_are_exclusive(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    project, _ = _project_with_clip(make_video, make_clip)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    project.audio = AudioMix(bgm_path=str(song))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    for p in (w.bgm_panel.player, w.preview.player, w.preview.bgm_player):
        monkeypatch.setattr(p, "setSource", lambda url: None)
        monkeypatch.setattr(p, "play", lambda: None)
    w._adopt_project(project)
    paused = []
    monkeypatch.setattr(w.preview, "pause", lambda: paused.append(1))
    w.bgm_panel.listen_btn.setChecked(True)  # BGM만 듣기 시작 → 미리보기 일시정지
    assert paused == [1] and w.bgm_panel.listen_btn.isChecked()
    w.preview.playback_started.emit()  # 미리보기 재생 시작 → BGM만 듣기 정지
    assert not w.bgm_panel.listen_btn.isChecked()


def test_refused_listen_does_not_pause_preview(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    project, _ = _project_with_clip(make_video, make_clip)
    project.audio = AudioMix(bgm_path=str(tmp_path / "gone.mp3"))  # 복원 후 파일이 지워진 상황
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w._adopt_project(project)
    paused = []
    monkeypatch.setattr(w.preview, "pause", lambda: paused.append(1))
    w.bgm_panel.listen_btn.setChecked(True)
    assert not w.bgm_panel.listen_btn.isChecked() and paused == []


def test_busy_pauses_preview_playback(qtbot, monkeypatch):
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    paused = []
    monkeypatch.setattr(w.preview, "pause", lambda: paused.append(1))
    w._set_busy(True)
    w._set_busy(False)
    assert paused == [1]


@pytest.mark.parametrize("edit", ["table", "title", "style", "settings"])
def test_every_stale_hook_marks_preview(qtbot, tmp_path, monkeypatch, make_video, make_clip, edit):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: None)
    project, clip = _project_with_clip(make_video, make_clip)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    monkeypatch.setattr(w.preview.player, "setSource", lambda url: None)
    monkeypatch.setattr(w.preview.player, "play", lambda: None)
    w._adopt_project(project)
    w.preview.set_full_preview(tmp_path / "full.mp4", main_window.pipeline.preview_signature(project, w.settings))
    assert "최신" in w.preview.full_status.text()
    if edit == "table":
        clip.enabled = False
        w._on_table_changed()
    elif edit == "title":
        w._on_title_changed("다른 제목")
    elif edit == "style":
        w.settings.title_y = 100
        w._on_style_changed()
    else:
        w.apply_settings(replace(w.settings, caption_color="#123456"))
    assert "다시 만들기" in w.preview.full_status.text()


def test_channel_finder_requires_api_key(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    w = MainWindow(Settings())
    qtbot.addWidget(w)
    monkeypatch.setattr(w, "open_settings", lambda: None)
    w.open_channel_finder()
    assert warned and "API 키" in warned[0] and w._channel_dialog is None


def test_channel_finder_opens_with_default_ref_and_adds_urls(qtbot, monkeypatch):
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: None)  # 실제 설정 파일을 건드리지 않는다
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.show()
    assert w.channel_action.text() == "채널 영상 찾기" and w.channel_action.isEnabled()
    w.url_panel.urls_edit.setPlainText("https://youtu.be/AAAAAAAAAAA")
    w.open_channel_finder()
    dlg = w._channel_dialog
    assert dlg is not None and dlg.isVisible() and dlg.ref_edit.text() == "https://youtu.be/AAAAAAAAAAA"
    dlg.urls_selected.emit(["https://youtu.be/AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB"])
    assert w.url_panel.urls() == ["https://youtu.be/AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB"]
    assert w.status_panel.message.text() == "URL 1개 추가됨 — 댓글 분석을 누르세요"
    dlg.urls_selected.emit(["https://youtu.be/BBBBBBBBBBB"])
    assert w.status_panel.message.text() == "이미 목록에 있는 영상입니다"
    w.open_channel_finder()
    assert w._channel_dialog is dlg  # 같은 API 키면 창을 재사용
    w.apply_settings(replace(w.settings, api_key="OTHER"))
    w.open_channel_finder()
    assert w._channel_dialog is not dlg  # 키가 바뀌면 새 클라이언트로 다시 만든다
    dlg2 = w._channel_dialog
    w._set_busy(True)
    assert w.channel_action.isEnabled()  # 분석·렌더 중에도 열 수 있다
    w.close()
    assert not dlg2.isVisible()


def test_video_and_settings_split_half_and_half(qtbot):
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    left, preview = w.splitter.widget(0), w.splitter.widget(1)
    assert left is w.url_panel.parentWidget() and preview is w.preview
    a, b = w.splitter.sizes()
    assert abs(a - b) <= 40  # 처음엔 편집 영역과 미리보기가 (레이아웃 여백 오차 안에서) 반반
    assert left.minimumWidth() == 420 and preview.minimumWidth() == 430  # 줄여도 버튼 행이 깨지지 않는 최소 폭
    w.resize(w.width() + 400, w.height())  # 최대화처럼 창이 넓어지면
    qtbot.waitUntil(lambda: w.splitter.sizes()[1] > b, timeout=2000)
    a2, b2 = w.splitter.sizes()
    assert a2 == a and b2 >= b + 390  # 왼쪽은 그대로, 미리보기만 커진다
