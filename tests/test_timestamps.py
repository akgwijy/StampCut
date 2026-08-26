import pytest

from stampcut.core.models import RawComment
from stampcut.core.timestamps import extract_mentions, find_timestamps, format_time


def comment(text, **kw):
    base = dict(id="c1", text=text, author="@jyp2101", like_count=0, is_reply=False)
    base.update(kw)
    return RawComment(**base)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7:05 기훈 선방", [(425, "기훈 선방")]),
        ("원더골 12:38", [(758, "원더골")]),
        ("1:02:33 골", [(3753, "골")]),
        ("12분 38초 역습", [(758, "역습")]),
        ("1시간 2분 슛", [(3720, "슛")]),
        ("12분 코너킥", [(720, "코너킥")]),
        ("2시간", [(7200, "")]),
        ("- 12:38 : 원더골 -", [(758, "원더골")]),
        ("10:42, 10:50 골", [(642, "골"), (650, "골")]),
        ("14:05 오프사이드 기가막히게 거네...", [(845, "오프사이드 기가막히게 거네...")]),
        ("12:38", [(758, "")]),
        ("좋은 경기였습니다", []),
        ("7:05분 아 이렇게 쓰면", [(425, "아 이렇게 쓰면")]),
    ],
)
def test_extract_single_line(make_video, text, expected):
    video = make_video(duration=8000)
    got = [(m.seconds, m.caption) for m in extract_mentions(video, comment(text))]
    assert got == expected


def test_multiline_each_line_own_caption(make_video):
    got = extract_mentions(make_video(), comment("7:05 기훈 선방\n14:03 기훈 선방 2"))
    assert [(m.seconds, m.caption) for m in got] == [(425, "기훈 선방"), (843, "기훈 선방 2")]


def test_rejects_over_duration_and_bad_seconds(make_video):
    video = make_video(duration=1545)
    assert extract_mentions(video, comment("59:59 뭐지")) == []
    assert extract_mentions(video, comment("12:70 뭐지")) == []
    assert find_timestamps("1:75:00", 100000) == []


def test_duplicate_seconds_in_one_comment_kept_once(make_video):
    got = extract_mentions(make_video(), comment("12:38 골\n12:38 다시"))
    assert len(got) == 1


def test_mention_carries_comment_metadata(make_video):
    m = extract_mentions(make_video(), comment("7:05 선방", id="x", author="@a", like_count=3, is_reply=True))[0]
    assert (m.comment_id, m.author, m.like_count, m.is_reply, m.video_id) == ("x", "@a", 3, True, "POZWcyKFvjY")


def test_find_timestamps_positions():
    assert find_timestamps("ab 7:05 cd", 10000) == [(425, (3, 7))]


def test_find_timestamps_drops_overlapping_hangul_match():
    assert find_timestamps("7:05분 x", 100000) == [(425, (0, 4))]


def test_find_timestamps_hangul_boundary_rejection():
    assert find_timestamps("12분 75초 역습", 100000) == []
    assert find_timestamps("1시간 75분", 100000) == []


def test_find_timestamps_adjacent_digit_exclusion():
    assert find_timestamps("12:345", 100000) == []


def test_format_time():
    assert format_time(425) == "7:05"
    assert format_time(3753) == "1:02:33"
    assert format_time(0) == "0:00"
