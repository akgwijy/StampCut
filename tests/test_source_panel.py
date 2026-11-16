from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialogButtonBox
from stampcut.core.models import Project
from stampcut.gui.url_panel import UrlPanel


def period_panel(qtbot):
    from stampcut.gui.source_panel import SourcePanel
    panel=SourcePanel()
    qtbot.addWidget(panel)
    panel.source_combo.setCurrentIndex(1)
    panel.channel_edit.setText("@demo")
    panel.start_date.setDate(QDate(2026,11,1))
    panel.end_date.setDate(QDate(2026,11,15))
    return panel


def test_query_edits_invalidate_confirmed_videos(qtbot,make_video):
    panel=period_panel(qtbot)
    videos=[make_video()]
    panel.set_videos(videos,"demo")
    assert panel.selection_ready() and panel.selected_urls()==[videos[0].url]
    panel.end_date.setDate(QDate(2026,11,16))
    assert not panel.selection_ready() and panel.selected_urls()==[]
    panel.set_videos(videos,"demo")
    panel.channel_edit.setText("@other")
    assert not panel.selection_ready()


def test_invalid_date_order_and_blank_channel_are_rejected(qtbot):
    panel=period_panel(qtbot)
    panel.start_date.setDate(QDate(2026,11,20))
    assert panel.validation_error()
    panel.start_date.setDate(QDate(2026,11,1))
    panel.channel_edit.clear()
    assert panel.validation_error()


def test_period_source_restore_uses_only_confirmed_urls(qtbot,make_video):
    panel=period_panel(qtbot)
    a=make_video(video_id="AAAAAAAAAAA",url="https://youtu.be/AAAAAAAAAAA")
    b=make_video(video_id="BBBBBBBBBBB",url="https://youtu.be/BBBBBBBBBBB")
    project=Project([b.url],"title",[a,b],[],source_kind="channel",source_channel="@demo",
                    source_start="2026-11-01",source_end="2026-11-15")
    panel.restore(project)
    assert panel.selection_ready() and panel.selected_urls()==[b.url]
    assert panel.metadata()=={"source_kind":"channel","source_channel":"@demo","source_start":"2026-11-01","source_end":"2026-11-15"}


def test_url_panel_channel_mode_uses_selection_not_hidden_urls(qtbot,make_video):
    panel=UrlPanel()
    qtbot.addWidget(panel)
    panel.urls_edit.setPlainText("not a url")
    source=panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    video=make_video()
    source.set_videos([video],"demo")
    assert panel.urls()==[video.url]
    assert panel.highlight_invalid() is False
    assert panel.urls_edit.isHidden()
    source.source_combo.setCurrentIndex(0)
    assert panel.urls()==["not a url"]


def test_period_video_confirmation_selects_all_by_default(qtbot,make_video):
    from stampcut.gui.period_video_dialog import PeriodVideoDialog
    from stampcut.gui.channel_models import V_CHECK
    videos=[make_video(video_id="AAAAAAAAAAA",url="https://youtu.be/AAAAAAAAAAA"),make_video(video_id="BBBBBBBBBBB",url="https://youtu.be/BBBBBBBBBBB")]
    dialog=PeriodVideoDialog(videos,"demo")
    qtbot.addWidget(dialog)
    assert dialog.selected_videos()==videos
    dialog.model.setData(dialog.model.index(0,V_CHECK),Qt.Unchecked,Qt.CheckStateRole)
    assert dialog.selected_videos()==[videos[1]]
    dialog.set_all_checked(False)
    assert dialog.selected_videos()==[]
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
