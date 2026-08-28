from PySide6.QtCore import Qt

from stampcut.core.models import RawComment
from stampcut.gui.channel_models import (
    C_AUTHOR,
    C_LIKES,
    C_TEXT,
    C_TIME,
    V_CHECK,
    V_COMMENTS,
    V_DATE,
    V_LENGTH,
    V_STAMPS,
    V_TITLE,
    ChannelVideoModel,
    CommentModel,
)


def test_video_model_display_append_and_check(qtbot, make_video):
    m = ChannelVideoModel()
    a = make_video(video_id="A" * 11, url="https://www.youtube.com/watch?v=" + "A" * 11)
    b = make_video(video_id="B" * 11, url="https://www.youtube.com/watch?v=" + "B" * 11, comment_count=7)
    m.append([a])
    m.append([b])
    assert (m.rowCount(), m.columnCount()) == (2, 6) and (a.index, b.index) == (0, 1)
    assert m.data(m.index(0, V_DATE)) == "26.08.20" and m.data(m.index(0, V_TITLE)) == a.title  # KST 날짜
    assert m.data(m.index(0, V_LENGTH)) == "24:09" and m.data(m.index(1, V_COMMENTS)) == 7
    assert m.data(m.index(0, V_STAMPS)) == "–"
    assert m.data(m.index(0, V_TITLE), Qt.ToolTipRole) == a.url
    assert m.data(m.index(0, V_CHECK), Qt.CheckStateRole) == Qt.Unchecked
    assert m.flags(m.index(0, V_CHECK)) & Qt.ItemIsUserCheckable
    changes = []
    m.checked_changed.connect(lambda: changes.append(1))
    assert m.setData(m.index(1, V_CHECK), Qt.Checked, Qt.CheckStateRole)
    assert m.checked_urls() == [b.url] and changes == [1]
    assert m.setData(m.index(1, V_CHECK), Qt.Unchecked, Qt.CheckStateRole) and m.checked_urls() == []
    assert not m.setData(m.index(1, V_TITLE), "x", Qt.EditRole)
    m.set_timestamp_count(a.video_id, 3)
    assert m.data(m.index(0, V_STAMPS)) == 3 and m.row_of(b.video_id) == 1 and m.video_at(1) is b
    m.clear()
    assert m.rowCount() == 0 and m.checked_urls() == [] and m.row_of(a.video_id) == -1


def test_comment_model_marks_timestamps(qtbot, make_video):
    v = make_video()
    m = CommentModel()
    m.set_comments(v, [RawComment("c1", "원더골 12:38\n대박", "@a", 3, False), RawComment("c2", "그냥 댓글", "@b", 1, True)])
    assert (m.rowCount(), m.columnCount()) == (2, 4) and m.timestamp_count() == 1
    assert m.data(m.index(0, C_TIME)) == "12:38" and m.data(m.index(1, C_TIME)) == ""
    assert m.data(m.index(0, C_TEXT)) == "원더골 12:38 대박"  # 한 줄로 접음
    assert m.data(m.index(0, C_TEXT), Qt.ToolTipRole) == "원더골 12:38\n대박"
    assert m.data(m.index(1, C_AUTHOR)) == "@b (답글)" and m.data(m.index(0, C_LIKES)) == 3
    assert m.data(m.index(0, C_TIME), Qt.FontRole).bold() and m.data(m.index(1, C_TIME), Qt.FontRole) is None
    m.set_comments(None, [])
    assert m.rowCount() == 0 and m.timestamp_count() == 0
