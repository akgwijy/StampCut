"""Channel-period selection uses KST publication dates and complete upload history."""
import json
import threading
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from stampcut.core import channel
from stampcut.core.ffmpeg import Cancelled
from stampcut.core.models import ChannelInfo
from stampcut.core.youtube_api import BASE_URL, YouTubeClient

CH = ChannelInfo("UC" + "x" * 22, "문성FC", "UU" + "x" * 22)
START = date(2026, 8, 20)
END = date(2026, 8, 21)


class PageClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def resolve_channel(self, text):
        self.calls.append(("resolve", text))
        return CH

    def fetch_channel_videos(self, ch, limit=200, page_token=None):
        self.calls.append(("videos", limit, page_token))
        return self.pages[page_token]


def video_item(vid, published="2026-08-20T00:00:00Z", comments=2):
    return {
        "id": vid,
        "snippet": {"title": "일반 제목", "channelTitle": "다른 채널", "publishedAt": published},
        "contentDetails": {"duration": "PT10M"},
        "statistics": {"commentCount": str(comments)},
    }


def register_channel():
    responses.get(f"{BASE_URL}/channels", json={"items": [{
        "id": CH.channel_id, "snippet": {"title": CH.title},
        "contentDetails": {"relatedPlaylists": {"uploads": CH.uploads_playlist_id}},
    }]})


def test_period_kst_boundaries_end_inclusive_sort_dedupe_and_reindex(make_video):
    # UTC 15:00 is KST midnight; equal-time IDs must have a stable order.
    values = [
        ("before", "2026-08-19T14:59:59.999999+00:00"),
        ("b", "2026-08-19T15:00:00+00:00"),
        ("a", "2026-08-19T15:00:00+00:00"),
        ("last", "2026-08-21T14:59:59.999999+00:00"),
        ("after", "2026-08-21T15:00:00+00:00"),
    ]
    videos = [make_video(video_id=vid, index=99, title="일반 제목", short_name="오래된 이름",
                         published_at=datetime.fromisoformat(published)) for vid, published in values]
    videos[3].title = "마지막 7게임"
    client = PageClient({None: (videos + [videos[1]], None)})
    progress = []
    out = channel.find_channel_videos_in_period(client, "@x", START, END, progress=lambda *a: progress.append(a))
    assert out.channel == CH and out.next_token is None
    assert [(v.video_id, v.index, v.short_name) for v in out.videos] == [
        ("a", 0, "영상 1"), ("b", 1, "영상 2"), ("last", 2, "7게임")]
    assert CH.title in progress[-1][3] and "3" in progress[-1][3]


def test_period_does_not_stop_on_old_or_empty_page(make_video):
    old = make_video(video_id="old", published_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"))
    match = make_video(video_id="match")
    client = PageClient({None: ([old], "P2"), "P2": ([], "P3"), "P3": ([match], None)})
    out = channel.find_channel_videos_in_period(client, "@x", START, END)
    assert [v.video_id for v in out.videos] == ["match"]
    assert client.calls == [("resolve", "@x"), ("videos", 50, None), ("videos", 50, "P2"), ("videos", 50, "P3")]


@pytest.mark.parametrize("text,start,end", [("@x", END, START), ("   ", START, END), ("", START, END)])
def test_invalid_period_or_blank_channel_rejected_before_network(text, start, end):
    client = PageClient({})
    with pytest.raises(ValueError):
        channel.find_channel_videos_in_period(client, text, start, end)
    assert client.calls == []


def test_period_empty_result_is_normal():
    client = PageClient({None: ([], None)})
    out = channel.find_channel_videos_in_period(client, "@x", START, END)
    assert out.channel == CH and out.videos == [] and out.next_token is None


def test_period_pre_cancelled_does_not_resolve():
    event = threading.Event()
    event.set()
    client = PageClient({})
    with pytest.raises(Cancelled):
        channel.find_channel_videos_in_period(client, "@x", START, END, cancel=event)
    assert client.calls == []


@responses.activate
def test_period_real_client_continues_past_200_and_skips_unavailable_and_no_comments():
    register_channel()
    ids = [f"{n:011d}" for n in range(251)]
    # Duplicate on the final page; missing/invalid entries cannot abort valid videos.
    playlist_ids = ids + [ids[0]]

    def playlist_callback(request):
        query = parse_qs(urlparse(request.url).query)
        page = int(query.get("pageToken", ["0"])[0])
        chunk = playlist_ids[page * 50:(page + 1) * 50]
        result = {"items": [{"contentDetails": {"videoId": vid}} for vid in chunk]}
        if (page + 1) * 50 < len(playlist_ids):
            result["nextPageToken"] = str(page + 1)
        return 200, {}, json.dumps(result)

    def videos_callback(request):
        got = parse_qs(urlparse(request.url).query)["id"][0].split(",")
        items = [video_item(vid, comments=0 if vid == ids[1] else 2) for vid in got if vid != ids[2]]
        for item in items:
            if item["id"] == ids[3]:
                del item["contentDetails"]
        return 200, {}, json.dumps({"items": items})

    responses.add_callback(responses.GET, f"{BASE_URL}/playlistItems", callback=playlist_callback, content_type="application/json")
    responses.add_callback(responses.GET, f"{BASE_URL}/videos", callback=videos_callback, content_type="application/json")
    out = channel.find_channel_videos_in_period(YouTubeClient("KEY"), "@x", START, END)
    assert len(out.videos) == 248
    assert [v.video_id for v in out.videos] == [ids[0]] + ids[4:]
    assert [v.index for v in out.videos] == list(range(248))
    assert out.videos[-1].video_id == "00000000250" and out.next_token is None
    assert all(v.channel_title == CH.title for v in out.videos)


@responses.activate
def test_period_cancel_between_playlist_and_video_requests():
    register_channel()
    event = threading.Event()

    def stop_after_playlist(request):
        event.set()
        return 200, {}, json.dumps({"items": [{"contentDetails": {"videoId": "A" * 11}}]})

    responses.add_callback(responses.GET, f"{BASE_URL}/playlistItems", callback=stop_after_playlist, content_type="application/json")
    with pytest.raises(Cancelled):
        channel.find_channel_videos_in_period(YouTubeClient("KEY"), "@x", START, END, cancel=event)
    assert [urlparse(c.request.url).path.rsplit("/", 1)[-1] for c in responses.calls] == ["channels", "playlistItems"]


@pytest.mark.parametrize("resource", ["commentThreads", "comments"])
@responses.activate
def test_client_cancel_between_comment_and_reply_pages(resource):
    event = threading.Event()
    client = YouTubeClient("KEY")
    client.cancel_event = event

    def stop_after_page(request):
        if "pageToken" in parse_qs(urlparse(request.url).query):
            return 200, {}, json.dumps({"items": []})
        event.set()
        return 200, {}, json.dumps({"items": [], "nextPageToken": "P2"})

    responses.add_callback(responses.GET, f"{BASE_URL}/{resource}", callback=stop_after_page, content_type="application/json")
    with pytest.raises(Cancelled):
        if resource == "commentThreads":
            client.fetch_all_comments("A" * 11)
        else:
            client._fetch_replies("parent")
    assert len(responses.calls) == 1


@responses.activate
def test_load_comments_attaches_cancel_event_for_reply_fetch(make_video):
    event = threading.Event()
    client = YouTubeClient("KEY")

    def stop_before_replies(request):
        event.set()
        return 200, {}, json.dumps({"items": [{
            "id": "thread", "snippet": {"topLevelComment": {"id": "top", "snippet": {"textOriginal": "x"}},
            "totalReplyCount": 1}, "replies": {"comments": []},
        }]})

    responses.add_callback(responses.GET, f"{BASE_URL}/commentThreads", callback=stop_before_replies, content_type="application/json")
    responses.get(f"{BASE_URL}/comments", json={"items": []})
    with pytest.raises(Cancelled):
        channel.load_comments(client, make_video(), cancel=event)
    assert len(responses.calls) == 1


@pytest.mark.parametrize("operation", ["legacy", "period", "comments"])
@responses.activate
def test_reused_client_clears_previous_cancel_event(operation, make_video):
    previous = threading.Event()
    previous.set()
    client = YouTubeClient("KEY")
    client.cancel_event = previous
    register_channel()
    responses.get(f"{BASE_URL}/playlistItems", json={"items": []})
    responses.get(f"{BASE_URL}/commentThreads", json={"items": []})
    if operation == "legacy":
        result = channel.find_channel_videos(client, "@x")
        assert result.channel == CH and result.videos == []
    elif operation == "period":
        result = channel.find_channel_videos_in_period(client, "@x", START, END)
        assert result.channel == CH and result.videos == []
    else:
        assert channel.load_comments(client, make_video()) == []
    assert client.cancel_event is None
