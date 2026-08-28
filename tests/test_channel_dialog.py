from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from stampcut.core.models import ChannelInfo, RawComment, VideoInfo
from stampcut.core.youtube_api import ChannelNotFound
from stampcut.gui.channel_dialog import BAD_REF_HINT, ChannelDialog
from stampcut.gui.channel_models import C_TIME, V_CHECK, V_STAMPS

CH = ChannelInfo("UC" + "x" * 22, "문성FC", "UU" + "x" * 22)
A, B, C = "A" * 11, "B" * 11, "C" * 11


def _video(vid, title, comments):
    return VideoInfo(0, vid, f"https://www.youtube.com/watch?v={vid}", title, title, "문성FC", datetime(2026, 8, 20, tzinfo=timezone.utc), 1449, comments)


class FakeClient:
    def __init__(self, pages, comments):
        self.pages, self.comments, self.calls = pages, comments, []
        self.fail = None

    def resolve_channel(self, text):
        self.calls.append(("resolve", text))
        if self.fail:
            raise self.fail
        return CH

    def fetch_channel_videos(self, ch, limit=200, page_token=None):
        self.calls.append(("videos", page_token))
        return self.pages[page_token]

    def fetch_all_comments(self, video_id):
        self.calls.append(("comments", video_id))
        return list(self.comments.get(video_id, []))


def _client():
    a, b, c = _video(A, "1게임", 3), _video(B, "2게임", 1), _video(C, "3게임", 5)
    pages = {None: ([a, b], "P2"), "P2": ([c], None)}
    comments = {A: [RawComment("c1", "그냥", "@x", 9, False), RawComment("c2", "12:38 원더골", "@y", 1, False)], B: []}
    return FakeClient(pages, comments)


def _dialog(qtbot, client=None):
    dlg = ChannelDialog(client or _client(), limit=2)
    qtbot.addWidget(dlg)
    return dlg


def _find(qtbot, dlg, text="@moonsungfc"):
    dlg.ref_edit.setText(text)
    with qtbot.waitSignal(dlg.page_loaded, timeout=5000):
        dlg.find_btn.click()


def test_find_lists_videos_and_more_appends(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    assert dlg.video_model.rowCount() == 2 and dlg.more_btn.isEnabled() and dlg.find_btn.isEnabled()
    assert dlg.status.text() == "문성FC — 댓글 있는 영상 2개 (더 보기 가능)"
    with qtbot.waitSignal(dlg.page_loaded, timeout=5000):
        dlg.more_btn.click()
    assert dlg.video_model.rowCount() == 3 and not dlg.more_btn.isEnabled()
    assert dlg.status.text() == "문성FC — 댓글 있는 영상 3개"
    assert dlg.client.calls == [("resolve", "@moonsungfc"), ("videos", None), ("videos", "P2")]  # 더 보기는 채널을 다시 찾지 않는다


def test_find_again_resets_list_and_cache(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    _find(qtbot, dlg, CH.channel_id)
    assert dlg.video_model.rowCount() == 2 and dlg.comment_model.rowCount() == 0
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    assert [c for c in dlg.client.calls if c[0] == "comments"] == [("comments", A), ("comments", A)]  # 캐시가 비워졌다


def test_bad_ref_shows_hint_without_request(qtbot):
    dlg = _dialog(qtbot)
    dlg.ref_edit.setText("https://www.youtube.com/c/moonsung")
    dlg.find()
    assert dlg.status.text() == BAD_REF_HINT and dlg.client.calls == []


def test_selecting_video_loads_comments_once_and_marks_timestamps(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    assert dlg.comment_model.rowCount() == 2
    assert dlg.comment_model.data(dlg.comment_model.index(0, C_TIME)) == "12:38"  # 타임스탬프 댓글이 먼저
    assert dlg.video_model.data(dlg.video_model.index(0, V_STAMPS)) == 1
    assert dlg.status.text() == "1게임: 댓글 2개, 타임스탬프 1개"
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(1)
    assert dlg.comment_model.rowCount() == 0 and dlg.status.text() == "2게임: 댓글이 없거나 막힌 영상입니다"
    assert dlg.video_model.data(dlg.video_model.index(1, V_STAMPS)) == 0
    dlg.videos.selectRow(0)  # 캐시 → 요청 없이 즉시 표시
    assert dlg.comment_model.rowCount() == 2
    assert [c for c in dlg.client.calls if c[0] == "comments"] == [("comments", A), ("comments", B)]


def test_check_enables_add_and_emits_urls(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    assert not dlg.add_btn.isEnabled()
    dlg.video_model.setData(dlg.video_model.index(1, V_CHECK), Qt.Checked, Qt.CheckStateRole)
    assert dlg.add_btn.isEnabled()
    with qtbot.waitSignal(dlg.urls_selected, timeout=1000) as blocker:
        dlg.add_btn.click()
    assert blocker.args == [[f"https://www.youtube.com/watch?v={B}"]]
    assert dlg.video_model.rowCount() == 2  # 창은 닫히지 않고 목록도 그대로


def test_set_default_ref_only_when_empty(qtbot):
    dlg = _dialog(qtbot)
    dlg.set_default_ref("https://youtu.be/" + A)
    assert dlg.ref_edit.text() == "https://youtu.be/" + A
    dlg.set_default_ref("@other")
    assert dlg.ref_edit.text() == "https://youtu.be/" + A


def test_channel_not_found_warns_and_reenables(qtbot, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    client = _client()
    client.fail = ChannelNotFound("@nobody")
    dlg = _dialog(qtbot, client)
    dlg.ref_edit.setText("@nobody")
    dlg.find()
    qtbot.waitUntil(lambda: bool(warned), timeout=5000)
    assert warned[0] == "채널을 찾을 수 없습니다: @nobody"
    qtbot.waitUntil(lambda: dlg.find_btn.isEnabled(), timeout=5000)
    assert dlg.status.text() == "채널을 찾을 수 없습니다: @nobody" and not dlg.more_btn.isEnabled()
