import threading

import pytest

from stampcut.core import channel
from stampcut.core.ffmpeg import Cancelled
from stampcut.core.models import ChannelInfo, RawComment

CH = ChannelInfo("UC" + "x" * 22, "문성FC", "UU" + "x" * 22)


class FakeClient:
    def __init__(self, videos, comments=None, next_token=None):
        self.videos, self.comments, self.next_token = videos, comments or {}, next_token
        self.calls = []

    def resolve_channel(self, text):
        self.calls.append(("resolve", text))
        return CH

    def fetch_channel_videos(self, ch, limit=200, page_token=None):
        self.calls.append(("videos", ch.channel_id, limit, page_token))
        return list(self.videos), self.next_token

    def fetch_all_comments(self, video_id):
        self.calls.append(("comments", video_id))
        return list(self.comments.get(video_id, []))


def test_find_channel_videos_resolves_then_lists(make_video):
    v = make_video()
    client = FakeClient([v], next_token="P2")
    prog = []
    page = channel.find_channel_videos(client, "@moonsungfc", limit=50, progress=lambda *a: prog.append(a))
    assert page.channel == CH and page.videos == [v] and page.next_token == "P2"
    assert client.calls == [("resolve", "@moonsungfc"), ("videos", CH.channel_id, 50, None)]
    assert prog[0][3] == "채널 찾는 중" and prog[-1][3] == "문성FC — 댓글 있는 영상 1개"


def test_find_channel_videos_reuses_channel_for_next_page(make_video):
    client = FakeClient([make_video()])
    other = ChannelInfo("UC" + "y" * 22, "다른채널", "UU" + "y" * 22)
    page = channel.find_channel_videos(client, "ignored", page_token="P2", channel=other)
    assert page.channel is other and client.calls == [("videos", other.channel_id, 200, "P2")]


def test_find_channel_videos_cancel_after_resolve(make_video):
    client = FakeClient([make_video()])
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        channel.find_channel_videos(client, "@x", cancel=cancel)
    assert client.calls == [("resolve", "@x")]


def test_load_comments_puts_timestamps_first_then_likes(make_video):
    v = make_video()
    raw = [
        RawComment("c1", "그냥 최고", "@a", 9, False),
        RawComment("c2", "12:38 원더골", "@b", 2, False),
        RawComment("c3", "7:05 선방", "@c", 5, True),
        RawComment("c4", "ㅋㅋ", "@d", 0, False),
    ]
    client = FakeClient([v], comments={v.video_id: raw})
    prog = []
    out = channel.load_comments(client, v, progress=lambda *a: prog.append(a))
    assert [c.id for c in out] == ["c3", "c2", "c1", "c4"]
    assert client.calls == [("comments", v.video_id)] and prog[-1][3] == "3게임 댓글 4개"
