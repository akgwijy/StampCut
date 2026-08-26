from PySide6.QtCore import Qt

from stampcut.core.models import ClipStatus, Settings
from stampcut.gui.clip_table import (
    COL_CAPTION,
    COL_ENABLED,
    COL_POST,
    COL_PRE,
    COL_STATUS,
    COL_TIME,
    ClipTableModel,
    status_text,
)


def test_display_and_defaults(qtbot, make_video, make_clip):
    m = ClipTableModel(Settings())
    clip = make_clip(make_video(), t=758, caption="원더골")
    m.set_clips([clip])
    assert (m.rowCount(), m.columnCount()) == (1, 7)
    assert m.data(m.index(0, COL_TIME)) == "12:38" and m.data(m.index(0, COL_CAPTION)) == "원더골"
    assert m.data(m.index(0, COL_PRE)) == 3 and m.data(m.index(0, COL_POST)) == 15
    assert m.data(m.index(0, COL_ENABLED), Qt.CheckStateRole) == Qt.Checked
    assert m.data(m.index(0, COL_STATUS)) == "대기"
    assert m.data(m.index(0, COL_PRE), Qt.ForegroundRole) is not None  # 기본값은 회색


def test_setdata_toggle_caption_and_seconds(qtbot, make_video, make_clip):
    m = ClipTableModel(Settings())
    clip = make_clip(make_video(), t=758, enabled=False, over_limit=True)
    m.set_clips([clip])
    changes = []
    m.changed.connect(lambda: changes.append(1))
    assert m.data(m.index(0, COL_STATUS)) == "길이 초과"
    assert m.setData(m.index(0, COL_ENABLED), Qt.Checked, Qt.CheckStateRole)
    assert clip.enabled and not clip.over_limit
    assert m.setData(m.index(0, COL_CAPTION), " 종범 골 ", Qt.EditRole) and clip.caption == "종범 골"
    assert m.setData(m.index(0, COL_PRE), 5, Qt.EditRole) and clip.pre == 5
    assert m.setData(m.index(0, COL_POST), 500, Qt.EditRole) and clip.post == 120
    assert m.data(m.index(0, COL_PRE), Qt.ForegroundRole) is None
    assert len(changes) == 4


def test_status_text(make_video, make_clip):
    clip = make_clip(make_video(), t=1)
    assert status_text(clip) == "대기"
    clip.status = ClipStatus.ERROR
    assert status_text(clip) == "오류"
    clip.enabled = False
    assert status_text(clip) == "제외"


def test_refresh_row_and_row_of(qtbot, make_video, make_clip):
    m = ClipTableModel(Settings())
    a, b = make_clip(make_video(), 1), make_clip(make_video(), 2)
    m.set_clips([a, b])
    with qtbot.waitSignal(m.dataChanged, timeout=1000) as blocker:
        m.refresh_row(b)
    assert blocker.args[0].row() == 1
    assert m.row_of(a) == 0 and m.clip_at(1) is b


def test_invalid_index_is_ignored(qtbot, make_video, make_clip):
    from PySide6.QtCore import QModelIndex

    m = ClipTableModel(Settings())
    assert m.data(QModelIndex()) is None  # 클립이 없을 때도 IndexError가 아니어야 한다
    assert m.setData(QModelIndex(), 5, Qt.EditRole) is False
    m.set_clips([make_clip(make_video(), t=758)])
    assert m.data(QModelIndex()) is None
    assert m.setData(QModelIndex(), 5, Qt.EditRole) is False


def test_seconds_delegate_has_no_arrow_buttons(qtbot):
    from PySide6.QtWidgets import QAbstractSpinBox, QWidget

    from stampcut.gui.clip_table import SecondsDelegate

    parent = QWidget()
    qtbot.addWidget(parent)
    editor = SecondsDelegate().createEditor(parent, None, None)
    assert editor.buttonSymbols() == QAbstractSpinBox.NoButtons  # 화살표 없이 직접 타이핑
    assert (editor.minimum(), editor.maximum()) == (0, 120)
    assert editor.suffix() == "초"
