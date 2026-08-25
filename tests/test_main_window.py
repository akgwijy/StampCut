from stampcut.core.models import Project, Settings
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
