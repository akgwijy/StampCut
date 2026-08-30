# 채널 영상 찾기(댓글 있는 영상 목록 + 댓글 보기) 설계

날짜: 2026-08-28
상태: 승인됨

## 목적

영상을 하나하나 열어 보지 않고도, 한 채널에서 **댓글이 달린 영상**만 골라 URL을 확보하고,
각 영상의 댓글(특히 타임스탬프가 적힌 댓글)을 바로 훑어본 뒤, 고른 영상을 메인 화면 URL
목록에 넣어 곧장 "댓글 분석"으로 이어가게 한다. 하이라이트 소스 영상을 찾는 시간을 줄인다.

## 결정 사항 (사용자 확정)

- 채널 지정: **입력창 하나에 무엇이든** — 채널 주소(`@핸들`, `channel/UC…`) 또는 그 채널의
  영상 주소. 메인 화면에 URL이 있으면 그 첫 URL을 입력창 기본값으로.
- 범위: **최근 N개, 기본 200개**(최신순). "더 보기"로 다음 페이지. 댓글 0개 영상은 항상 제외.
- 선택 후 동작: **체크한 영상을 메인 URL 목록에 추가**(id 기준 중복 제거).
- 댓글: **영상을 클릭할 때** 불러오고, **타임스탬프 댓글을 위로·굵게** 표시. 영상 행에
  "타임스탬프 n개"를 채움. 목록 생성 시 모든 영상의 댓글을 미리 받지는 않는다.
- UI: **별도 창**(툴바 버튼 → 모달 아닌 창). 메인 화면 레이아웃은 건드리지 않는다.

## 1. core (Qt 없음)

### 1-1. 모델 (models.py)

영상은 기존 `VideoInfo`를 그대로 쓴다 (`index` = 목록 순서, `short_name` =
`short_name_from_title(title, index)`, `channel_title` = 채널명). 새 타입은 하나:

```python
@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    uploads_playlist_id: str
```

### 1-2. youtube_api.py 확장

```python
class ChannelNotFound(YouTubeApiError):
    def __init__(self, ref: str, message: str | None = None): ...   # .ref 보존

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

def parse_channel_ref(text: str) -> tuple[str, str] | None
```

- 반환: `("handle", "@x")` / `("id", "UC…")` / `("video", "<11자 id>")` / `None`.
- 인식 규칙(앞뒤 공백 제거 후):
  - `youtube.com/@x…` 또는 맨 `@x` → handle (`@` 포함, 뒤의 `/`·`?` 이후는 버림)
  - `youtube.com/channel/UC…` 또는 맨 `UC…`(총 24자) → id
  - 그 외는 기존 `parse_video_id`로 시도 → 성공하면 video
  - `/c/…`, `/user/…` 는 인식하지 않는다(→ `None`; GUI가 안내 문구를 보여준다)

```python
def resolve_channel(self, text: str) -> ChannelInfo
```

- `parse_channel_ref`가 `None`이면 `ValueError(text)`.
- handle → `channels.list(part=snippet,contentDetails, forHandle=@x)`;
  id → `channels.list(part=snippet,contentDetails, id=UC…)`;
  video → `videos.list(part=snippet, id=vid)`의 `snippet.channelId`로 다시 `channels.list(id=)`.
- `items`가 비면 `ChannelNotFound(text)`. 결과 `ChannelInfo(id, snippet.title,
  contentDetails.relatedPlaylists.uploads)`.

```python
def fetch_channel_videos(self, channel: ChannelInfo, limit: int = 200, page_token: str | None = None) -> tuple[list[VideoInfo], str | None]
```

- `playlistItems.list(part=contentDetails, playlistId=uploads, maxResults=50, pageToken=…)`를
  **모은 항목 수가 `limit`에 닿을 때까지** 페이징. 마지막으로 받은 응답의 `nextPageToken`을
  두 번째 반환값으로 (없으면 `None`). `limit`은 페이지 단위(50)로 올림해 잘라 낸다.
- id 묶음(50개씩)을 `_video_items(ids)`로 `videos.list(part=snippet,contentDetails,statistics)`
  조회. `_video_items`는 기존 `fetch_video_infos`에서 분리한 내부 헬퍼(동작 불변).
- `statistics.commentCount`가 없거나 0인 영상은 **제외**. 반환 순서는 재생목록 순서(최신순).
- `VideoInfo.url` = `https://www.youtube.com/watch?v=<id>`, `index`는 반환 목록에서의 순서
  (페이지를 이어 붙일 때는 GUI가 다시 매긴다), `channel_title` = `channel.title`.

### 1-3. timestamps.py

```python
def comment_has_timestamp(comment: RawComment, video: VideoInfo) -> bool
```

- `comment.text`의 어느 줄이든 `find_timestamps(line, video.duration)`이 비어 있지 않으면 True.
- `first_timestamp(comment, video) -> int | None` — 등장 순서상 첫 타임스탬프(초). 댓글 표의
  "시각" 열과 정렬에 쓴다.

### 1-4. 신규 `core/channel.py` (Worker가 부르는 함수)

```python
@dataclass
class ChannelPage:
    channel: ChannelInfo
    videos: list[VideoInfo]
    next_token: str | None

def find_channel_videos(client, text, limit=200, page_token=None, channel=None, progress=_noop, cancel=None) -> ChannelPage
def load_comments(client, video, progress=_noop, cancel=None) -> list[RawComment]
```

- `find_channel_videos`: `channel`이 `None`이면 `client.resolve_channel(text)` (progress
  `"channel"`, "채널 찾는 중"), 이어서 `fetch_channel_videos(channel, limit, page_token)`
  (progress `"channel"`, "영상 목록 받는 중"). `cancel`은 두 호출 사이에서 확인.
- `load_comments`: `client.fetch_all_comments(video.video_id)` 결과를
  **타임스탬프 댓글 먼저, 그 안에서 좋아요 내림차순, 나머지도 좋아요 내림차순**으로 정렬해
  반환. `CommentsDisabled`는 `fetch_all_comments`가 이미 빈 목록으로 돌려준다.
- 기존 `_analyze_job`처럼 `ApiKeyError`/`QuotaError`를 사용자 문구의 `RuntimeError`로 바꾸는
  래핑은 GUI 쪽(`channel_dialog._job`)에서 한다.

## 2. GUI

### 2-1. url_panel.py

```python
def add_urls(self, urls: list[str]) -> int
```

- 현재 줄들의 `parse_video_id` 집합을 만들고, 그 안에 없는 URL만 새 줄로 덧붙인다.
  기존 텍스트는 유지(끝에 개행이 없으면 넣고 붙임). 추가한 개수를 돌려준다.
- `setPlainText`가 아니라 `appendPlainText`로 붙여 `textChanged`가 한 번만 나게 한다.

### 2-2. 신규 `gui/channel_dialog.py`

`class ChannelDialog(QDialog)` — 제목 "채널 영상 찾기", `setModal(False)`, 크기 1100×700.
생성 인자 `client: YouTubeClient`(또는 같은 메서드를 가진 가짜), `limit: int = 200`.

레이아웃
- 1행: `ref_edit`(QLineEdit, 플레이스홀더 "채널 주소(@핸들, channel/UC…) 또는 그 채널의 영상 주소") ·
  `find_btn` "찾기"(Enter로도) · `more_btn` "더 보기"(다음 페이지 있을 때만 활성).
- 2행: `status` 라벨 — "문성FC — 댓글 있는 영상 37개 (더 보기 가능)" / 진행 메시지 / 오류.
- 가운데 `QSplitter(Horizontal)`:
  - 왼쪽 `videos: QTableView` + `ChannelVideoModel(QAbstractTableModel)`
    열: `COL_CHECK`(체크) · 날짜(`yy.mm.dd`, KST) · 제목 · 길이(`format_time`) · 댓글 수 ·
    타임스탬프 수(로드 전 "–"). 행 = `VideoInfo`, 체크 상태와 타임스탬프 수는 모델이 보관
    (`dict[video_id]`). 제목 열 Stretch, 나머지 ResizeToContents. 단일 행 선택.
  - 오른쪽 `comments: QTableView` + `CommentModel(QAbstractTableModel)`
    열: 시각(첫 타임스탬프, 없으면 빈칸) · 작성자 · 좋아요 · 내용(한 줄로 접음, 툴팁에 전문).
    타임스탬프 댓글 행은 굵게(`FontRole`). 행 순서는 `load_comments` 결과 그대로.
- 아래: `add_btn` "체크한 영상 URL 목록에 추가"(체크 0개면 비활성) · 오른쪽 정렬로 `닫기`.

동작
- `find_btn`/Enter: 입력이 `parse_channel_ref`로 인식되지 않으면 `status`에
  "채널 주소(@핸들, channel/UC…)나 영상 주소를 넣으세요. /c/·/user/ 주소는 지원하지 않습니다."
  표시하고 끝. 인식되면 모델 비우기, 댓글 캐시 비우기, `_run(find_channel_videos, …)`.
- 결과 `ChannelPage`: `channel`·`next_token` 보관, 영상을 모델에 추가(`index` 재부여),
  `status` 갱신, `more_btn` 활성 여부 = `next_token is not None`.
- `more_btn`: 같은 채널로 `page_token=next_token` 조회 → 모델에 **덧붙임**.
- 영상 행 선택(`currentRowChanged`): 캐시에 있으면 즉시 표시. 없으면 `_run(load_comments, …)`
  → 결과를 캐시(`dict[video_id, list[RawComment]]`)에 넣고 표시, 타임스탬프 수 =
  `sum(comment_has_timestamp)`를 모델에 반영. 로드 중 다른 행을 고르면 `_pending_video`에
  기억했다가 끝난 뒤 이어서 로드한다(항상 마지막 선택만).
- 댓글이 3,000개를 넘는 영상은 할당량(100개당 1유닛)을 안내하는 확인 대화상자를 거친다. 아니오면 불러오지 않고 status에 표시.
- `add_btn`: 체크된 영상의 `url` 목록으로 `urls_selected(list)` 발생. 창은 닫지 않는다.
- 워커: 기존 `Worker`(QThreadPool) 사용, 한 번에 하나(`self._worker`). 실행 중엔
  `find_btn`·`more_btn`·`ref_edit` 비활성, 댓글 로드 중엔 영상 표 선택은 허용(대기 규칙).
  `progress` → `status` 메시지. `failed` → `status`에 오류 + `QMessageBox.warning`.
  `closeEvent` → 진행 중 워커 `cancel.set()` 후 시그널 차단(메인 창과 같은 패턴).
- `_job(fn, *args)`: `ApiKeyError`/`QuotaError`를 `_analyze_job`과 같은 문구의 `RuntimeError`로,
  `ChannelNotFound` → `RuntimeError("채널을 찾을 수 없습니다: {ref}")`.
- `set_default_ref(text)`: 입력창이 비어 있을 때만 채운다(메인 창이 열 때 호출).

### 2-3. main_window.py

- 툴바: `self.channel_action = toolbar.addAction("채널 영상 찾기", self.open_channel_finder)`
  (설정 액션 오른쪽). busy와 무관하게 항상 활성.
- `open_channel_finder()`: `settings.api_key`가 없으면 경고 후 `open_settings()`.
  `self._channel_dialog`가 없거나 API 키가 바뀌었으면 `ChannelDialog(YouTubeClient(key), parent=self)`
  생성 후 `urls_selected.connect(self._on_channel_urls)`. `set_default_ref(첫 URL)` →
  `show()`, `raise_()`, `activateWindow()`.
- `_on_channel_urls(urls)`: `n = self.url_panel.add_urls(urls)` → 상태줄
  `"URL {n}개 추가됨 — 댓글 분석을 누르세요"` (n == 0이면 "이미 목록에 있는 영상입니다").
  URL 칸 `textChanged`가 기존 `_on_urls_changed`를 타므로 자동저장은 그대로.
- `closeEvent`: 채널 창이 있으면 `close()`.

## 3. 오류 처리

| 상황 | 동작 |
|---|---|
| 입력이 채널/영상 주소가 아님 (`/c/`, `/user/` 포함) | 요청 없이 `status` 안내 |
| 채널 없음 (`ChannelNotFound`) / 영상 없음 | 경고 "채널을 찾을 수 없습니다: …" |
| API 키 오류 / 할당량 초과 | 기존 분석과 같은 문구 |
| 댓글 막힌 영상 | 댓글 표 비우고 `status` "{short_name}: 댓글이 없거나 막힌 영상입니다" (타임스탬프 수 0) |
| 네트워크 오류 | 메시지 그대로 표시, 목록은 유지 |
| 창 닫는 중 워커 진행 | 취소 신호 + 시그널 차단 (메인 창 패턴) |

## 4. 테스트

- `youtube_api`: `parse_channel_ref` 파라미터 표(핸들 URL·맨 핸들·채널 URL·맨 UC id·영상 URL·
  `/c/`·쓰레기); `resolve_channel` 세 경로와 `ChannelNotFound`(`responses`); `fetch_channel_videos`
  2페이지 페이징·`limit` 절단·`nextPageToken` 반환·댓글 0 제외·`url`/`index`/`channel_title`;
  기존 `fetch_video_infos` 테스트 불변.
- `timestamps`: `comment_has_timestamp`(줄 중간·한글 표기·영상 길이 초과는 False),
  `first_timestamp`.
- `channel`: FakeClient로 `find_channel_videos`(채널 재사용·페이지 토큰 전달·cancel) 와
  `load_comments` 정렬(타임스탬프 먼저, 좋아요 순).
- `url_panel.add_urls`: 빈 칸/기존 줄 유지/id 중복 제거/반환 개수/`textChanged` 1회.
- `channel_dialog`(가짜 클라이언트 + `qtbot`): 찾기 → 표 채움·status·더 보기 활성; 더 보기 →
  덧붙임; 행 선택 → 댓글 표(타임스탬프 행 굵게·시각 열)·타임스탬프 수 반영·캐시로 재요청 없음;
  체크 → `add_btn` 활성 → `urls_selected` 인자; 잘못된 입력 → 요청 없이 안내; 오류 → status.
- `main_window`: 액션 존재·API 키 없으면 경고; `open_channel_finder` → 창 생성·기본값 입력;
  `urls_selected` → URL 칸에 추가·중복 제외·상태줄 문구.

## 5. 범위 밖

- `/c/커스텀`, `/user/` 주소 해석(검색 API 100유닛) — 안내만.
- 댓글 답글 트리 표시, 댓글 검색/필터, 채널·목록 저장, 목록 생성 시 전 영상 댓글 선로드.
