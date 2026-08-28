"""채널 영상 찾기 창의 표 모델: 영상 목록(체크·타임스탬프 수)과 댓글 목록(타임스탬프 강조)."""
from __future__ import annotations

from datetime import timedelta, timezone

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QFont

from stampcut.core.models import RawComment, VideoInfo
from stampcut.core.timestamps import first_timestamp, format_time

KST = timezone(timedelta(hours=9))
V_CHECK, V_DATE, V_TITLE, V_LENGTH, V_COMMENTS, V_STAMPS = range(6)
VIDEO_HEADERS = ["✓", "날짜", "제목", "길이", "댓글", "타임스탬프"]
C_TIME, C_AUTHOR, C_LIKES, C_TEXT = range(4)
COMMENT_HEADERS = ["시각", "작성자", "좋아요", "내용"]


class ChannelVideoModel(QAbstractTableModel):
    checked_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.videos: list[VideoInfo] = []
        self._checked: set[str] = set()
        self._stamps: dict[str, int] = {}  # video_id → 타임스탬프 댓글 수 (댓글을 불러온 뒤)

    # --- 데이터 갱신 ---
    def clear(self) -> None:
        self.beginResetModel()
        self.videos, self._checked, self._stamps = [], set(), {}
        self.endResetModel()
        self.checked_changed.emit()

    def append(self, videos: list[VideoInfo]) -> None:
        """페이지를 덧붙인다. index는 표에서의 순서로 다시 매긴다."""
        if not videos:
            return
        start = len(self.videos)
        self.beginInsertRows(QModelIndex(), start, start + len(videos) - 1)
        for i, v in enumerate(videos):
            v.index = start + i
            self.videos.append(v)
        self.endInsertRows()

    def video_at(self, row: int) -> VideoInfo:
        return self.videos[row]

    def row_of(self, video_id: str) -> int:
        return next((i for i, v in enumerate(self.videos) if v.video_id == video_id), -1)

    def set_timestamp_count(self, video_id: str, count: int) -> None:
        self._stamps[video_id] = count
        r = self.row_of(video_id)
        if r >= 0:
            self.dataChanged.emit(self.index(r, V_STAMPS), self.index(r, V_STAMPS))

    def checked_urls(self) -> list[str]:
        return [v.url for v in self.videos if v.video_id in self._checked]

    # --- QAbstractTableModel ---
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.videos)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(VIDEO_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return VIDEO_HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == V_CHECK:
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.videos[index.row()]
        col = index.column()
        if role == Qt.CheckStateRole and col == V_CHECK:
            return Qt.Checked if v.video_id in self._checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            if col == V_DATE:
                return v.published_at.astimezone(KST).strftime("%y.%m.%d")
            if col == V_TITLE:
                return v.title
            if col == V_LENGTH:
                return format_time(v.duration)
            if col == V_COMMENTS:
                return v.comment_count
            if col == V_STAMPS:
                n = self._stamps.get(v.video_id)
                return "–" if n is None else n
        if role == Qt.ToolTipRole and col == V_TITLE:
            return v.url
        if role == Qt.TextAlignmentRole and col in (V_LENGTH, V_COMMENTS, V_STAMPS):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or index.column() != V_CHECK or role != Qt.CheckStateRole:
            return False
        vid = self.videos[index.row()].video_id
        if Qt.CheckState(value) == Qt.Checked:
            self._checked.add(vid)
        else:
            self._checked.discard(vid)
        self.dataChanged.emit(index, index)
        self.checked_changed.emit()
        return True


class CommentModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.video: VideoInfo | None = None
        self.comments: list[RawComment] = []
        self._stamps: list[int | None] = []  # 행별 첫 타임스탬프(초)

    def set_comments(self, video: VideoInfo | None, comments: list[RawComment]) -> None:
        self.beginResetModel()
        self.video = video
        self.comments = list(comments)
        self._stamps = [first_timestamp(c, video) if video is not None else None for c in self.comments]
        self.endResetModel()

    def timestamp_count(self) -> int:
        return sum(1 for s in self._stamps if s is not None)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.comments)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COMMENT_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COMMENT_HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        c = self.comments[index.row()]
        stamp = self._stamps[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == C_TIME:
                return "" if stamp is None else format_time(stamp)
            if col == C_AUTHOR:
                return c.author + (" (답글)" if c.is_reply else "")
            if col == C_LIKES:
                return c.like_count
            if col == C_TEXT:
                return " ".join(c.text.split())  # 한 줄로 접음, 전문은 툴팁
        if role == Qt.ToolTipRole and col == C_TEXT:
            return c.text
        if role == Qt.FontRole and stamp is not None:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.TextAlignmentRole and col in (C_TIME, C_LIKES):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None
