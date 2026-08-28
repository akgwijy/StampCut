import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from stampcut.core.models import ChannelInfo
from stampcut.core.youtube_api import (
    BASE_URL,
    ApiKeyError,
    ChannelNotFound,
    QuotaError,
    VideoNotFound,
    YouTubeApiError,
    YouTubeClient,
    parse_channel_ref,
    parse_iso_duration,
    parse_video_id,
    short_name_from_title,
)

VID_A = "A" * 11
VID_B = "B" * 11
VID_C = "C" * 11
CH = "UC" + "x" * 22
UPLOADS = "UU" + "x" * 22


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.youtube.com/watch?v=i3_SYn3e_kY", "i3_SYn3e_kY"),
        ("https://www.youtube.com/watch?v=i3_SYn3e_kY&t=120s", "i3_SYn3e_kY"),
        ("https://youtu.be/i3_SYn3e_kY?si=abc", "i3_SYn3e_kY"),
        ("https://www.youtube.com/shorts/i3_SYn3e_kY", "i3_SYn3e_kY"),
        ("https://www.youtube.com/live/i3_SYn3e_kY", "i3_SYn3e_kY"),
        ("https://www.youtube.com/embed/i3_SYn3e_kY", "i3_SYn3e_kY"),
        ("  i3_SYn3e_kY  ", "i3_SYn3e_kY"),
        ("https://example.com/x", None),
        ("hello", None),
    ],
)
def test_parse_video_id(text, expected):
    assert parse_video_id(text) == expected


def test_parse_iso_duration():
    assert parse_iso_duration("PT25M45S") == 1545
    assert parse_iso_duration("PT1H2M3S") == 3723
    assert parse_iso_duration("PT59S") == 59
    assert parse_iso_duration("PT2H") == 7200


def test_short_name_from_title():
    assert short_name_from_title("26.08.20 문성FC 3게임(vs 하리보)", 0) == "3게임"
    assert short_name_from_title("다른 제목", 1) == "영상 2"


def video_item(vid, title="26.08.20 문성FC 3게임(vs 하리보)"):
    return {
        "id": vid,
        "snippet": {"title": title, "channelTitle": "문성FC", "publishedAt": "2026-08-20T13:36:52Z"},
        "contentDetails": {"duration": "PT24M9S"},
        "statistics": {"commentCount": "4"},
    }


def reply(rid, text):
    return {"id": rid, "snippet": {"textOriginal": text, "authorDisplayName": "@r", "likeCount": 0}}


def thread(cid, text, replies=(), total=None):
    return {
        "id": cid,
        "snippet": {
            "topLevelComment": {"id": cid, "snippet": {"textOriginal": text, "authorDisplayName": "@a", "likeCount": 1}},
            "totalReplyCount": len(replies) if total is None else total,
        },
        "replies": {"comments": [reply(r, t) for r, t in replies]},
    }


@responses.activate
def test_fetch_video_infos_builds_infos_in_url_order():
    responses.get(f"{BASE_URL}/videos", json={"items": [video_item(VID_B), video_item(VID_A)]})
    infos = YouTubeClient("KEY").fetch_video_infos([f"https://youtu.be/{VID_A}", VID_B])
    assert [(v.index, v.video_id) for v in infos] == [(0, VID_A), (1, VID_B)]
    assert infos[0].duration == 1449 and infos[0].short_name == "3게임" and infos[0].comment_count == 4
    assert infos[0].published_at.tzinfo is not None
    q = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert q["key"] == ["KEY"] and q["id"] == [f"{VID_A},{VID_B}"]


@responses.activate
def test_fetch_video_infos_missing_raises():
    responses.get(f"{BASE_URL}/videos", json={"items": []})
    with pytest.raises(VideoNotFound) as ei:
        YouTubeClient("KEY").fetch_video_infos([f"https://youtu.be/{VID_A}"])
    assert ei.value.video_id == VID_A


@responses.activate
def test_fetch_video_infos_non_strict_skips_missing():
    responses.get(f"{BASE_URL}/videos", json={"items": [video_item(VID_B)]})
    infos = YouTubeClient("KEY").fetch_video_infos([f"https://youtu.be/{VID_A}", VID_B], strict=False)
    assert [(v.index, v.video_id) for v in infos] == [(1, VID_B)]


@responses.activate
def test_fetch_video_infos_non_strict_all_missing_raises():
    responses.get(f"{BASE_URL}/videos", json={"items": []})
    with pytest.raises(VideoNotFound) as ei:
        YouTubeClient("KEY").fetch_video_infos([f"https://youtu.be/{VID_A}", VID_B], strict=False)
    assert ei.value.video_id == VID_A


def test_fetch_video_infos_bad_url_raises_value_error():
    with pytest.raises(ValueError):
        YouTubeClient("KEY").fetch_video_infos(["nope"])


@responses.activate
def test_fetch_all_comments_pages_and_replies():
    def threads_cb(request):
        q = parse_qs(urlparse(request.url).query)
        if "pageToken" not in q:
            body = {"items": [thread("c1", "7:05 선방", replies=[("r1", "답글")])], "nextPageToken": "P2"}
        else:
            assert q["pageToken"] == ["P2"]
            body = {"items": [thread("c2", "원더골 12:38", replies=[("r2", "x")], total=2)]}
        return 200, {}, json.dumps(body)

    responses.add_callback(responses.GET, f"{BASE_URL}/commentThreads", callback=threads_cb, content_type="application/json")
    responses.get(f"{BASE_URL}/comments", json={"items": [reply("r2", "x"), reply("r3", "y")]})
    out = YouTubeClient("KEY").fetch_all_comments(VID_A)
    assert [(c.id, c.is_reply) for c in out] == [("c1", False), ("r1", True), ("c2", False), ("r2", True), ("r3", True)]
    assert out[0].text == "7:05 선방" and out[0].like_count == 1 and out[0].author == "@a"
    q = parse_qs(urlparse(responses.calls[-1].request.url).query)
    assert q["parentId"] == ["c2"]


def _err(status, reason):
    return dict(status=status, json={"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}})


@responses.activate
def test_error_mapping():
    responses.get(f"{BASE_URL}/videos", **_err(400, "keyInvalid"))
    with pytest.raises(ApiKeyError):
        YouTubeClient("BAD").fetch_video_infos([VID_A])
    responses.get(f"{BASE_URL}/videos", **_err(403, "quotaExceeded"))
    with pytest.raises(QuotaError):
        YouTubeClient("KEY").fetch_video_infos([VID_A])
    responses.get(f"{BASE_URL}/commentThreads", **_err(403, "commentsDisabled"))
    assert YouTubeClient("KEY").fetch_all_comments(VID_A) == []


@responses.activate
def test_error_403_forbidden_is_generic_not_api_key_error():
    responses.get(f"{BASE_URL}/videos", **_err(403, "forbidden"))
    with pytest.raises(YouTubeApiError) as ei:
        YouTubeClient("KEY").fetch_video_infos([VID_A])
    assert not isinstance(ei.value, ApiKeyError)


@responses.activate
def test_error_403_access_not_configured_is_api_key_error():
    responses.get(f"{BASE_URL}/videos", **_err(403, "accessNotConfigured"))
    with pytest.raises(ApiKeyError):
        YouTubeClient("KEY").fetch_video_infos([VID_A])


@responses.activate
def test_error_404_raises_video_not_found():
    responses.get(
        f"{BASE_URL}/videos",
        status=404,
        json={"error": {"code": 404, "message": "nope", "errors": [{"reason": "notFound"}]}},
    )
    with pytest.raises(VideoNotFound):
        YouTubeClient("KEY").fetch_video_infos([VID_A])


@responses.activate
def test_network_error_wrapped_as_youtube_api_error():
    responses.get(f"{BASE_URL}/videos", body=requests.ConnectionError("down"))
    with pytest.raises(YouTubeApiError):
        YouTubeClient("KEY").fetch_video_infos([VID_A])


@responses.activate
def test_fetch_video_infos_missing_content_details_raises_video_not_found():
    item = video_item(VID_A)
    del item["contentDetails"]
    responses.get(f"{BASE_URL}/videos", json={"items": [item]})
    with pytest.raises(VideoNotFound) as ei:
        YouTubeClient("KEY").fetch_video_infos([VID_A])
    assert ei.value.video_id == VID_A


def _thread_missing_top_level_snippet(cid):
    return {
        "id": cid,
        "snippet": {"topLevelComment": {"id": cid}, "totalReplyCount": 0},
        "replies": {"comments": []},
    }


@responses.activate
def test_fetch_all_comments_skips_thread_with_malformed_top_level_comment():
    body = {"items": [_thread_missing_top_level_snippet("bad1"), thread("c1", "정상 댓글")]}
    responses.get(f"{BASE_URL}/commentThreads", json=body)
    out = YouTubeClient("KEY").fetch_all_comments(VID_A)
    assert [c.id for c in out] == ["c1"]


@responses.activate
def test_fetch_all_comments_skips_malformed_reply():
    th = thread("c1", "댓글", replies=[], total=2)
    responses.get(f"{BASE_URL}/commentThreads", json={"items": [th]})
    responses.get(f"{BASE_URL}/comments", json={"items": [{"id": "bad_reply"}, reply("r1", "정상 답글")]})
    out = YouTubeClient("KEY").fetch_all_comments(VID_A)
    assert [c.id for c in out] == ["c1", "r1"]


@responses.activate
def test_fetch_all_comments_disabled_mid_pagination_returns_partial():
    def threads_cb(request):
        q = parse_qs(urlparse(request.url).query)
        if "pageToken" not in q:
            body = {"items": [thread("c1", "첫 댓글")], "nextPageToken": "P2"}
            return 200, {}, json.dumps(body)
        err = {"error": {"code": 403, "message": "commentsDisabled", "errors": [{"reason": "commentsDisabled"}]}}
        return 403, {}, json.dumps(err)

    responses.add_callback(responses.GET, f"{BASE_URL}/commentThreads", callback=threads_cb, content_type="application/json")
    out = YouTubeClient("KEY").fetch_all_comments(VID_A)
    assert [c.id for c in out] == ["c1"]


@responses.activate
def test_comment_falls_back_to_text_display():
    item = {
        "id": "c1",
        "snippet": {
            "topLevelComment": {"id": "c1", "snippet": {"textDisplay": "7:05 하이라이트", "authorDisplayName": "@a", "likeCount": 2}},
            "totalReplyCount": 0,
        },
        "replies": {"comments": []},
    }
    responses.get(f"{BASE_URL}/commentThreads", json={"items": [item]})
    out = YouTubeClient("KEY").fetch_all_comments(VID_A)
    assert [c.text for c in out] == ["7:05 하이라이트"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.youtube.com/@moonsungfc/videos", ("handle", "@moonsungfc")),
        ("  @moonsungfc ", ("handle", "@moonsungfc")),
        ("https://www.youtube.com/channel/" + CH, ("id", CH)),
        (CH, ("id", CH)),
        ("https://youtu.be/" + VID_A, ("video", VID_A)),
        ("https://www.youtube.com/watch?v=" + VID_A + "&t=5s", ("video", VID_A)),
        ("https://www.youtube.com/c/moonsung", None),
        ("https://www.youtube.com/user/moonsung", None),
        ("hello", None),
    ],
)
def test_parse_channel_ref(text, expected):
    assert parse_channel_ref(text) == expected


def channel_item(cid=CH, title="문성FC", uploads=UPLOADS):
    return {"id": cid, "snippet": {"title": title}, "contentDetails": {"relatedPlaylists": {"uploads": uploads}}}


@responses.activate
def test_resolve_channel_by_handle_and_id():
    responses.get(f"{BASE_URL}/channels", json={"items": [channel_item()]})
    ch = YouTubeClient("KEY").resolve_channel("https://www.youtube.com/@moonsungfc")
    assert ch == ChannelInfo(CH, "문성FC", UPLOADS)
    q = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert q["forHandle"] == ["@moonsungfc"] and q["part"] == ["snippet,contentDetails"] and q["key"] == ["KEY"]
    YouTubeClient("KEY").resolve_channel(CH)
    q = parse_qs(urlparse(responses.calls[1].request.url).query)
    assert q["id"] == [CH] and "forHandle" not in q


@responses.activate
def test_resolve_channel_from_video_url():
    responses.get(f"{BASE_URL}/videos", json={"items": [{"id": VID_A, "snippet": {"channelId": CH}}]})
    responses.get(f"{BASE_URL}/channels", json={"items": [channel_item()]})
    ch = YouTubeClient("KEY").resolve_channel(f"https://youtu.be/{VID_A}")
    assert ch.channel_id == CH and ch.uploads_playlist_id == UPLOADS
    q0 = parse_qs(urlparse(responses.calls[0].request.url).query)
    q1 = parse_qs(urlparse(responses.calls[1].request.url).query)
    assert q0["id"] == [VID_A] and q0["part"] == ["snippet"] and q1["id"] == [CH]


@responses.activate
def test_resolve_channel_not_found_and_bad_ref():
    responses.get(f"{BASE_URL}/channels", json={"items": []})
    with pytest.raises(ChannelNotFound) as ei:
        YouTubeClient("KEY").resolve_channel("@nobody")
    assert ei.value.ref == "@nobody" and "@nobody" in str(ei.value)
    responses.get(f"{BASE_URL}/videos", json={"items": []})
    with pytest.raises(ChannelNotFound):
        YouTubeClient("KEY").resolve_channel(VID_A)
    with pytest.raises(ValueError):
        YouTubeClient("KEY").resolve_channel("https://www.youtube.com/c/x")


def playlist_item(vid):
    return {"contentDetails": {"videoId": vid}}


def video_with_comments(vid, count):
    it = video_item(vid, title=f"영상 {vid[:1]}")
    it["snippet"]["channelTitle"] = "스니펫채널명"  # fetch_channel_videos는 이 값이 아니라 채널명을 써야 한다
    it["statistics"] = {"commentCount": str(count)}
    return it


@responses.activate
def test_fetch_channel_videos_pages_filters_and_returns_next_token():
    ch = ChannelInfo(CH, "문성FC", UPLOADS)

    def playlist_cb(request):
        q = parse_qs(urlparse(request.url).query)
        assert q["playlistId"] == [UPLOADS] and q["maxResults"] == ["50"] and q["part"] == ["contentDetails"]
        if "pageToken" not in q:
            return 200, {}, json.dumps({"items": [playlist_item(VID_A), playlist_item(VID_B)], "nextPageToken": "P2"})
        assert q["pageToken"] == ["P2"]
        return 200, {}, json.dumps({"items": [playlist_item(VID_C)], "nextPageToken": "P3"})

    responses.add_callback(responses.GET, f"{BASE_URL}/playlistItems", callback=playlist_cb, content_type="application/json")
    responses.get(
        f"{BASE_URL}/videos",
        json={"items": [video_with_comments(VID_A, 4), video_with_comments(VID_B, 0), video_with_comments(VID_C, 7)]},
    )
    videos, token = YouTubeClient("KEY").fetch_channel_videos(ch, limit=3)
    assert [(v.index, v.video_id, v.comment_count) for v in videos] == [(0, VID_A, 4), (1, VID_C, 7)]  # 댓글 0개 제외
    assert token == "P3"
    assert videos[0].url == f"https://www.youtube.com/watch?v={VID_A}" and videos[0].channel_title == "문성FC"
    assert videos[0].title == "영상 A" and videos[0].duration == 1449
    q = parse_qs(urlparse(responses.calls[-1].request.url).query)
    assert q["id"] == [f"{VID_A},{VID_B},{VID_C}"] and q["part"] == ["snippet,contentDetails,statistics"]


@responses.activate
def test_fetch_channel_videos_keeps_whole_last_page_and_stops_without_token():
    ch = ChannelInfo(CH, "문성FC", UPLOADS)
    responses.get(f"{BASE_URL}/playlistItems", json={"items": [playlist_item(VID_A), playlist_item(VID_B)]})  # nextPageToken 없음
    responses.get(f"{BASE_URL}/videos", json={"items": [video_with_comments(VID_A, 1), video_with_comments(VID_B, 2)]})
    videos, token = YouTubeClient("KEY").fetch_channel_videos(ch, limit=1, page_token="P9")
    assert [v.video_id for v in videos] == [VID_A, VID_B] and token is None  # limit은 페이지 단위로 올림
    q = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert q["pageToken"] == ["P9"]


def test_fetch_channel_videos_without_uploads_playlist():
    assert YouTubeClient("KEY").fetch_channel_videos(ChannelInfo(CH, "x", "")) == ([], None)


@responses.activate
def test_fetch_channel_videos_uses_channel_title_even_when_empty():
    ch = ChannelInfo(CH, "", UPLOADS)
    responses.get(f"{BASE_URL}/playlistItems", json={"items": [playlist_item(VID_A)]})
    responses.get(f"{BASE_URL}/videos", json={"items": [video_with_comments(VID_A, 1)]})
    videos, _ = YouTubeClient("KEY").fetch_channel_videos(ch)
    assert videos[0].channel_title == ""  # 스니펫의 channelTitle로 대체하지 않는다
