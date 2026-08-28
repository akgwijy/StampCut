# 채널 영상 찾기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한 채널의 최근 업로드 중 **댓글이 있는 영상**만 목록으로 보여주고, 영상을 고르면 댓글(타임스탬프 댓글 강조)을 보여주며, 체크한 영상 URL을 메인 화면 URL 목록에 추가하는 별도 창을 만든다.

**Architecture:** `core/youtube_api.py`에 채널 해석·업로드 목록 조회를 더하고(`resolve_channel`, `fetch_channel_videos`), `core/channel.py`가 Worker에서 부를 두 함수(`find_channel_videos`, `load_comments`)를 제공한다. GUI는 `gui/channel_models.py`(영상·댓글 표 모델)와 `gui/channel_dialog.py`(모달 아닌 창)로 나누고, `MainWindow`는 툴바 액션으로 창을 열고 `urls_selected`를 `UrlPanel.add_urls`로 받는다. 댓글은 영상을 클릭할 때만 불러오고 캐시한다.

**Tech Stack:** Python 3.12, PySide6 (`QAbstractTableModel`, `QDialog`, `QThreadPool` `Worker`), YouTube Data API v3 (`channels`, `playlistItems`, `videos`, `commentThreads`), `requests`, pytest + pytest-qt + `responses`.

스펙: `docs/superpowers/specs/2026-08-28-channel-finder-design.md`

## Global Constraints

- `stampcut/core`는 Qt를 import하지 않는다. GUI는 `stampcut/gui`에만.
- 영상 모델은 기존 `VideoInfo`를 재사용한다. 새 타입은 `ChannelInfo(channel_id, title, uploads_playlist_id)`(models.py)와 `ChannelPage(channel, videos, next_token)`(channel.py)뿐.
- `fetch_channel_videos`는 **`commentCount > 0`인 영상만** 돌려주고, 재생목록 순서(최신순)를 유지하며, `limit`은 페이지(50개) 단위로 올림해 자른다(마지막 페이지를 중간에서 끊지 않는다). 두 번째 반환값은 다음 페이지 토큰(없으면 `None`).
- 기존 `fetch_video_infos`의 동작·테스트는 불변(내부 헬퍼로 분리만).
- Worker 규약: 백그라운드 함수는 `fn(*args, progress=..., cancel=..., **kwargs)`로 호출된다. `progress(stage, done, total, message)`.
- 사용자 문구(정확히): 잘못된 입력 안내 `"채널 주소(@핸들, channel/UC…)나 영상 주소를 넣으세요. /c/·/user/ 주소는 지원하지 않습니다."`, 채널 없음 `"채널을 찾을 수 없습니다: {ref}"`, 상태 `"{채널명} — 댓글 있는 영상 {n}개"`, 댓글 없음 `"{short_name}: 댓글이 없거나 막힌 영상입니다"`, URL 추가 `"URL {n}개 추가됨 — 댓글 분석을 누르세요"` / `"이미 목록에 있는 영상입니다"`. API 키/할당량 문구는 `main_window._analyze_job`과 동일.
- 댓글 정렬: 타임스탬프 댓글 먼저, 그 안에서 좋아요 내림차순; 나머지도 좋아요 내림차순. 타임스탬프 판별은 `timestamps.find_timestamps(line, video.duration)` (영상 길이 초과는 무효).
- 테스트는 실제 네트워크를 쓰지 않는다: API는 `responses`, GUI는 가짜 클라이언트 + `qtbot`.
- 테스트 실행: `.\.venv\Scripts\python -m pytest -q` (플레인 `pytest`는 PATH에 없음). 시작 시 272 passed / 3 deselected.
- 커밋 메시지는 기존 관례(`feat(core): …`, `feat(gui): …`, `docs: …`), 본문 마지막 줄에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File Structure

| 파일 | 역할 | 작업 |
|---|---|---|
| `stampcut/core/models.py` | `ChannelInfo` | Task 1 |
| `stampcut/core/youtube_api.py` | `parse_channel_ref`, `ChannelNotFound`, `resolve_channel`; `_video_items`/`_video_info` 분리, `fetch_channel_videos` | Task 1, 2 |
| `stampcut/core/timestamps.py` | `first_timestamp`, `comment_has_timestamp` | Task 3 |
| `stampcut/core/channel.py` (신규) | `ChannelPage`, `find_channel_videos`, `load_comments` | Task 4 |
| `stampcut/gui/url_panel.py` | `add_urls` | Task 5 |
| `stampcut/gui/channel_models.py` (신규) | `ChannelVideoModel`, `CommentModel` | Task 6 |
| `stampcut/gui/channel_dialog.py` (신규) | `ChannelDialog` + `_job` 예외 래핑 | Task 7 |
| `stampcut/gui/main_window.py`, `README.md` | 툴바 액션·창 열기·URL 추가·문서 | Task 8 |

---

### Task 1: parse_channel_ref + ChannelInfo + resolve_channel

**Files:**
- Modify: `stampcut/core/models.py` (`VideoInfo` 아래)
- Modify: `stampcut/core/youtube_api.py`
- Test: `tests/test_youtube_api.py`

**Interfaces:**
- Produces: `models.ChannelInfo(channel_id: str, title: str, uploads_playlist_id: str)`; `youtube_api.ChannelNotFound(YouTubeApiError)` with `.ref`; `youtube_api.parse_channel_ref(text) -> tuple[str, str] | None` returning `("handle", "@x") | ("id", "UC…") | ("video", "<11자>")`; `YouTubeClient.resolve_channel(text) -> ChannelInfo` (raises `ValueError` for unparseable text, `ChannelNotFound` when the API returns nothing).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_youtube_api.py` import 블록을 다음으로 바꾼다:

```python
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
```

파일 끝에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_youtube_api.py -q`
Expected: FAIL — `ImportError: cannot import name 'ChannelInfo'` / `'ChannelNotFound'`

- [ ] **Step 3: models.py 구현**

`VideoInfo` 클래스 바로 아래에 추가:

```python
@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    uploads_playlist_id: str  # 채널 업로드 재생목록 (UU…)
```

- [ ] **Step 4: youtube_api.py 구현**

import와 정규식 상수 추가 (`_DUR_RE` 아래):

```python
from stampcut.core.models import ChannelInfo, RawComment, VideoInfo

...

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_URL_RE = re.compile(r"youtube\.com/(@[A-Za-z0-9._-]+)")
_CHANNEL_URL_RE = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})")
_BARE_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]+$")
```

`VideoNotFound` 아래에 예외 추가:

```python
class ChannelNotFound(YouTubeApiError):
    def __init__(self, ref: str, message: str | None = None):
        super().__init__(message if message is not None else f"채널을 찾을 수 없습니다: {ref}")
        self.ref = ref
```

`parse_video_id` 아래에 함수 추가:

```python
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
```

`YouTubeClient`에 메서드 추가 (`fetch_video_infos` 앞):

```python
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
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_youtube_api.py -q`
Expected: 모두 PASS (기존 테스트 포함)

- [ ] **Step 6: 커밋**

```bash
git add stampcut/core/models.py stampcut/core/youtube_api.py tests/test_youtube_api.py
git commit -m "feat(core): parse_channel_ref and resolve_channel (handle / id / video URL)"
```

---

### Task 2: fetch_channel_videos (업로드 목록 페이징, 댓글 있는 영상만)

**Files:**
- Modify: `stampcut/core/youtube_api.py` (`fetch_video_infos` 리팩터 + 새 메서드)
- Test: `tests/test_youtube_api.py`

**Interfaces:**
- Consumes: `ChannelInfo` (Task 1)
- Produces: `YouTubeClient.fetch_channel_videos(channel: ChannelInfo, limit: int = 200, page_token: str | None = None) -> tuple[list[VideoInfo], str | None]`; 내부 `_video_items(ids) -> dict[str, dict]`, `_video_info(it, idx, vid, url) -> VideoInfo`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_youtube_api.py` 끝에 추가:

```python
def playlist_item(vid):
    return {"contentDetails": {"videoId": vid}}


def video_with_comments(vid, count):
    it = video_item(vid, title=f"영상 {vid[:1]}")
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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_youtube_api.py -q`
Expected: FAIL — `AttributeError: 'YouTubeClient' object has no attribute 'fetch_channel_videos'`

- [ ] **Step 3: 구현**

`fetch_video_infos`를 다음 세 메서드로 교체 (동작 불변):

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_youtube_api.py -q`
Expected: 모두 PASS — 기존 `test_fetch_video_infos_*` 4개도 그대로 통과

- [ ] **Step 5: 커밋**

```bash
git add stampcut/core/youtube_api.py tests/test_youtube_api.py
git commit -m "feat(core): fetch_channel_videos — uploads playlist paging, comment_count > 0 only"
```

---

### Task 3: first_timestamp / comment_has_timestamp

**Files:**
- Modify: `stampcut/core/timestamps.py` (`extract_mentions` 아래)
- Test: `tests/test_timestamps.py`

**Interfaces:**
- Produces: `first_timestamp(comment: RawComment, video: VideoInfo) -> int | None`, `comment_has_timestamp(comment: RawComment, video: VideoInfo) -> bool`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_timestamps.py` 끝에 추가 (파일 상단 import에 `RawComment`와 두 함수를 추가한다 — 이미 있는 import 줄을 확인하고 없는 이름만 덧붙인다):

```python
from stampcut.core.models import RawComment
from stampcut.core.timestamps import comment_has_timestamp, first_timestamp


def _c(text):
    return RawComment("c", text, "@a", 0, False)


def test_first_timestamp_and_has_timestamp(make_video):
    v = make_video(duration=1449)
    assert first_timestamp(_c("잘 봤어요\n원더골 12:38 그리고 7:05"), v) == 758  # 첫 줄에 없으면 다음 줄, 줄 안에서는 등장 순
    assert first_timestamp(_c("12분 38초 골"), v) == 758
    assert first_timestamp(_c("50:00 은 영상 길이 밖"), v) is None  # 1449초 초과는 무효
    assert first_timestamp(_c("그냥 댓글"), v) is None
    assert comment_has_timestamp(_c("1:02 선방"), v) and not comment_has_timestamp(_c("ㅋㅋ"), v)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_timestamps.py -q`
Expected: FAIL — `ImportError: cannot import name 'comment_has_timestamp'`

- [ ] **Step 3: 구현**

`timestamps.py`의 `extract_mentions` 아래(`format_time` 앞)에 추가:

```python
def first_timestamp(comment: RawComment, video: VideoInfo) -> int | None:
    """댓글에서 등장 순서상 첫 타임스탬프(초). 없으면 None. 영상 길이를 넘는 값은 무시한다."""
    for line in comment.text.splitlines():
        found = find_timestamps(line, video.duration)
        if found:
            return found[0][0]
    return None


def comment_has_timestamp(comment: RawComment, video: VideoInfo) -> bool:
    return first_timestamp(comment, video) is not None
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_timestamps.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add stampcut/core/timestamps.py tests/test_timestamps.py
git commit -m "feat(core): first_timestamp / comment_has_timestamp helpers"
```

---

### Task 4: core/channel.py — Worker용 함수

**Files:**
- Create: `stampcut/core/channel.py`
- Test: `tests/test_channel.py`

**Interfaces:**
- Consumes: `ChannelInfo`, `resolve_channel`, `fetch_channel_videos` (Task 1–2), `comment_has_timestamp` (Task 3), `ff.Cancelled`
- Produces: `ChannelPage(channel: ChannelInfo, videos: list[VideoInfo], next_token: str | None)`; `find_channel_videos(client, text, limit=200, page_token=None, channel=None, progress=_noop, cancel=None) -> ChannelPage`; `load_comments(client, video, progress=_noop, cancel=None) -> list[RawComment]`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_channel.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_channel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stampcut.core.channel'`

- [ ] **Step 3: 구현**

`stampcut/core/channel.py`:

```python
"""채널 영상 찾기: 채널 해석 → 댓글 있는 영상 목록, 영상별 댓글. GUI는 이 함수들을 Worker에서 부른다."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from stampcut.core import ffmpeg as ff
from stampcut.core.models import ChannelInfo, RawComment, VideoInfo
from stampcut.core.timestamps import comment_has_timestamp

ProgressFn = Callable[[str, int, int, str], None]


def _noop(stage: str, done: int, total: int, message: str) -> None:
    pass


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ff.Cancelled()


@dataclass
class ChannelPage:
    channel: ChannelInfo
    videos: list[VideoInfo]
    next_token: str | None  # 더 보기용. None이면 마지막 페이지


def find_channel_videos(
    client,
    text: str,
    limit: int = 200,
    page_token: str | None = None,
    channel: ChannelInfo | None = None,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
) -> ChannelPage:
    """channel이 없으면 text로 채널을 찾고, 업로드 목록에서 댓글 있는 영상을 limit개까지 받는다."""
    if channel is None:
        progress("channel", 0, 2, "채널 찾는 중")
        channel = client.resolve_channel(text)
    _check(cancel)
    progress("channel", 1, 2, f"{channel.title} 영상 목록 받는 중")
    videos, next_token = client.fetch_channel_videos(channel, limit=limit, page_token=page_token)
    progress("channel", 2, 2, f"{channel.title} — 댓글 있는 영상 {len(videos)}개")
    return ChannelPage(channel, videos, next_token)


def load_comments(client, video: VideoInfo, progress: ProgressFn = _noop, cancel: threading.Event | None = None) -> list[RawComment]:
    """영상의 댓글 전부. 타임스탬프 댓글이 앞에 오고, 그 안에서는 좋아요 많은 순. 댓글이 막힌 영상은 []."""
    progress("comments", 0, 1, f"{video.short_name} 댓글 받는 중")
    _check(cancel)
    comments = client.fetch_all_comments(video.video_id)
    ordered = sorted(comments, key=lambda c: (not comment_has_timestamp(c, video), -c.like_count))
    progress("comments", 1, 1, f"{video.short_name} 댓글 {len(ordered)}개")
    return ordered
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_channel.py -q`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add stampcut/core/channel.py tests/test_channel.py
git commit -m "feat(core): channel.find_channel_videos / load_comments for the channel finder"
```

---

### Task 5: UrlPanel.add_urls

**Files:**
- Modify: `stampcut/gui/url_panel.py`
- Test: `tests/test_url_panel.py`

**Interfaces:**
- Produces: `UrlPanel.add_urls(urls: list[str]) -> int` — id 기준 중복 제거, 기존 텍스트 유지, 추가 개수 반환, `textChanged` 1회.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_url_panel.py` 끝에 추가:

```python
def test_add_urls_appends_without_duplicates(qtbot):
    p = UrlPanel()
    qtbot.addWidget(p)
    p.urls_edit.setPlainText("https://youtu.be/AAAAAAAAAAA\n")
    changes = []
    p.urls_edit.textChanged.connect(lambda: changes.append(1))
    n = p.add_urls([
        "https://www.youtube.com/watch?v=AAAAAAAAAAA",  # 이미 있음 (같은 id)
        "https://www.youtube.com/watch?v=BBBBBBBBBBB",
        "https://youtu.be/BBBBBBBBBBB",  # 같은 호출 안 중복
        "junk",
    ])
    assert n == 1
    assert p.urls() == ["https://youtu.be/AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB"]
    assert len(changes) == 1
    assert p.add_urls(["https://youtu.be/BBBBBBBBBBB"]) == 0 and len(changes) == 1


def test_add_urls_into_empty_panel(qtbot):
    p = UrlPanel()
    qtbot.addWidget(p)
    assert p.add_urls(["https://youtu.be/AAAAAAAAAAA"]) == 1
    assert p.urls_edit.toPlainText() == "https://youtu.be/AAAAAAAAAAA"
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_url_panel.py -q`
Expected: FAIL — `AttributeError: 'UrlPanel' object has no attribute 'add_urls'`

- [ ] **Step 3: 구현**

`url_panel.py`의 `set_busy` 앞에 추가:

```python
    def add_urls(self, urls: list[str]) -> int:
        """이미 있는 영상(id 기준)은 건너뛰고 새 줄로 덧붙인다. 추가한 개수를 돌려준다."""
        known = {parse_video_id(line) for line in self._lines()}
        fresh: list[str] = []
        for u in urls:
            vid = parse_video_id(u)
            if vid and vid not in known:
                known.add(vid)
                fresh.append(u.strip())
        if fresh:
            self.urls_edit.appendPlainText("\n".join(fresh))  # textChanged 한 번
        return len(fresh)
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_url_panel.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add stampcut/gui/url_panel.py tests/test_url_panel.py
git commit -m "feat(gui): UrlPanel.add_urls appends new video URLs without duplicates"
```

---

### Task 6: 표 모델 — ChannelVideoModel, CommentModel

**Files:**
- Create: `stampcut/gui/channel_models.py`
- Test: `tests/test_channel_models.py`

**Interfaces:**
- Consumes: `VideoInfo`, `RawComment`, `first_timestamp` (Task 3), `format_time`
- Produces: 상수 `V_CHECK, V_DATE, V_TITLE, V_LENGTH, V_COMMENTS, V_STAMPS = range(6)`, `C_TIME, C_AUTHOR, C_LIKES, C_TEXT = range(4)`; `ChannelVideoModel` (signal `checked_changed`; `clear()`, `append(videos)`, `video_at(row)`, `row_of(video_id)`, `set_timestamp_count(video_id, n)`, `checked_urls() -> list[str]`); `CommentModel` (`set_comments(video | None, comments)`, `timestamp_count() -> int`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_channel_models.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_channel_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stampcut.gui.channel_models'`

- [ ] **Step 3: 구현**

`stampcut/gui/channel_models.py`:

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_channel_models.py -q`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
git add stampcut/gui/channel_models.py tests/test_channel_models.py
git commit -m "feat(gui): channel video / comment table models"
```

---

### Task 7: ChannelDialog

**Files:**
- Create: `stampcut/gui/channel_dialog.py`
- Test: `tests/test_channel_dialog.py`

**Interfaces:**
- Consumes: `channel.find_channel_videos`, `channel.load_comments`, `ChannelPage` (Task 4); `parse_channel_ref`, `ChannelNotFound`, `ApiKeyError`, `QuotaError`; `ChannelVideoModel`, `CommentModel`, `V_TITLE` (Task 6); `comment_has_timestamp` (Task 3); `gui.workers.Worker`.
- Produces: `BAD_REF_HINT` 상수; `ChannelDialog(client, limit=200, parent=None)` with signals `urls_selected(list)`, `page_loaded(object)`, `comments_loaded(object)`; methods `set_default_ref(text)`, `busy() -> bool`, `find()`, `load_more()`; attributes `client`, `ref_edit`, `find_btn`, `more_btn`, `status`, `videos`, `video_model`, `comments`, `comment_model`, `add_btn`, `close_btn`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_channel_dialog.py`:

```python
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from stampcut.core.models import ChannelInfo, RawComment, VideoInfo
from stampcut.core.youtube_api import ChannelNotFound
from stampcut.gui.channel_dialog import BAD_REF_HINT, ChannelDialog
from stampcut.gui.channel_models import C_TIME, V_CHECK, V_STAMPS

CH = ChannelInfo("UC" + "x" * 22, "문성FC", "UU" + "x" * 22)
A, B, C = "A" * 11, "B" * 11, "C" * 11


def _video(vid, title, comments):
    return VideoInfo(0, vid, f"https://www.youtube.com/watch?v={vid}", title, title, "문성FC", datetime(2026, 8, 20, tzinfo=timezone.utc), 1449, comments)


class FakeClient:
    def __init__(self, pages, comments):
        self.pages, self.comments, self.calls = pages, comments, []
        self.fail = None

    def resolve_channel(self, text):
        self.calls.append(("resolve", text))
        if self.fail:
            raise self.fail
        return CH

    def fetch_channel_videos(self, ch, limit=200, page_token=None):
        self.calls.append(("videos", page_token))
        return self.pages[page_token]

    def fetch_all_comments(self, video_id):
        self.calls.append(("comments", video_id))
        return list(self.comments.get(video_id, []))


def _client():
    a, b, c = _video(A, "1게임", 3), _video(B, "2게임", 1), _video(C, "3게임", 5)
    pages = {None: ([a, b], "P2"), "P2": ([c], None)}
    comments = {A: [RawComment("c1", "그냥", "@x", 9, False), RawComment("c2", "12:38 원더골", "@y", 1, False)], B: []}
    return FakeClient(pages, comments)


def _dialog(qtbot, client=None):
    dlg = ChannelDialog(client or _client(), limit=2)
    qtbot.addWidget(dlg)
    return dlg


def _find(qtbot, dlg, text="@moonsungfc"):
    dlg.ref_edit.setText(text)
    with qtbot.waitSignal(dlg.page_loaded, timeout=5000):
        dlg.find_btn.click()


def test_find_lists_videos_and_more_appends(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    assert dlg.video_model.rowCount() == 2 and dlg.more_btn.isEnabled() and dlg.find_btn.isEnabled()
    assert dlg.status.text() == "문성FC — 댓글 있는 영상 2개 (더 보기 가능)"
    with qtbot.waitSignal(dlg.page_loaded, timeout=5000):
        dlg.more_btn.click()
    assert dlg.video_model.rowCount() == 3 and not dlg.more_btn.isEnabled()
    assert dlg.status.text() == "문성FC — 댓글 있는 영상 3개"
    assert dlg.client.calls == [("resolve", "@moonsungfc"), ("videos", None), ("videos", "P2")]  # 더 보기는 채널을 다시 찾지 않는다


def test_find_again_resets_list_and_cache(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    _find(qtbot, dlg, CH.channel_id)
    assert dlg.video_model.rowCount() == 2 and dlg.comment_model.rowCount() == 0
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    assert [c for c in dlg.client.calls if c[0] == "comments"] == [("comments", A), ("comments", A)]  # 캐시가 비워졌다


def test_bad_ref_shows_hint_without_request(qtbot):
    dlg = _dialog(qtbot)
    dlg.ref_edit.setText("https://www.youtube.com/c/moonsung")
    dlg.find()
    assert dlg.status.text() == BAD_REF_HINT and dlg.client.calls == []


def test_selecting_video_loads_comments_once_and_marks_timestamps(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(0)
    assert dlg.comment_model.rowCount() == 2
    assert dlg.comment_model.data(dlg.comment_model.index(0, C_TIME)) == "12:38"  # 타임스탬프 댓글이 먼저
    assert dlg.video_model.data(dlg.video_model.index(0, V_STAMPS)) == 1
    assert dlg.status.text() == "1게임: 댓글 2개, 타임스탬프 1개"
    with qtbot.waitSignal(dlg.comments_loaded, timeout=5000):
        dlg.videos.selectRow(1)
    assert dlg.comment_model.rowCount() == 0 and dlg.status.text() == "2게임: 댓글이 없거나 막힌 영상입니다"
    assert dlg.video_model.data(dlg.video_model.index(1, V_STAMPS)) == 0
    dlg.videos.selectRow(0)  # 캐시 → 요청 없이 즉시 표시
    assert dlg.comment_model.rowCount() == 2
    assert [c for c in dlg.client.calls if c[0] == "comments"] == [("comments", A), ("comments", B)]


def test_check_enables_add_and_emits_urls(qtbot):
    dlg = _dialog(qtbot)
    _find(qtbot, dlg)
    assert not dlg.add_btn.isEnabled()
    dlg.video_model.setData(dlg.video_model.index(1, V_CHECK), Qt.Checked, Qt.CheckStateRole)
    assert dlg.add_btn.isEnabled()
    with qtbot.waitSignal(dlg.urls_selected, timeout=1000) as blocker:
        dlg.add_btn.click()
    assert blocker.args == [[f"https://www.youtube.com/watch?v={B}"]]
    assert dlg.video_model.rowCount() == 2  # 창은 닫히지 않고 목록도 그대로


def test_set_default_ref_only_when_empty(qtbot):
    dlg = _dialog(qtbot)
    dlg.set_default_ref("https://youtu.be/" + A)
    assert dlg.ref_edit.text() == "https://youtu.be/" + A
    dlg.set_default_ref("@other")
    assert dlg.ref_edit.text() == "https://youtu.be/" + A


def test_channel_not_found_warns_and_reenables(qtbot, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    client = _client()
    client.fail = ChannelNotFound("@nobody")
    dlg = _dialog(qtbot, client)
    dlg.ref_edit.setText("@nobody")
    dlg.find()
    qtbot.waitUntil(lambda: bool(warned), timeout=5000)
    assert warned[0] == "채널을 찾을 수 없습니다: @nobody"
    qtbot.waitUntil(lambda: dlg.find_btn.isEnabled(), timeout=5000)
    assert dlg.status.text() == "채널을 찾을 수 없습니다: @nobody" and not dlg.more_btn.isEnabled()
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_channel_dialog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stampcut.gui.channel_dialog'`

- [ ] **Step 3: 구현**

`stampcut/gui/channel_dialog.py`:

```python
"""채널 영상 찾기 창: 댓글 있는 영상 목록 → 영상별 댓글(타임스탬프 강조) → 체크한 URL을 메인 화면에 추가."""
from __future__ import annotations

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from stampcut.core import channel as channel_mod
from stampcut.core.models import ChannelInfo, RawComment, VideoInfo
from stampcut.core.timestamps import comment_has_timestamp
from stampcut.core.youtube_api import ApiKeyError, ChannelNotFound, QuotaError, parse_channel_ref
from stampcut.gui.channel_models import V_TITLE, ChannelVideoModel, CommentModel
from stampcut.gui.workers import Worker

BAD_REF_HINT = "채널 주소(@핸들, channel/UC…)나 영상 주소를 넣으세요. /c/·/user/ 주소는 지원하지 않습니다."


def _job(fn, *args, progress, cancel, **kwargs):
    """core 예외를 사용자 문구로 바꾼다 (main_window._analyze_job과 같은 규칙)."""
    try:
        return fn(*args, progress=progress, cancel=cancel, **kwargs)
    except ApiKeyError as e:
        raise RuntimeError(f"API 키가 잘못되었거나 YouTube Data API v3가 사용 설정되지 않았습니다.\n설정에서 키를 확인하세요.\n({e})") from e
    except QuotaError as e:
        raise RuntimeError(f"오늘의 API 할당량을 다 썼습니다. 내일 오후 4시(태평양 자정)에 초기화됩니다.\n({e})") from e
    except ChannelNotFound as e:
        raise RuntimeError(f"채널을 찾을 수 없습니다: {e.ref}") from e


class ChannelDialog(QDialog):
    urls_selected = Signal(list)
    page_loaded = Signal(object)  # ChannelPage
    comments_loaded = Signal(object)  # VideoInfo

    def __init__(self, client, limit: int = 200, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client, self.limit = client, limit
        self.setWindowTitle("채널 영상 찾기")
        self.setModal(False)
        self.resize(1100, 700)
        self.pool = QThreadPool.globalInstance()
        self._worker: Worker | None = None
        self._channel: ChannelInfo | None = None
        self._next_token: str | None = None
        self._comment_cache: dict[str, list[RawComment]] = {}
        self._pending_video: VideoInfo | None = None  # 로드 중 고른 마지막 영상

        self.ref_edit = QLineEdit()
        self.ref_edit.setPlaceholderText("채널 주소(@핸들, channel/UC…) 또는 그 채널의 영상 주소")
        self.ref_edit.returnPressed.connect(self.find)
        self.find_btn = QPushButton("찾기")
        self.find_btn.clicked.connect(self.find)
        self.more_btn = QPushButton("더 보기")
        self.more_btn.setEnabled(False)
        self.more_btn.clicked.connect(self.load_more)
        self.status = QLabel("")

        self.video_model = ChannelVideoModel(self)
        self.video_model.checked_changed.connect(self._sync_add_btn)
        self.videos = QTableView()
        self.videos.setModel(self.video_model)
        self.videos.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.videos.setSelectionMode(QAbstractItemView.SingleSelection)
        self.videos.verticalHeader().setVisible(False)
        header = self.videos.horizontalHeader()
        for col in range(self.video_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == V_TITLE else QHeaderView.ResizeToContents)
        self.videos.selectionModel().currentRowChanged.connect(self._on_video_selected)

        self.comment_model = CommentModel(self)
        self.comments = QTableView()
        self.comments.setModel(self.comment_model)
        self.comments.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.comments.verticalHeader().setVisible(False)
        self.comments.horizontalHeader().setStretchLastSection(True)
        self.comments.setWordWrap(False)

        self.add_btn = QPushButton("체크한 영상 URL 목록에 추가")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._emit_urls)
        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.close)

        top = QHBoxLayout()
        top.addWidget(self.ref_edit, 1)
        top.addWidget(self.find_btn)
        top.addWidget(self.more_btn)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.videos)
        splitter.addWidget(self.comments)
        splitter.setSizes([600, 500])
        bottom = QHBoxLayout()
        bottom.addWidget(self.add_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.close_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.status)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

    # --- 외부 API ---
    def set_default_ref(self, text: str) -> None:
        """입력창이 비어 있을 때만 채운다 (메인 창이 열 때 첫 URL을 넣어 준다)."""
        if not self.ref_edit.text().strip():
            self.ref_edit.setText(text)

    def busy(self) -> bool:
        return self._worker is not None and not self._worker.done

    # --- 찾기 / 더 보기 ---
    def find(self) -> None:
        if self.busy():
            return
        text = self.ref_edit.text().strip()
        if parse_channel_ref(text) is None:
            self.status.setText(BAD_REF_HINT)
            return
        self._channel, self._next_token, self._pending_video = None, None, None
        self._comment_cache.clear()
        self.video_model.clear()
        self.comment_model.set_comments(None, [])
        self._start_page(text, None)

    def load_more(self) -> None:
        if self.busy() or self._channel is None or self._next_token is None:
            return
        self._start_page(self.ref_edit.text().strip(), self._next_token)

    def _start_page(self, text: str, token: str | None) -> None:
        w = Worker(_job, channel_mod.find_channel_videos, self.client, text, limit=self.limit, page_token=token, channel=self._channel)
        w.signals.finished.connect(self._on_page)
        w.signals.failed.connect(self._on_failed)
        w.signals.cancelled.connect(lambda: self._set_busy(False))
        self._run(w)

    def _on_page(self, page) -> None:
        self._channel, self._next_token = page.channel, page.next_token
        self.video_model.append(page.videos)
        self._set_busy(False)
        more = " (더 보기 가능)" if page.next_token else ""
        self.status.setText(f"{page.channel.title} — 댓글 있는 영상 {self.video_model.rowCount()}개{more}")
        self.page_loaded.emit(page)

    # --- 댓글 ---
    def _on_video_selected(self, current, _previous) -> None:
        if not current.isValid():
            return
        video = self.video_model.video_at(current.row())
        cached = self._comment_cache.get(video.video_id)
        if cached is not None:
            self._show_comments(video, cached)
            return
        if self.busy():
            self._pending_video = video  # 끝나면 마지막 선택을 이어서 불러온다
            return
        self._start_comments(video)

    def _start_comments(self, video: VideoInfo) -> None:
        w = Worker(_job, channel_mod.load_comments, self.client, video)
        w.signals.finished.connect(lambda comments, v=video: self._on_comments(v, comments))
        w.signals.failed.connect(self._on_failed)
        w.signals.cancelled.connect(lambda: self._set_busy(False))
        self._run(w)

    def _on_comments(self, video: VideoInfo, comments: list) -> None:
        self._set_busy(False)
        self._comment_cache[video.video_id] = comments
        self.video_model.set_timestamp_count(video.video_id, sum(1 for c in comments if comment_has_timestamp(c, video)))
        self._show_comments(video, comments)
        self.comments_loaded.emit(video)
        self._run_pending()

    def _show_comments(self, video: VideoInfo, comments: list) -> None:
        self.comment_model.set_comments(video, comments)
        if comments:
            self.status.setText(f"{video.short_name}: 댓글 {len(comments)}개, 타임스탬프 {self.comment_model.timestamp_count()}개")
        else:
            self.status.setText(f"{video.short_name}: 댓글이 없거나 막힌 영상입니다")

    def _run_pending(self) -> None:
        pending, self._pending_video = self._pending_video, None
        if pending is not None:
            cached = self._comment_cache.get(pending.video_id)
            if cached is not None:
                self._show_comments(pending, cached)
            else:
                self._start_comments(pending)

    # --- 공통 ---
    def _run(self, w: Worker) -> None:
        self._worker = w
        self._set_busy(True)
        w.signals.progress.connect(lambda stage, done, total, msg: self.status.setText(msg))
        self.pool.start(w)

    def _set_busy(self, busy: bool) -> None:
        self.find_btn.setEnabled(not busy)
        self.ref_edit.setEnabled(not busy)
        self.more_btn.setEnabled(not busy and self._next_token is not None)

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.status.setText(msg.splitlines()[0])
        QMessageBox.warning(self, "채널 영상 찾기", msg)
        self._run_pending()

    def _sync_add_btn(self) -> None:
        self.add_btn.setEnabled(bool(self.video_model.checked_urls()))

    def _emit_urls(self) -> None:
        urls = self.video_model.checked_urls()
        if urls:
            self.urls_selected.emit(urls)

    def closeEvent(self, event) -> None:
        if self.busy():  # 진행 중 워커가 닫힌 창을 건드리지 않도록 (메인 창과 같은 패턴)
            self._worker.cancel.set()
            self._worker.signals.blockSignals(True)
        super().closeEvent(event)
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_channel_dialog.py -q`
Expected: 7 passed, 경고 없음

- [ ] **Step 5: 커밋**

```bash
git add stampcut/gui/channel_dialog.py tests/test_channel_dialog.py
git commit -m "feat(gui): ChannelDialog — videos with comments, per-video comments, add URLs"
```

---

### Task 8: MainWindow 연결 + README

**Files:**
- Modify: `stampcut/gui/main_window.py`
- Modify: `README.md`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `ChannelDialog` (Task 7), `UrlPanel.add_urls` (Task 5), `YouTubeClient`
- Produces: `MainWindow.channel_action`, `open_channel_finder()`, `_on_channel_urls(urls)`, `_channel_dialog: ChannelDialog | None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_main_window.py` 끝에 추가:

```python
def test_channel_finder_requires_api_key(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    w = MainWindow(Settings())
    qtbot.addWidget(w)
    monkeypatch.setattr(w, "open_settings", lambda: None)
    w.open_channel_finder()
    assert warned and "API 키" in warned[0] and w._channel_dialog is None


def test_channel_finder_opens_with_default_ref_and_adds_urls(qtbot, monkeypatch):
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: None)  # 실제 설정 파일을 건드리지 않는다
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.show()
    assert w.channel_action.text() == "채널 영상 찾기" and w.channel_action.isEnabled()
    w.url_panel.urls_edit.setPlainText("https://youtu.be/AAAAAAAAAAA")
    w.open_channel_finder()
    dlg = w._channel_dialog
    assert dlg is not None and dlg.isVisible() and dlg.ref_edit.text() == "https://youtu.be/AAAAAAAAAAA"
    dlg.urls_selected.emit(["https://youtu.be/AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB"])
    assert w.url_panel.urls() == ["https://youtu.be/AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB"]
    assert w.status_panel.message.text() == "URL 1개 추가됨 — 댓글 분석을 누르세요"
    dlg.urls_selected.emit(["https://youtu.be/BBBBBBBBBBB"])
    assert w.status_panel.message.text() == "이미 목록에 있는 영상입니다"
    w.open_channel_finder()
    assert w._channel_dialog is dlg  # 같은 API 키면 창을 재사용
    w.apply_settings(replace(w.settings, api_key="OTHER"))
    w.open_channel_finder()
    assert w._channel_dialog is not dlg  # 키가 바뀌면 새 클라이언트로 다시 만든다
    dlg2 = w._channel_dialog
    w._set_busy(True)
    assert w.channel_action.isEnabled()  # 분석·렌더 중에도 열 수 있다
    w.close()
    assert not dlg2.isVisible()
```

(`replace`는 이 파일 상단에 `from dataclasses import replace`가 이미 있다 — 없으면 추가.)

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_main_window.py -q`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'open_channel_finder'`

- [ ] **Step 3: main_window.py 구현**

import 추가:

```python
from stampcut.gui.channel_dialog import ChannelDialog
```

생성자에서 `self.settings_action = toolbar.addAction("⚙ 설정", self.open_settings)` 아래에:

```python
        self.channel_action = toolbar.addAction("채널 영상 찾기", self.open_channel_finder)
        self._channel_dialog: ChannelDialog | None = None
        self._channel_dialog_key = ""
```

`_on_output_dir_changed` 아래(또는 "--- 렌더 ---" 앞)에 추가:

```python
    # --- 채널 영상 찾기 ---
    def open_channel_finder(self) -> None:
        if not self.settings.api_key:
            self._warn("YouTube API 키가 필요합니다. 설정에서 입력하세요.")
            self.open_settings()
            return
        if self._channel_dialog is None or self._channel_dialog_key != self.settings.api_key:
            if self._channel_dialog is not None:
                self._channel_dialog.close()
            self._channel_dialog = ChannelDialog(YouTubeClient(self.settings.api_key), parent=self)
            self._channel_dialog_key = self.settings.api_key
            self._channel_dialog.urls_selected.connect(self._on_channel_urls)
        urls = self.url_panel.urls()
        if urls:
            self._channel_dialog.set_default_ref(urls[0])
        self._channel_dialog.show()
        self._channel_dialog.raise_()
        self._channel_dialog.activateWindow()

    def _on_channel_urls(self, urls: list) -> None:
        n = self.url_panel.add_urls(list(urls))
        if not self.status_panel.has_result():  # 완성된 결과의 열기/재생 버튼은 지우지 않는다
            self.status_panel.set_idle(f"URL {n}개 추가됨 — 댓글 분석을 누르세요" if n else "이미 목록에 있는 영상입니다")
```

`closeEvent`에서 `self._flush_autosave()` 앞에 추가:

```python
        if self._channel_dialog is not None:
            self._channel_dialog.close()
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_main_window.py -q`
Expected: 모두 PASS

- [ ] **Step 5: README 갱신**

`README.md` 사용법 2번 항목("유튜브 URL을 한 줄에 하나씩 붙여넣고 …") 바로 아래에 하위 항목 추가:

```markdown
   - URL을 모르면 툴바의 **채널 영상 찾기**를 누르세요. 채널 주소(`@핸들`, `channel/UC…`)나 그 채널의 영상 주소를 넣고 **찾기**를 누르면 최근 200개 중 **댓글이 달린 영상만** 목록에 뜹니다(**더 보기**로 이어서). 영상을 클릭하면 댓글이 오른쪽에 나오고 타임스탬프가 적힌 댓글은 위쪽에 굵게 표시되며, 영상 행에 타임스탬프 개수가 채워집니다. 쓸 영상을 체크하고 **체크한 영상 URL 목록에 추가**를 누르면 URL 칸에 들어갑니다. (`/c/…`, `/user/…` 주소는 지원하지 않습니다.)
```

- [ ] **Step 6: 전체 테스트**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: 전부 PASS (272 + 새 테스트), 실패 0

- [ ] **Step 7: 커밋**

```bash
git add stampcut/gui/main_window.py tests/test_main_window.py README.md
git commit -m "feat(gui): channel finder action in MainWindow; docs: usage"
```

---

## 실행 후 확인 (수동)

1. `python -m stampcut` → 툴바에 **채널 영상 찾기**. API 키 없이 누르면 경고 + 설정 창.
2. `@핸들` 또는 영상 주소 입력 → 찾기 → 댓글 있는 영상만, 최신순, 상태줄에 개수. 더 보기로 이어짐.
3. 영상 클릭 → 댓글 표(타임스탬프 댓글 위·굵게·시각), 영상 행 타임스탬프 수 채워짐. 다른 영상 클릭 후 되돌아오면 즉시 표시(재요청 없음).
4. 체크 → "체크한 영상 URL 목록에 추가" → 메인 URL 칸에 새 줄로 추가, 중복은 건너뜀, 상태줄 문구.
5. `/c/…` 주소 → 요청 없이 안내 문구. 없는 핸들 → 경고창.
