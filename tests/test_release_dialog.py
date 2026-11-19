from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from stampcut.core import settings as settings_mod
from stampcut.core.models import Settings
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.publishing.models import PublishedPost
from stampcut.core.publishing.service import BatchResult,PublishOutcome
from stampcut.gui.status_bar import StatusPanel
from stampcut.gui import main_window


def create(qtbot,tmp_path,settings=None,service=None):
    from stampcut.gui.release_dialog import ReleaseDialog
    store=CredentialStore(tmp_path/"vault.bin",protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
    video=tmp_path/"output.mp4";video.write_bytes(b"test")
    widget=ReleaseDialog(settings or Settings(),video_path=video,suggested_title="경기 영상",store=store,service=service)
    qtbot.addWidget(widget)
    return widget,video


def test_result_release_button_passes_completed_file(qtbot,tmp_path):
    panel=StatusPanel();qtbot.addWidget(panel)
    assert panel.release_btn.isHidden()
    path=tmp_path/"done.mp4"
    panel.set_done(path)
    received=[];panel.release_requested.connect(received.append)
    panel.release_btn.click()
    assert received==[path] and not panel.release_btn.isHidden()
    panel.set_busy(True)
    assert panel.release_btn.isHidden()


def test_post_content_and_options_save_on_cancel_and_reopen(qtbot,tmp_path):
    widget,video=create(qtbot,tmp_path)
    body="첫 줄\n\n둘째 줄 #축구 ⚽"
    widget.body_edit.setPlainText(body)
    widget.title_edit.setText("저장한 제목")
    widget.instagram_check.setChecked(False)
    widget.privacy_combo.setCurrentIndex(widget.privacy_combo.findData("unlisted"))
    widget.reject()
    settings=settings_mod.load()
    assert settings.publish_draft["body"]==body and settings.publish_draft["privacy"]=="unlisted"
    reopened,_=create(qtbot,tmp_path,settings=settings)
    assert reopened.body_edit.toPlainText()==body and reopened.title_edit.text()=="저장한 제목"
    assert not reopened.instagram_check.isChecked()
    assert "access_token" not in settings.publish_draft


def test_post_content_is_saved_while_dialog_stays_open(qtbot,tmp_path):
    widget,_=create(qtbot,tmp_path)
    widget.body_edit.setPlainText("자동 저장")
    qtbot.waitUntil(lambda:settings_mod.load().publish_draft.get("body")=="자동 저장",timeout=2000)


def test_editing_text_never_uploads_and_click_shows_target_results(qtbot,tmp_path,monkeypatch):
    calls=[]
    class Service:
        def publish(self,video,draft,progress,cancel):
            calls.append((video,draft.body))
            return BatchResult([PublishOutcome("youtube","success",PublishedPost("youtube","ABCDEFGHIJK","https://www.youtube.com/watch?v=ABCDEFGHIJK")),PublishOutcome("instagram","error",error="권한 확인")])
    widget,video=create(qtbot,tmp_path,service=Service())
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    widget.body_edit.setPlainText("게시할 내용")
    assert calls==[] and queued==[]
    widget.upload_btn.click()
    assert widget.is_busy and not widget.body_edit.isEnabled()
    queued[0].run()
    assert calls==[(video,"게시할 내용")] and not widget.is_busy
    assert "완료" in widget.result_table.item(0,1).text()
    assert "권한 확인" in widget.result_table.item(1,1).text()
    assert widget.upload_btn.isEnabled()


def test_close_during_upload_cancels_but_preserves_draft(qtbot,tmp_path,monkeypatch):
    class Service:
        def publish(self,video,draft,progress,cancel):
            return BatchResult(cancelled=cancel.is_set())
    widget,_=create(qtbot,tmp_path,service=Service())
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    widget.show();widget.body_edit.setPlainText("취소해도 저장")
    widget.upload_btn.click();widget.reject()
    assert widget.isVisible() and queued[0].cancel.is_set()
    queued[0].run()
    assert not widget.isVisible() and settings_mod.load().publish_draft["body"]=="취소해도 저장"


def test_empty_file_or_target_does_not_start_worker(qtbot,tmp_path,monkeypatch):
    widget,_=create(qtbot,tmp_path)
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    widget.video_edit.clear();widget.upload_btn.click()
    assert queued==[] and widget.message.text()


def test_main_window_result_and_toolbar_open_release_dialog(qtbot,tmp_path,monkeypatch):
    opened=[]
    class FakeDialog(QDialog):
        def __init__(self,settings,**kwargs):
            super().__init__(kwargs.get("parent"));opened.append(kwargs)
        def exec(self):return QDialog.Rejected
    monkeypatch.setattr(main_window,"ReleaseDialog",FakeDialog,raising=False)
    window=main_window.MainWindow(Settings());qtbot.addWidget(window)
    path=tmp_path/"ready.mp4";window.status_panel.set_done(path)
    window.status_panel.release_btn.click()
    assert opened[-1]["video_path"]==path
    window.release_action.trigger()
    assert len(opened)==2
    window._set_busy(True)
    assert not window.release_action.isEnabled()
    window._set_busy(False)


def test_new_video_does_not_reuse_previous_instagram_media_url(qtbot,tmp_path):
    from stampcut.core.publishing.models import ReleaseDraft
    old=tmp_path/"old.mp4";old.write_bytes(b"old")
    settings=Settings(publish_draft=ReleaseDraft(body="재사용할 글",video_path=str(old),instagram_video_url="https://media.example/old.mp4").to_dict())
    widget,new=create(qtbot,tmp_path,settings=settings)
    assert widget.body_edit.toPlainText()=="재사용할 글"
    assert widget.instagram_url_edit.text()==""
    widget.instagram_url_edit.setText("https://media.example/new.mp4")
    widget.video_edit.setText(str(old))
    assert widget.instagram_url_edit.text()==""


def test_main_close_waits_for_release_worker(qtbot,tmp_path,monkeypatch):
    class Service:
        def publish(self,video,draft,progress,cancel):return BatchResult(cancelled=cancel.is_set())
    widget,_=create(qtbot,tmp_path,service=Service())
    window=main_window.MainWindow(Settings());qtbot.addWidget(window)
    window._release_dialog=widget
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    window.show();widget.show()
    widget.upload_btn.click()
    window.close()
    assert window.isVisible() and widget.isVisible() and queued[0].cancel.is_set()
    queued[0].run()
    assert not widget.isVisible()
    window.close()


def test_url_mode_can_prepare_a_compatible_file_without_publishing(qtbot,tmp_path,monkeypatch):
    from stampcut.gui import release_dialog
    widget,video=create(qtbot,tmp_path)
    widget.store.put("instagram",{"method":"url","username":"demo"})
    widget._refresh_accounts()
    prepared=tmp_path/"compatible.mp4";prepared.write_bytes(b"converted")
    monkeypatch.setattr(release_dialog,"prepare_instagram",lambda *a,**k:prepared,raising=False)
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    widget.body_edit.setPlainText("keep caption")
    widget.instagram_url_edit.setText("https://media.example/old.mp4")
    widget.prepare_btn.click()
    queued[0].run()
    assert widget.video_edit.text()==str(video)
    assert widget.prepared_edit.text()==str(prepared)
    assert widget.instagram_url_edit.text()=="" and widget.body_edit.toPlainText()=="keep caption"


def test_replaced_file_at_same_path_clears_previous_instagram_url(qtbot,tmp_path):
    import hashlib
    from stampcut.gui.release_dialog import ReleaseDialog
    video=tmp_path/"same.mp4";video.write_bytes(b"old")
    stat=video.stat()
    settings=Settings(publish_draft={"video_path":str(video),"instagram_video_url":"https://media.example/old.mp4","video_stamp":f"{stat.st_size}:{stat.st_mtime_ns}","instagram_source_fingerprint":hashlib.sha256(b"old").hexdigest()})
    video.write_bytes(b"replacement bytes")
    store=CredentialStore(tmp_path/"vault",protect=lambda b:b,unprotect=lambda b:b)
    widget=ReleaseDialog(settings,video_path=video,store=store);qtbot.addWidget(widget)
    assert widget.instagram_url_edit.text()==""


def test_settings_save_failure_allows_explicit_close_without_losing_memory(qtbot,tmp_path,monkeypatch):
    from stampcut.gui import release_dialog
    from PySide6.QtWidgets import QMessageBox
    widget,_=create(qtbot,tmp_path);widget.show();widget.body_edit.setPlainText("keep in memory")
    def fail(*a,**k):raise PermissionError("read only")
    monkeypatch.setattr(settings_mod,"save",fail)
    monkeypatch.setattr(QMessageBox,"warning",lambda *a,**k:QMessageBox.Discard)
    widget.close()
    assert not widget.isVisible() and widget.settings.publish_draft["body"]=="keep in memory"


def test_discard_on_busy_close_is_confirmed_only_once(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    class Service:
        def publish(self,*a,**k):return BatchResult(cancelled=True)
    widget,_=create(qtbot,tmp_path,service=Service());widget.show()
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    widget.upload_btn.click()
    def fail(*a,**k):raise PermissionError("read only")
    monkeypatch.setattr(settings_mod,"save",fail)
    prompts=[]
    def discard(*a,**k):prompts.append(1);return QMessageBox.Discard
    monkeypatch.setattr(QMessageBox,"warning",discard)
    widget.reject();queued[0].run()
    assert not widget.isVisible() and len(prompts)==1


def test_cancel_close_choice_keeps_dialog_after_worker_stops(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    class Service:
        def publish(self,*a,**k):return BatchResult(cancelled=True)
    widget,_=create(qtbot,tmp_path,service=Service());widget.show()
    queued=[];monkeypatch.setattr(widget.pool,"start",queued.append)
    widget.upload_btn.click()
    def fail(*a,**k):raise PermissionError("read only")
    monkeypatch.setattr(settings_mod,"save",fail)
    prompts=[]
    def cancel(*a,**k):prompts.append(1);return QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox,"warning",cancel)
    widget.reject();queued[0].run()
    assert widget.isVisible() and len(prompts)==1
    monkeypatch.setattr(QMessageBox,"warning",lambda *a,**k:QMessageBox.Discard)
    widget.reject()


def test_cancel_unsaved_close_does_not_leave_main_shutdown_pending(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    widget,_=create(qtbot,tmp_path)
    window=main_window.MainWindow(Settings());qtbot.addWidget(window)
    window._release_dialog=widget;window.show();widget.show()
    def fail(*a,**k):raise PermissionError("read only")
    monkeypatch.setattr(settings_mod,"save",fail)
    monkeypatch.setattr(QMessageBox,"warning",lambda *a,**k:QMessageBox.Cancel)
    window.close()
    assert window.isVisible() and widget.isVisible() and not window._release_close_requested
    monkeypatch.setattr(QMessageBox,"warning",lambda *a,**k:QMessageBox.Discard)
    widget.reject();window.close()
