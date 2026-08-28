"""채널 영상 찾기 창: 댓글 있는 영상 목록 → 영상별 댓글(타임스탬프 강조) → 체크한 URL을 메인 화면에 추가."""
from __future__ import annotations

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from stampcut.core import channel as channel_mod
from stampcut.core.models import ChannelInfo, RawComment, VideoInfo
from stampcut.core.timestamps import comment_has_timestamp
from stampcut.core.youtube_api import ApiKeyError, ChannelNotFound, QuotaError, parse_channel_ref
from stampcut.gui.channel_models import V_TITLE, ChannelVideoModel, CommentModel
from stampcut.gui.workers import Worker

BAD_REF_HINT = "채널 주소(@핸들, channel/UC…)나 영상 주소를 넣으세요. /c/·/user/ 주소는 지원하지 않습니다."


def _job(fn, *args, progress, cancel, **kwargs):
    """core 예외를 사용자 문구로 바꾼다 (main_window._analyze_job과 같은 규칙)."""
    try:
        return fn(*args, progress=progress, cancel=cancel, **kwargs)
    except ApiKeyError as e:
        raise RuntimeError(f"API 키가 잘못되었거나 YouTube Data API v3가 사용 설정되지 않았습니다.\n설정에서 키를 확인하세요.\n({e})") from e
    except QuotaError as e:
        raise RuntimeError(f"오늘의 API 할당량을 다 썼습니다. 내일 오후 4시(태평양 자정)에 초기화됩니다.\n({e})") from e
    except ChannelNotFound as e:
        raise RuntimeError(f"채널을 찾을 수 없습니다: {e.ref}") from e


class ChannelDialog(QDialog):
    urls_selected = Signal(list)
    page_loaded = Signal(object)  # ChannelPage
    comments_loaded = Signal(object)  # VideoInfo

    def __init__(self, client, limit: int = 200, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client, self.limit = client, limit
        self.setWindowTitle("채널 영상 찾기")
        self.setModal(False)
        self.resize(1100, 700)
        self.pool = QThreadPool.globalInstance()
        self._worker: Worker | None = None
        self._channel: ChannelInfo | None = None
        self._next_token: str | None = None
        self._comment_cache: dict[str, list[RawComment]] = {}
        self._pending_video: VideoInfo | None = None  # 로드 중 고른 마지막 영상

        self.ref_edit = QLineEdit()
        self.ref_edit.setPlaceholderText("채널 주소(@핸들, channel/UC…) 또는 그 채널의 영상 주소")
        self.ref_edit.returnPressed.connect(self.find)
        self.find_btn = QPushButton("찾기")
        self.find_btn.clicked.connect(self.find)
        self.more_btn = QPushButton("더 보기")
        self.more_btn.setEnabled(False)
        self.more_btn.clicked.connect(self.load_more)
        self.status = QLabel("")

        self.video_model = ChannelVideoModel(self)
        self.video_model.checked_changed.connect(self._sync_add_btn)
        self.videos = QTableView()
        self.videos.setModel(self.video_model)
        self.videos.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.videos.setSelectionMode(QAbstractItemView.SingleSelection)
        self.videos.verticalHeader().setVisible(False)
        header = self.videos.horizontalHeader()
        for col in range(self.video_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == V_TITLE else QHeaderView.ResizeToContents)
        self.videos.selectionModel().currentRowChanged.connect(self._on_video_selected)

        self.comment_model = CommentModel(self)
        self.comments = QTableView()
        self.comments.setModel(self.comment_model)
        self.comments.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.comments.verticalHeader().setVisible(False)
        self.comments.horizontalHeader().setStretchLastSection(True)
        self.comments.setWordWrap(False)

        self.add_btn = QPushButton("체크한 영상 URL 목록에 추가")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._emit_urls)
        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.close)

        top = QHBoxLayout()
        top.addWidget(self.ref_edit, 1)
        top.addWidget(self.find_btn)
        top.addWidget(self.more_btn)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.videos)
        splitter.addWidget(self.comments)
        splitter.setSizes([600, 500])
        bottom = QHBoxLayout()
        bottom.addWidget(self.add_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.close_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.status)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

    # --- 외부 API ---
    def set_default_ref(self, text: str) -> None:
        """입력창이 비어 있을 때만 채운다 (메인 창이 열 때 첫 URL을 넣어 준다)."""
        if not self.ref_edit.text().strip():
            self.ref_edit.setText(text)

    def busy(self) -> bool:
        return self._worker is not None and not self._worker.done

    # --- 찾기 / 더 보기 ---
    def find(self) -> None:
        if self.busy():
            return
        text = self.ref_edit.text().strip()
        if parse_channel_ref(text) is None:
            self.status.setText(BAD_REF_HINT)
            return
        self._channel, self._next_token, self._pending_video = None, None, None
        self._comment_cache.clear()
        self.video_model.clear()
        self.comment_model.set_comments(None, [])
        self._start_page(text, None)

    def load_more(self) -> None:
        if self.busy() or self._channel is None or self._next_token is None:
            return
        self._start_page(self.ref_edit.text().strip(), self._next_token)

    def _start_page(self, text: str, token: str | None) -> None:
        w = Worker(_job, channel_mod.find_channel_videos, self.client, text, limit=self.limit, page_token=token, channel=self._channel)
        w.signals.finished.connect(self._on_page)
        w.signals.failed.connect(self._on_failed)
        w.signals.cancelled.connect(lambda: self._set_busy(False))
        self._run(w)

    def _on_page(self, page) -> None:
        self._channel, self._next_token = page.channel, page.next_token
        self.video_model.append(page.videos)
        self._set_busy(False)
        more = " (더 보기 가능)" if page.next_token else ""
        self.status.setText(f"{page.channel.title} — 댓글 있는 영상 {self.video_model.rowCount()}개{more}")
        self.page_loaded.emit(page)

    # --- 댓글 ---
    def _on_video_selected(self, current, _previous) -> None:
        if not current.isValid():
            return
        video = self.video_model.video_at(current.row())
        cached = self._comment_cache.get(video.video_id)
        if cached is not None:
            self._show_comments(video, cached)
            return
        if self.busy():
            self._pending_video = video  # 끝나면 마지막 선택을 이어서 불러온다
            return
        self._start_comments(video)

    def _start_comments(self, video: VideoInfo) -> None:
        w = Worker(_job, channel_mod.load_comments, self.client, video)
        w.signals.finished.connect(lambda comments, v=video: self._on_comments(v, comments))
        w.signals.failed.connect(self._on_failed)
        w.signals.cancelled.connect(lambda: self._set_busy(False))
        self._run(w)

    def _on_comments(self, video: VideoInfo, comments: list) -> None:
        self._set_busy(False)
        self._comment_cache[video.video_id] = comments
        self.video_model.set_timestamp_count(video.video_id, sum(1 for c in comments if comment_has_timestamp(c, video)))
        self._show_comments(video, comments)
        self.comments_loaded.emit(video)
        self._run_pending()

    def _show_comments(self, video: VideoInfo, comments: list) -> None:
        self.comment_model.set_comments(video, comments)
        if comments:
            self.status.setText(f"{video.short_name}: 댓글 {len(comments)}개, 타임스탬프 {self.comment_model.timestamp_count()}개")
        else:
            self.status.setText(f"{video.short_name}: 댓글이 없거나 막힌 영상입니다")

    def _run_pending(self) -> None:
        pending, self._pending_video = self._pending_video, None
        if pending is not None:
            cached = self._comment_cache.get(pending.video_id)
            if cached is not None:
                self._show_comments(pending, cached)
            else:
                self._start_comments(pending)

    # --- 공통 ---
    def _run(self, w: Worker) -> None:
        self._worker = w
        self._set_busy(True)
        w.signals.progress.connect(lambda stage, done, total, msg: self.status.setText(msg))
        self.pool.start(w)

    def _set_busy(self, busy: bool) -> None:
        self.find_btn.setEnabled(not busy)
        self.ref_edit.setEnabled(not busy)
        self.more_btn.setEnabled(not busy and self._next_token is not None)

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.status.setText(msg.splitlines()[0])
        QMessageBox.warning(self, "채널 영상 찾기", msg)
        self._run_pending()

    def _sync_add_btn(self) -> None:
        self.add_btn.setEnabled(bool(self.video_model.checked_urls()))

    def _emit_urls(self) -> None:
        urls = self.video_model.checked_urls()
        if urls:
            self.urls_selected.emit(urls)

    def closeEvent(self, event) -> None:
        if self.busy():  # 진행 중 워커가 닫힌 창을 건드리지 않도록 (메인 창과 같은 패턴)
            self._worker.cancel.set()
            self._worker.signals.blockSignals(True)
        super().closeEvent(event)
