from stampcut.core.models import Project, Settings
from stampcut.gui.status_bar import StatusPanel, overall_percent


def test_overall_percent():
    assert overall_percent("download", 1, 4) == 10
    assert overall_percent("render", 150, 300) == 65
    assert overall_percent("concat", 1, 1) == 100
    assert overall_percent("preview", 2, 4) == 50
    assert overall_percent("analyze", 0, 0) == 0


def test_summary_busy_and_done(qtbot, tmp_path, make_video, make_clip):
    p = StatusPanel()
    qtbot.addWidget(p)
    v = make_video()
    s = Settings(max_total_seconds=30)
    project = Project([v.url], "t", [v], [make_clip(v, 100), make_clip(v, 500)])
    p.update_summary(project, s)
    assert p.summary.text() == "클립 2개 · 총 0:36 / 0:30" and "d00000" in p.summary.styleSheet()
    assert p.render_btn.isEnabled()
    p.set_busy(True)
    assert not p.render_btn.isEnabled()
    p.set_busy(False)
    p.update_summary(project, s)
    assert p.render_btn.isEnabled()
    p.set_progress("render", 150, 300, "렌더링 중")
    assert p.progress.value() == 65 and p.message.text() == "렌더링 중"
    out = tmp_path / "x.mp4"
    p.set_done(out)
    assert p.progress.value() == 100 and not p.open_folder_btn.isHidden() and "x.mp4" in p.message.text()
    p.set_idle("대기")
    assert p.progress.value() == 0 and p.open_folder_btn.isHidden()
    p.set_output_dir(tmp_path)
    assert p.output_dir() == tmp_path and str(tmp_path) in p.output_btn.text()
