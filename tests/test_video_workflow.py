from datetime import datetime, timezone
import json

import pytest
import responses
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog
from stampcut.core import project_io
from stampcut.core.models import ChannelInfo, Project, RawComment, Settings
from stampcut.gui import main_window


def window(qtbot,monkeypatch,settings=None,project_file=None):
    monkeypatch.setattr(main_window.settings_mod,"save",lambda *a,**k:None)
    monkeypatch.setattr(main_window.MainWindow,"start_previews",lambda *a,**k:None)
    widget=main_window.MainWindow(settings or Settings(api_key="TEST"),project_file=project_file)
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget,"_warn",lambda message:None)
    return widget


def test_switch_updates_preview_and_preserves_clip_edits(qtbot,monkeypatch,make_video,make_clip):
    widget=window(qtbot,monkeypatch)
    video=make_video()
    clip=make_clip(video,60,caption_x=300,caption_y=1400,zoom=1.5)
    widget.project=Project([video.url],"title",[video],[clip])
    widget.model.set_clips([clip])
    widget.table.selectRow(0)
    widget.output_mode_combo.setCurrentIndex(1)
    assert widget.settings.output_mode==widget.project.output_mode=="video"
    assert (widget.preview.scene.width(),widget.preview.scene.height())==(1920,1080)
    assert widget.preview.title_x_spin.maximum()==1920
    assert widget.preview.caption_y_spin.maximum()==1080
    widget.preview.caption_x_spin.setValue(1200)
    widget.output_mode_combo.setCurrentIndex(0)
    assert (widget.preview.scene.width(),widget.preview.scene.height())==(1080,1920)
    assert (clip.caption_x,clip.caption_y,clip.zoom)==(300,1400,1.5)
    widget.output_mode_combo.setCurrentIndex(1)
    assert clip.caption_x==1200


def test_saved_channel_video_project_restores_mode_and_source(qtbot,monkeypatch,tmp_path,make_video,make_clip):
    video=make_video()
    clip=make_clip(video,60,caption_x=1400,caption_y=800)
    project=Project([video.url],"title",[video],[clip],output_mode="video",source_kind="channel",
                    source_channel="@demo",source_start="2026-11-01",source_end="2026-11-15")
    path=tmp_path/"project.json"
    project_io.save(project,path)
    widget=window(qtbot,monkeypatch,project_file=path)
    source=widget.url_panel.source_panel
    assert widget.settings.output_mode=="video" and widget.output_mode_combo.currentData()=="video"
    assert (widget.preview.scene.width(),widget.preview.scene.height())==(1920,1080)
    assert widget.preview.caption_x_spin.value()==1400
    assert source.selection_ready() and source.selected_urls()==[video.url]
    assert widget.url_panel.urls_edit.isHidden()
    assert widget.project.source_kind=="channel" and widget.project.source_start=="2026-11-01"


def test_channel_lookup_confirmation_and_all_comment_analysis(qtbot,monkeypatch,make_video):
    videos=[make_video(video_id="AAAAAAAAAAA",url="https://youtu.be/AAAAAAAAAAA",published_at=datetime(2026,11,5,tzinfo=timezone.utc)),
            make_video(video_id="BBBBBBBBBBB",url="https://youtu.be/BBBBBBBBBBB",published_at=datetime(2026,11,7,tzinfo=timezone.utc))]
    comments=[]
    class Client:
        def resolve_channel(self,text):
            return ChannelInfo("UC"+"a"*22,"demo","UU"+"a"*22)
        def fetch_channel_videos(self,channel,limit=200,page_token=None):
            return videos,None
        def fetch_video_infos(self,urls,strict=False):
            return [v for v in videos if v.url in urls]
        def fetch_all_comments(self,video_id):
            comments.append(video_id)
            return [RawComment(video_id,"1:00 highlight","author",3,False)]
    monkeypatch.setattr(main_window,"YouTubeClient",lambda key:Client())
    from stampcut.gui.period_video_dialog import PeriodVideoDialog
    monkeypatch.setattr(PeriodVideoDialog,"exec",lambda self:QDialog.Accepted)
    widget=window(qtbot,monkeypatch)
    monkeypatch.setattr(widget.pool,"start",lambda worker:worker.run())
    widget.output_mode_combo.setCurrentIndex(1)
    source=widget.url_panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    source.start_date.setDate(QDate(2026,11,1))
    source.end_date.setDate(QDate(2026,11,15))
    source.find_btn.click()
    assert source.selection_ready() and len(source.selected_videos)==2
    widget.start_analysis()
    assert comments==["AAAAAAAAAAA","BBBBBBBBBBB"]
    assert widget.project is not None and len(widget.project.clips)==2
    assert widget.project.output_mode=="video"
    assert widget.project.source_kind=="channel" and widget.project.source_end=="2026-11-15"


def test_query_requires_new_confirmation_after_dates_change(qtbot,monkeypatch,make_video):
    widget=window(qtbot,monkeypatch)
    errors=[]
    monkeypatch.setattr(widget,"_warn",errors.append)
    source=widget.url_panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    source.set_videos([make_video()],"demo")
    source.end_date.setDate(source.end_date.date().addDays(1))
    started=[]
    monkeypatch.setattr(widget,"_start",started.append)
    widget.start_analysis()
    assert errors and started==[]


def test_lookup_cancel_locks_controls_and_stops_before_requests(qtbot,monkeypatch):
    widget=window(qtbot,monkeypatch)
    queued=[]
    monkeypatch.setattr(widget.pool,"start",queued.append)
    source=widget.url_panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    source.find_btn.click()
    assert len(queued)==1
    assert not widget.output_mode_combo.isEnabled() and not source.isEnabled()
    widget.status_panel.cancel_btn.click()
    assert queued[0].cancel.is_set()
    queued[0].run()
    assert widget.output_mode_combo.isEnabled() and source.isEnabled()


def test_background_preview_cannot_overwrite_channel_query_status(qtbot,monkeypatch):
    from stampcut.gui.workers import Worker
    widget=window(qtbot,monkeypatch)
    queued=[]
    monkeypatch.setattr(widget.pool,"start",queued.append)
    source=widget.url_panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    source.find_btn.click()
    queued[0].done=True
    message=widget.status_panel.message.text()
    widget._on_previews_finished(None)
    assert widget.status_panel.message.text()==message
    background=Worker(lambda progress,cancel:None)
    widget._on_worker_progress(background,"preview",1,1,"background")
    assert widget.status_panel.message.text()==message


@pytest.mark.parametrize("arrival", ["before_confirmation", "during_confirmation"])
def test_legacy_finder_urls_survive_pending_period_result(qtbot, monkeypatch, make_video, arrival):
    from stampcut.core.channel import ChannelPage

    widget = window(qtbot, monkeypatch)
    queued = []
    monkeypatch.setattr(widget.pool, "start", queued.append)
    source = widget.url_panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    source.find_btn.click()
    assert len(queued) == 1 and widget.channel_action.isEnabled()
    video_a = make_video(video_id="AAAAAAAAAAA", url="https://youtu.be/AAAAAAAAAAA")
    url_b = "https://youtu.be/BBBBBBBBBBB"
    page = ChannelPage(ChannelInfo("UC" + "a" * 22, "demo", "UU" + "a" * 22), [video_a], None)
    confirmations = []

    def confirm(dialog):
        confirmations.append(True)
        if arrival == "during_confirmation":
            widget._on_channel_urls([url_b])
        return QDialog.Accepted

    monkeypatch.setattr(main_window.PeriodVideoDialog, "exec", confirm)
    if arrival == "before_confirmation":
        widget._on_channel_urls([url_b])
    # Emit the pending worker's result through the real GUI callback connection.
    queued[0].done = True
    queued[0].signals.finished.emit(page)
    assert widget.url_panel.urls() == [url_b]
    assert widget.url_panel.urls_edit.toPlainText() == url_b
    assert not source.is_channel() and not source.selection_ready()
    assert confirmations == ([] if arrival == "before_confirmation" else [True])


def test_period_confirmation_rejects_query_changed_in_dialog(qtbot, monkeypatch, make_video):
    from stampcut.core.channel import ChannelPage

    widget = window(qtbot, monkeypatch)
    queued = []
    monkeypatch.setattr(widget.pool, "start", queued.append)
    source = widget.url_panel.source_panel
    source.source_combo.setCurrentIndex(1)
    source.channel_edit.setText("@demo")
    source.find_btn.click()
    video = make_video()
    page = ChannelPage(ChannelInfo("UC" + "a" * 22, "demo", "UU" + "a" * 22), [video], None)

    def confirm(dialog):
        source.end_date.setDate(source.end_date.date().addDays(1))
        return QDialog.Accepted

    monkeypatch.setattr(main_window.PeriodVideoDialog, "exec", confirm)
    queued[0].done = True
    queued[0].signals.finished.emit(page)
    assert not source.selection_ready() and widget.url_panel.urls() == []
    assert widget.url_panel.urls_edit.toPlainText() == ""


@pytest.mark.parametrize("last_resource", ["commentThreads", "comments"])
@responses.activate
def test_cancel_during_final_comment_request_preserves_project(qtbot, monkeypatch, make_video, last_resource):
    from stampcut.core.youtube_api import BASE_URL, YouTubeClient

    video = make_video()
    client = YouTubeClient("TEST")
    monkeypatch.setattr(main_window, "YouTubeClient", lambda key: client)
    widget = window(qtbot, monkeypatch)
    widget.ffpaths = object()  # Make any accidental adoption attempt start previews.
    previous = Project([], "keep existing", [], [])
    widget.project = previous
    widget.url_panel.urls_edit.setPlainText(video.url)
    queued, previews, cancelled, finished = [], [], [], []
    monkeypatch.setattr(widget.pool, "start", queued.append)
    monkeypatch.setattr(widget, "start_previews", previews.append)
    responses.get(f"{BASE_URL}/videos", json={"items": [{
        "id": video.video_id,
        "snippet": {"title": "demo", "channelTitle": "demo", "publishedAt": "2026-11-01T00:00:00Z"},
        "contentDetails": {"duration": "PT5M"},
        "statistics": {"commentCount": "2"},
    }]})
    comment = {"id": "top", "snippet": {"textOriginal": "1:00 highlight", "authorDisplayName": "author", "likeCount": 3}}
    thread = {"id": "thread", "snippet": {"topLevelComment": comment, "totalReplyCount": 1 if last_resource == "comments" else 0},
              "replies": {"comments": []}}
    final_payload = {"items": [thread]}
    if last_resource == "comments":
        responses.get(f"{BASE_URL}/commentThreads", json=final_payload)
        final_payload = {"items": [{"id": "reply", "snippet": {"textOriginal": "2:00 reply", "authorDisplayName": "reply author", "likeCount": 1}}]}

    def finish_request(request):
        widget.cancel_work()
        return 200, {}, json.dumps(final_payload)

    responses.add_callback(responses.GET, f"{BASE_URL}/{last_resource}", callback=finish_request, content_type="application/json")
    widget.start_analysis()
    worker = queued[0]
    worker.signals.cancelled.connect(lambda: cancelled.append(True))
    worker.signals.finished.connect(finished.append)
    worker.run()
    assert widget.project is previous and previews == []
    assert cancelled == [True] and finished == []
    assert "취소" in widget.status_panel.message.text()
    assert widget.output_mode_combo.isEnabled() and widget.url_panel.source_panel.isEnabled()



def test_cancel_before_queued_analysis_result_preserves_project(qtbot, monkeypatch, make_video, make_clip):
    widget = window(qtbot, monkeypatch)
    widget.ffpaths = object()
    video = make_video()
    previous = Project([], "keep existing", [], [])
    widget.project = previous
    widget.url_panel.urls_edit.setPlainText(video.url)
    queued, previews = [], []
    monkeypatch.setattr(widget.pool, "start", queued.append)
    monkeypatch.setattr(widget, "start_previews", previews.append)
    widget.start_analysis()
    worker = queued[0]
    # The background job finished, but its GUI terminal callback is still queued.
    worker.done = True
    widget.cancel_work()
    worker.signals.finished.emit(Project([video.url], "new result", [video], [make_clip(video, 60)]))
    assert widget.project is previous and previews == []
    assert worker.cancel.is_set()
    assert "취소" in widget.status_panel.message.text()
    assert widget.output_mode_combo.isEnabled() and widget.url_panel.source_panel.isEnabled()
