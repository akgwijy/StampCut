import threading
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from stampcut.core.models import ChannelInfo, RawComment, VideoInfo
from stampcut.core.youtube_api import ChannelNotFound
from stampcut.gui.channel_dialog import BAD_REF_HINT, ChannelDialog
from stampcut.gui.channel_models import C_TIME, V_CHECK, V_STAMPS

CH = ChannelInfo("UC" + "x" * 22, "문성FC", "UU" + "x" * 22)
A, B, C = "A" * 11, "B" * 11, "C" * 11


@pytest.fixture(autouse=True)
def _isolate_orphaned_workers():
    """닫힌 창이 넘긴 워커 목록이 테스트 사이에 남지 않게 한다."""
    from stampcut.gui import channel_dialog as cd

    cd._ORPHANED_WORKERS.clear()
    yield
    cd._ORPHANED_WORKERS.clear()


@pytest.fixture(autouse=True)
def _no_modal_warning(monkeypatch):
    """워커 오류가 나면 모달 경고창 대신 목록에 담아, 테스트가 멈추지 않고 실패로 드러나게 한다."""
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    return warned


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


def _slow_pages(client, gate, page_token_to_block):
    """지정한 페이지 요청을 gate가 열릴 때까지 막는다 (워커가 진행 중인 상태를 만든다)."""
    original = client.fetch_channel_videos

    def slow(ch, limit=200, page_token=None):
        if page_token == page_token_to_block:
            gate.wait(5)
        return original(ch, limit, page_token)

    client.fetch_channel_videos = slow


def test_row_selected_during_more_loads_after_page(qtbot):
    client = _client()
    gate = threading.Event()
    _slow_pages(client, gate, "P2")
    dlg = _dialog(qtbot, client)
    _find(qtbot, dlg)
    dlg.more_btn.click()  # 페이지 워커가 gate에 막혀 진행 중
    assert dlg.busy()
    dlg.videos.selectRow(0)  # busy → pending으로 기억
    assert dlg.comment_model.rowCount() == 0
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        gate.set()  # 페이지 완료 → pending 영상을 이어서 로드
    assert dlg.video_model.rowCount() == 3 and dlg.comment_model.rowCount() == 2
    assert dlg.status.text() == "1게임: 댓글 2개, 타임스탬프 1개"
    assert [c for c in client.calls if c[0] == "comments"] == [("comments", A)]


def test_close_during_load_reenables_on_reopen(qtbot):
    client = _client()
    gate = threading.Event()
    _slow_pages(client, gate, None)
    dlg = _dialog(qtbot, client)
    dlg.ref_edit.setText("@moonsungfc")
    dlg.find()
    assert dlg.busy() and not dlg.find_btn.isEnabled()
    old = dlg._worker
    dlg.close()
    assert not dlg.busy() and dlg.find_btn.isEnabled() and dlg.ref_edit.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: old.done, timeout=5000)  # 차단된 워커가 끝나도 창 상태는 그대로
    assert dlg.video_model.rowCount() == 0 and dlg.find_btn.isEnabled()


def test_workers_are_kept_until_done(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    assert all(w.done for w in dlg._workers) and len(dlg._workers) >= 1


def test_job_maps_api_errors_to_user_messages():
    from stampcut.core.youtube_api import ApiKeyError, QuotaError
    from stampcut.gui.channel_dialog import _job

    def boom(exc, progress, cancel):
        raise exc

    with pytest.raises(RuntimeError, match="API 키"):
        _job(boom, ApiKeyError("bad"), progress=None, cancel=None)
    with pytest.raises(RuntimeError, match="할당량"):
        _job(boom, QuotaError("q"), progress=None, cancel=None)
    with pytest.raises(RuntimeError, match="채널을 찾을 수 없습니다: @x"):
        _job(boom, ChannelNotFound("@x"), progress=None, cancel=None)


def test_comments_for_superseded_selection_are_not_shown(qtbot):
    client = _client()
    gate = threading.Event()
    original = client.fetch_all_comments

    def slow(video_id):
        if video_id == A:
            assert gate.wait(5)
        return original(video_id)

    client.fetch_all_comments = slow
    dlg = _dialog(qtbot, client)
    _find(qtbot, dlg)
    dlg.videos.selectRow(0)  # A 로드 시작 (gate에 막힘)
    dlg.videos.selectRow(1)  # busy → B가 pending
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):  # A 완료
        gate.set()
    assert dlg.comment_model.rowCount() == 0  # A의 댓글이 B가 선택된 표를 덮지 않는다
    assert dlg.video_model.data(dlg.video_model.index(0, V_STAMPS)) == 1  # 개수는 기록된다
    # busy()/calls는 워커 스레드가 직접 건드리는 상태라 Qt 신호 전달보다 먼저 참이 될 수 있다(waitUntil이
    # 첫 호출에 만족되면 이벤트 루프를 한 번도 돌리지 않는다). status.text() 자체를 기다려야 경쟁이 없다.
    qtbot.waitUntil(lambda: dlg.status.text() == "2게임: 댓글이 없거나 막힌 영상입니다", timeout=5000)
    assert not dlg.busy() and ("comments", B) in dlg.client.calls


def test_big_comment_count_asks_before_loading(qtbot, monkeypatch):
    client = _client()
    client.pages[None][0][0].comment_count = 5000  # 1게임
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    dlg = _dialog(qtbot, client)
    _find(qtbot, dlg)
    dlg.videos.selectRow(0)
    assert asked and "5,000" in asked[0] and "51유닛" in asked[0]
    assert not [c for c in client.calls if c[0] == "comments"] and "불러오지 않았습니다" in dlg.status.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg._on_video_selected(dlg.video_model.index(0, 0), None)  # 같은 행 다시 → 이번엔 예
    assert dlg.comment_model.rowCount() == 2


def test_close_during_load_keeps_worker_alive_outside_dialog(qtbot):
    from stampcut.gui import channel_dialog as cd

    client = _client()
    gate = threading.Event()
    _slow_pages(client, gate, None)
    dlg = _dialog(qtbot, client)
    dlg.ref_edit.setText("@moonsungfc")
    dlg.find()
    old = dlg._worker
    dlg.close()
    assert any(w is old for w in cd._ORPHANED_WORKERS)  # 창이 삭제돼도 실행 중 QRunnable이 GC되지 않게
    gate.set()
    qtbot.waitUntil(lambda: old.done, timeout=5000)
    dlg.close()  # 다음 close에서 끝난 워커는 정리된다
    assert not any(w is old for w in cd._ORPHANED_WORKERS)
