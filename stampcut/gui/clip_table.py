"""후보 클립 표 모델과 앞/뒤 초 스핀박스 델리게이트."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSpinBox, QStyledItemDelegate

from stampcut.core.models import Clip, ClipStatus, Settings
from stampcut.core.timestamps import format_time

COL_ENABLED, COL_VIDEO, COL_TIME, COL_CAPTION, COL_PRE, COL_POST, COL_STATUS = range(7)
HEADERS = ["✓", "영상", "시간", "자막", "앞", "뒤", "상태"]
_STATUS_TEXT = {
    ClipStatus.PENDING: "대기",
    ClipStatus.DOWNLOADING: "받는 중",
    ClipStatus.READY: "준비됨",
    ClipStatus.ERROR: "오류",
}


def status_text(clip: Clip) -> str:
    if not clip.enabled:
        return "길이 초과" if clip.over_limit else "제외"
    return _STATUS_TEXT[clip.status]


class ClipTableModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.clips: list[Clip] = []

    # --- 데이터 갱신 ---
    def set_clips(self, clips: list[Clip]) -> None:
        self.beginResetModel()
        self.clips = list(clips)
        self.endResetModel()

    def set_settings(self, settings: Settings) -> None:
        self.settings = settings
        if self.clips:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.clips) - 1, self.columnCount() - 1))

    def clip_at(self, row: int) -> Clip:
        return self.clips[row]

    def row_of(self, clip: Clip) -> int:
        return next((i for i, c in enumerate(self.clips) if c.id == clip.id), -1)

    def refresh_row(self, clip: Clip) -> None:
        r = self.row_of(clip)
        if r >= 0:
            self.dataChanged.emit(self.index(r, 0), self.index(r, self.columnCount() - 1))

    # --- QAbstractTableModel ---
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.clips)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        col = index.column()
        if col == COL_ENABLED:
            f |= Qt.ItemIsUserCheckable
        elif col in (COL_CAPTION, COL_PRE, COL_POST):
            f |= Qt.ItemIsEditable
        return f

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        clip = self.clips[index.row()]
        col = index.column()
        s = self.settings
        if role == Qt.CheckStateRole and col == COL_ENABLED:
            return Qt.Checked if clip.enabled else Qt.Unchecked
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == COL_VIDEO:
                return clip.video.short_name
            if col == COL_TIME:
                return format_time(clip.t)
            if col == COL_CAPTION:
                return clip.caption
            if col == COL_PRE:
                return clip.effective_pre(s)
            if col == COL_POST:
                return clip.effective_post(s)
            if col == COL_STATUS:
                return status_text(clip)
        if role == Qt.ForegroundRole:
            if (col == COL_PRE and clip.pre is None) or (col == COL_POST and clip.post is None):
                return QColor("#888888")
            if not clip.enabled:
                return QColor("#999999")
            if col == COL_STATUS and clip.status is ClipStatus.ERROR:
                return QColor("#d00000")
        if role == Qt.ToolTipRole:
            if col == COL_STATUS and clip.error:
                return clip.error
            if col == COL_CAPTION:
                return "\n".join(f"{m.author}: {m.caption or '(자막 없음)'}" for m in clip.mentions)
        if role == Qt.TextAlignmentRole and col in (COL_TIME, COL_PRE, COL_POST):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        clip = self.clips[index.row()]
        col = index.column()
        if role == Qt.CheckStateRole and col == COL_ENABLED:
            clip.enabled = Qt.CheckState(value) == Qt.Checked
            clip.over_limit = False
        elif role == Qt.EditRole and col == COL_CAPTION:
            clip.caption = str(value).strip()
        elif role == Qt.EditRole and col == COL_PRE:
            clip.pre = max(0, min(120, int(value)))
        elif role == Qt.EditRole and col == COL_POST:
            clip.post = max(0, min(120, int(value)))
        else:
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), self.columnCount() - 1))
        self.changed.emit()
        return True


class SecondsDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        sb = QSpinBox(parent)
        sb.setRange(0, 120)
        sb.setSuffix("초")
        return sb

    def setEditorData(self, editor, index):
        editor.setValue(int(index.data(Qt.EditRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)
