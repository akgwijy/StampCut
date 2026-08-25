import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from stampcut.core.youtube_api import (
    BASE_URL,
    ApiKeyError,
    QuotaError,
    VideoNotFound,
    YouTubeApiError,
    YouTubeClient,
    parse_iso_duration,
    parse_video_id,
    short_name_from_title,
)

VID_A = "A" * 11
VID_B = "B" * 11


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
