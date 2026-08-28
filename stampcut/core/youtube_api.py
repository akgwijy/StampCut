"""YouTube Data API v3 — 영상 정보와 댓글 수집."""
from __future__ import annotations

import re
from datetime import datetime

import requests

from stampcut.core.models import ChannelInfo, RawComment, VideoInfo

BASE_URL = "https://www.googleapis.com/youtube/v3"
_ID = r"([A-Za-z0-9_-]{11})"
_URL_PATTERNS = [
    re.compile(p)
    for p in (r"[?&]v=" + _ID, r"youtu\.be/" + _ID, r"/shorts/" + _ID, r"/live/" + _ID, r"/embed/" + _ID)
]
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_GAME_RE = re.compile(r"(\d+)\s*게임")
_DUR_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_URL_RE = re.compile(r"youtube\.com/(@[A-Za-z0-9._-]+)")
_CHANNEL_URL_RE = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})")
_BARE_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]+$")


class YouTubeApiError(Exception):
    pass


class ApiKeyError(YouTubeApiError):
    pass


class QuotaError(YouTubeApiError):
    pass


class CommentsDisabled(YouTubeApiError):
    pass


class VideoNotFound(YouTubeApiError):
    def __init__(self, video_id: str, message: str | None = None):
        super().__init__(message if message is not None else f"영상을 찾을 수 없습니다: {video_id}")
        self.video_id = video_id


class ChannelNotFound(YouTubeApiError):
    def __init__(self, ref: str, message: str | None = None):
        super().__init__(message if message is not None else f"채널을 찾을 수 없습니다: {ref}")
        self.ref = ref


def parse_video_id(text: str) -> str | None:
    t = text.strip()
    for p in _URL_PATTERNS:
        m = p.search(t)
        if m:
            return m.group(1)
    return t if _BARE_ID.match(t) else None


def parse_channel_ref(text: str) -> tuple[str, str] | None:
    """채널 주소(@핸들 / channel/UC…) 또는 영상 주소를 ("handle"|"id"|"video", 값)으로. 모르면 None.

    /c/커스텀, /user/ 주소는 검색 API(100유닛)가 필요하므로 지원하지 않는다.
    """
    t = text.strip()
    m = _HANDLE_URL_RE.search(t)
    if m:
        return "handle", m.group(1)
    m = _CHANNEL_URL_RE.search(t)
    if m:
        return "id", m.group(1)
    if _BARE_HANDLE_RE.match(t):
        return "handle", t
    if _CHANNEL_ID_RE.match(t):
        return "id", t
    vid = parse_video_id(t)
    return ("video", vid) if vid else None


def short_name_from_title(title: str, index: int) -> str:
    m = _GAME_RE.search(title)
    return f"{m.group(1)}게임" if m else f"영상 {index + 1}"


def parse_iso_duration(s: str) -> int:
    m = _DUR_RE.match(s)
    if not m:
        return 0
    h, mi, se = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + se


def _raw(item: dict, is_reply: bool) -> RawComment:
    sn = item["snippet"]
    return RawComment(
        id=item["id"],
        text=sn.get("textOriginal") or sn.get("textDisplay", ""),
        author=sn.get("authorDisplayName", ""),
        like_count=int(sn.get("likeCount", 0)),
        is_reply=is_reply,
    )


class YouTubeClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, base_url: str = BASE_URL):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.base_url = base_url

    def _get(self, resource: str, **params) -> dict:
        params["key"] = self.api_key
        try:
            r = self.session.get(f"{self.base_url}/{resource}", params=params, timeout=30)
        except requests.RequestException as e:
            raise YouTubeApiError(f"네트워크 오류: {e}") from e
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    @staticmethod
    def _raise(r: requests.Response) -> None:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        reasons = {e.get("reason") for e in err.get("errors", [])}
        msg = err.get("message", r.text[:200])
        if r.status_code in (400, 403):
            if "keyInvalid" in reasons or "accessNotConfigured" in reasons or "API key not valid" in msg:
                raise ApiKeyError(msg)
        if r.status_code == 403:
            if "quotaExceeded" in reasons:
                raise QuotaError(msg)
            if "commentsDisabled" in reasons:
                raise CommentsDisabled(msg)
            raise YouTubeApiError(f"HTTP 403: {msg}")
        if r.status_code == 404:
            raise VideoNotFound("", msg)
        raise YouTubeApiError(f"HTTP {r.status_code}: {msg}")

    def _channel_by(self, ref: str, **params) -> ChannelInfo:
        data = self._get("channels", part="snippet,contentDetails", **params)
        items = data.get("items") or []
        if not items:
            raise ChannelNotFound(ref)
        it = items[0]
        return ChannelInfo(
            channel_id=it["id"],
            title=it.get("snippet", {}).get("title", ""),
            uploads_playlist_id=it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
        )

    def resolve_channel(self, text: str) -> ChannelInfo:
        """채널 주소·핸들·채널 id, 또는 그 채널의 영상 주소로 채널을 찾는다 (1~2유닛)."""
        ref = parse_channel_ref(text)
        if ref is None:
            raise ValueError(text)
        kind, value = ref
        if kind == "handle":
            return self._channel_by(text, forHandle=value)
        if kind == "id":
            return self._channel_by(text, id=value)
        data = self._get("videos", part="snippet", id=value)
        items = data.get("items") or []
        channel_id = items[0].get("snippet", {}).get("channelId") if items else None
        if not channel_id:
            raise ChannelNotFound(text)
        return self._channel_by(text, id=channel_id)

    def _video_items(self, ids: list[str]) -> dict[str, dict]:
        """videos.list를 50개씩 묶어 id → 항목."""
        items: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            data = self._get("videos", part="snippet,contentDetails,statistics", id=",".join(ids[i : i + 50]))
            for it in data.get("items", []):
                items[it["id"]] = it
        return items

    @staticmethod
    def _video_info(it: dict, idx: int, vid: str, url: str) -> VideoInfo:
        sn = it["snippet"]
        return VideoInfo(
            index=idx,
            video_id=vid,
            url=url,
            title=sn["title"],
            short_name=short_name_from_title(sn["title"], idx),
            channel_title=sn.get("channelTitle", ""),
            published_at=datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
            duration=parse_iso_duration(it["contentDetails"]["duration"]),
            comment_count=int(it.get("statistics", {}).get("commentCount", 0)),
        )

    def fetch_video_infos(self, urls: list[str], strict: bool = True) -> list[VideoInfo]:
        """strict=False면 응답에 없는 영상은 건너뛴다. 하나도 못 찾았을 때만 VideoNotFound."""
        ids: list[str] = []
        for u in urls:
            vid = parse_video_id(u)
            if not vid:
                raise ValueError(u)
            ids.append(vid)
        items = self._video_items(ids)
        infos: list[VideoInfo] = []
        missing: list[str] = []
        for idx, (u, vid) in enumerate(zip(urls, ids)):
            it = items.get(vid)
            if not it or "snippet" not in it or "contentDetails" not in it:
                if strict:
                    raise VideoNotFound(vid)
                missing.append(vid)
                continue
            infos.append(self._video_info(it, idx, vid, u.strip()))
        if not infos and missing:
            raise VideoNotFound(missing[0])
        return infos

    def fetch_channel_videos(self, channel: ChannelInfo, limit: int = 200, page_token: str | None = None) -> tuple[list[VideoInfo], str | None]:
        """업로드 재생목록을 최신순으로 limit개(페이지 단위 올림)까지 훑어 댓글이 있는 영상만 돌려준다.

        두 번째 값은 다음 페이지 토큰(없으면 None). playlistItems 50개당 1유닛 + videos 50개당 1유닛.
        """
        if not channel.uploads_playlist_id:
            return [], None
        ids: list[str] = []
        token = page_token
        next_token: str | None = None
        while True:
            params: dict = dict(part="contentDetails", playlistId=channel.uploads_playlist_id, maxResults=50)
            if token:
                params["pageToken"] = token
            data = self._get("playlistItems", **params)
            ids.extend(it["contentDetails"]["videoId"] for it in data.get("items", []) if "contentDetails" in it)
            token = data.get("nextPageToken")
            next_token = token
            if not token or len(ids) >= limit:
                break
        items = self._video_items(ids)
        infos: list[VideoInfo] = []
        for vid in ids:
            it = items.get(vid)
            if not it or "snippet" not in it or "contentDetails" not in it:
                continue
            info = self._video_info(it, len(infos), vid, f"https://www.youtube.com/watch?v={vid}")
            if info.comment_count > 0:
                info.channel_title = channel.title or info.channel_title
                infos.append(info)
        return infos, next_token

    def _fetch_replies(self, parent_id: str) -> list[RawComment]:
        out: list[RawComment] = []
        token = None
        while True:
            params = dict(part="snippet", parentId=parent_id, maxResults=100, textFormat="plainText")
            if token:
                params["pageToken"] = token
            data = self._get("comments", **params)
            out.extend(_raw(it, True) for it in data.get("items", []) if "snippet" in it)
            token = data.get("nextPageToken")
            if not token:
                return out

    def fetch_all_comments(self, video_id: str) -> list[RawComment]:
        out: list[RawComment] = []
        token = None
        while True:
            params = dict(part="snippet,replies", videoId=video_id, maxResults=100, textFormat="plainText", order="time")
            if token:
                params["pageToken"] = token
            try:
                data = self._get("commentThreads", **params)
            except CommentsDisabled:
                return out
            for th in data.get("items", []):
                top = th.get("snippet", {}).get("topLevelComment", {})
                if "snippet" not in top:
                    continue
                out.append(_raw(top, False))
                replies = th.get("replies", {}).get("comments", [])
                if th["snippet"].get("totalReplyCount", 0) > len(replies):
                    out.extend(self._fetch_replies(top["id"]))
                else:
                    out.extend(_raw(r, True) for r in replies if "snippet" in r)
            token = data.get("nextPageToken")
            if not token:
                return out
