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
