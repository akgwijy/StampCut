# StampCut 설계 문서

- 날짜: 2026-08-25
- 상태: 승인됨 (브레인스토밍 완료)
- 한 줄 요약: **유튜브 댓글에 적힌 타임스탬프("7:05 기훈 선방")를 모아, 릴스/쇼츠용 9:16 하이라이트 영상을 자동 편집하는 Windows 데스크톱 앱.** 관객(댓글)이 지목한 순간 = 하이라이트.

---

## 1. 배경과 목표

### 1.1 대상

- 채널: 문성FC (`@msfc2022`, 구독자 75명, 영상 244개). 아마추어 축구 클럽.
- 영상: 경기 하루당 "1게임~4게임" 4편 업로드, 각 20~30분, 일반 업로드(라이브 아님), 임베드 허용.
- 댓글: 영상당 0~4개이며, 있으면 **전부 타임스탬프 댓글**. 타임스탬프 위치는 앞("7:05 기훈 선방")과 뒤("원더골 12:38") 둘 다 나타난다. 좋아요는 사실상 0.
- 원본 파일은 폰에만 있어 로컬 원본 사용은 불가. 전체 영상 다운로드는 시간이 오래 걸려 **원하지 않음**.

### 1.2 사용자 흐름

1. 유튜브 URL 여러 개(보통 하루치 4경기)와 타이틀을 입력 → **댓글 분석**
2. 댓글에서 타임스탬프를 추출해 후보 클립 표를 채움. 동시에 각 후보 구간(저화질)을 미리 받아 앱 안에서 재생.
3. 표에서 클립을 켜고 끄고, 자막을 고치고, 앞/뒤 초와 정방형 크롭(줌·위치)을 조절. 미리보기는 최종 결과와 동일하게 보임.
4. **하이라이트 만들기** → 확정 구간만 고화질로 받아 렌더링 → `출력폴더\{타이틀}.mp4` 하나.

### 1.3 검증된 전제 (2026-08-25 확인)

| 항목 | 결과 |
|---|---|
| 샘플 영상 `i3_SYn3e_kY` | 1545초, `isLiveContent=false`, `playableInEmbed=true` |
| 최근 10편 댓글 | 7편은 0개, 3편은 1~4개. 타임스탬프 댓글 비율 100% |
| 댓글 형식 예 | `7:05 기훈 선방`, `원더골 12:38`, `14:05 오프사이드 기가막히게 거네...`, `10:42 종범 골` |
| 개발 PC | Windows 11. Python은 스토어 스텁만 있음(설치 필요). node 24, git, winget 있음. ffmpeg 없음 |

---

## 2. 요구사항 (확정)

| # | 요구사항 | 결정 |
|---|---|---|
| R1 | 입력 | 유튜브 URL 여러 개 (한 줄에 하나) + 타이틀 |
| R2 | 댓글 수집 | YouTube Data API v3 (API 키, 설정에 저장). 답글 포함 |
| R3 | 하이라이트 선정 | 근접 타임스탬프 묶기 → 점수 → 총 길이 **최대 3분(기본, 변경 가능)** 까지 채움 |
| R4 | 클립 길이 | 고정: 기본 **앞 3초 / 뒤 15초**. 클립별 개별 조절 가능, 안 건드리면 기본값 |
| R5 | 순서 | URL 입력 순 → 각 영상 내 시각 순 |
| R6 | 검토 단계 | 후보 표(체크박스) + 앱 내 미리보기. 확인 없이 기본 세팅으로 바로 진행도 가능 |
| R7 | 설정 저장 | 기본값(앞/뒤 초, 최대 길이 등)·API 키·출력 폴더 등을 저장해 반복 사용 |
| R8 | 영상 소스 | 전체 다운로드 없음. 미리보기는 후보 구간만 360p, 최종은 확정 구간만 ≤1080p |
| R9 | 출력 | **9:16 (1080×1920)** mp4 한 파일. 검정 배경, 위 타이틀 띠 420px / 정방형 영상 1080px / 아래 자막 띠 420px |
| R10 | 자막 | 하단 띠에 댓글 텍스트. 작성자 표시 없음. 타이틀은 "날짜 채널명 하이라이트" (예: `26.08.20 문성FC 하이라이트`) |
| R11 | 크롭 | 클립별 정방형 크롭: 줌(확대/축소), 좌우·상하 위치. 클립 안에서는 고정 |
| R12 | GUI | 단일 창 (좌: 후보 표, 우: 미리보기). Python + PySide6 |

---

## 3. 화면

### 3.1 메인 창 (단일 창)

```
┌─ StampCut ───────────────────────────────────────────────── [⚙ 설정] ─┐
│ 유튜브 URL (한 줄에 하나)                 타이틀 (상단 띠)               │
│ ┌─────────────────────────────────┐    ┌──────────────────────────┐    │
│ │ https://www.youtube.com/watch…  │    │ 26.08.20 문성FC 하이라이트 │    │
│ │ https://www.youtube.com/watch…  │    └──────────────────────────┘    │
│ │ https://www.youtube.com/watch…  │                     [ 댓글 분석 ]  │
│ └─────────────────────────────────┘                                    │
├─────────────────────────────────────────┬──────────────────────────────┤
│ ✓ │ 영상  │ 시간  │ 자막         │앞│뒤│상태│  미리보기 (최종 결과 그대로) │
│ ☑ │ 1게임 │ 11:08 │ 기훈 선방    │3│15│준비│   ┌──────────┐              │
│ ☑ │ 3게임 │ 10:42 │ 종범 골      │3│15│준비│   │ 타이틀    │  ▶ ⏸ 0:07/0:18│
│ ☑ │ 3게임 │ 12:38 │ 원더골       │5│20│준비│   │┌────────┐│  줌 [──●───] │
│ ☐ │ 3게임 │ 17:56 │ 역습         │3│15│제외│   ││ 영상   ││  앞 [3] 뒤 [15]│
│ ☑ │ 4게임 │  7:05 │ 기훈 선방    │3│15│받는중│  ││(드래그)││  자막 [원더골]│
│                                         │   │└────────┘│  [기본값으로] │
│                                         │   │ 12:38 원더골│             │
├─────────────────────────────────────────┴──────────────────────────────┤
│ 클립 6개 · 총 1:53 / 3:00 · 출력: E:\highlights\…mp4   [하이라이트 만들기]│
│ ▓▓▓▓▓▓▓░░░░░░░░ 미리보기용 구간 받는 중 3 / 6                             │
└────────────────────────────────────────────────────────────────────────┘
```

동작:

- **URL 입력창**: `QPlainTextEdit`. 인식 형식은 `watch?v=`, `youtu.be/`, `shorts/`, `live/`, `embed/`, 11자 ID 단독. 파싱 실패한 줄은 빨간 배경으로 표시하고 분석을 시작하지 않는다.
- **타이틀**: 분석 후 비어 있으면 템플릿으로 자동 채움. 언제든 수정 가능.
- **표** (`QTableView`): 체크박스(enabled), 영상(입력 순번과 짧은 이름 — 제목에서 "N게임" 추출, 없으면 순번), 시간(`m:ss`), 자막(더블클릭 편집), 앞/뒤(스핀박스, 0~120초 정수. `pre/post`가 None이면 기본값을 회색으로 표시하고, 사용자가 값을 바꾸면 그 클립의 명시값으로 저장해 검은색으로 표시), 상태(대기 / 받는 중 / 준비됨 / 오류 / 길이 초과 / 제외). 행 선택 시 오른쪽 미리보기가 그 클립으로 바뀐다.
- **미리보기**: 9:16 비율 위젯(창 높이에 맞춰 축소 표시). 타이틀 띠·정방형 영상·자막 띠를 실제 레이아웃 그대로 그린다. 정방형 안의 영상을 **드래그하면 드래그 방향으로 영상이 따라 움직이고**(pan 갱신), 줌 슬라이더(0.5×~3.0×, 0.05 단위)로 확대/축소한다. 클립 구간(`t−pre ~ t+post`)만 반복 재생, 앞/뒤 초를 바꾸면 즉시 반영. **[기본값으로]**는 선택 클립의 앞/뒤/줌/위치를 초기화.
- **상태줄**: 켜진 클립 수, 총 길이(최대 길이 초과 시 빨간색, 렌더링은 허용), 출력 경로(클릭해 변경), **[하이라이트 만들기]**, 진행률 바 + 단계 텍스트. 완료 시 "폴더 열기 / 재생" 버튼.
- 표를 건드리지 않고 바로 **[하이라이트 만들기]**를 눌러도 된다(기본 세팅 진행). 미리보기 다운로드가 끝나지 않았어도 렌더는 가능(최종 구간은 따로 받으므로).

### 3.2 설정 창

API 키 · 앞/뒤 기본값 · 최대 총 길이 · 묶음 간격 · 출력 폴더 · 타이틀 템플릿 · 배경색 · 자막 폰트 · 자막에 시간 표시 · ffmpeg 경로 · 병렬 다운로드 수 · yt-dlp 버전 표시 + **[yt-dlp 업데이트]** · [캐시 비우기] · [로그 폴더 열기].

### 3.3 출력 캔버스 레이아웃 (1080×1920)

```
y=0    ┌──────────────────────┐
       │  타이틀 (64px, 흰색)  │   상단 띠 0~420, 세로 중앙 정렬
y=420  ├──────────────────────┤
       │                      │
       │   정방형 영상 1080²   │   420~1500
       │                      │
y=1500 ├──────────────────────┤
       │  12:38 (36px, 노랑)   │   y=1510
       │  원더골 (60px, 흰색)  │   y=1560~, 최대 2줄, 검정 테두리 4px
y=1680 │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │   이 아래(240px)는 릴스/쇼츠 앱 UI가 가리므로 비워 둠
y=1920 └──────────────────────┘
```

텍스트 폭 추정: 한글·CJK 1자 = `font_size`, 그 외 문자 = `0.55 × font_size`. 가용 폭 960px(좌우 여백 60px). 넘치면 줄바꿈, 최대 2줄, 그래도 넘치면 말줄임(…) 후 표에서 수정 유도. 상수는 `renderer.LAYOUT` 한 곳에 둔다.

---

## 4. 기술 스택

| 역할 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.12 | `winget install Python.Python.3.12` |
| GUI | PySide6 (QtWidgets, QtMultimedia) | `QGraphicsVideoItem`으로 정방형 클리핑·드래그·줌. QtWebEngine 불필요 |
| 댓글 | YouTube Data API v3, `requests` 직접 호출 | 가볍고 모킹 쉬움 |
| 다운로드 | `yt-dlp` 라이브러리 임포트, `download_ranges` + `force_keyframes_at_cuts` | 구간만 정확히 |
| 편집 | `ffmpeg` subprocess | 크롭·자막·합치기 |
| 폰트 | Pretendard Bold 동봉 (OFL), 설정에서 변경 가능 | 한글 보장 |
| 설정 | JSON `%APPDATA%\StampCut\settings.json` | 단순 |
| 캐시 | `%LOCALAPPDATA%\StampCut\cache\{video_id}\` | 재사용 |
| 렌더 임시 | `%LOCALAPPDATA%\StampCut\render\{job_id}\` | 성공 시 삭제 |
| 로그 | `%LOCALAPPDATA%\StampCut\logs\stampcut.log` (1MB × 3 회전) | |
| 테스트 | pytest, pytest-qt, responses | |

ffmpeg 탐색 순서: ① 앱 폴더 `bin\ffmpeg.exe` → ② 설정의 경로 → ③ PATH. 없으면 렌더 버튼 비활성 + 안내(`winget install Gyan.FFmpeg`). ffprobe는 ffmpeg 옆에서 찾는다.

---

## 5. 모듈 구조

`core`는 Qt를 전혀 모른다. GUI는 core의 작업을 스레드로 감싸 시그널로 전달만 한다.

```
stampcut/
├─ __main__.py              # python -m stampcut
├─ app.py                   # QApplication, 로깅 초기화, MainWindow
├─ core/
│  ├─ models.py             # dataclass: Settings, VideoInfo, Mention, Clip, Project, ClipStatus
│  ├─ youtube_api.py        # parse_video_id(), fetch_videos(), fetch_all_comments()
│  ├─ timestamps.py         # extract_mentions(video, comment) -> list[Mention]
│  ├─ highlights.py         # build_clips(mentions, videos, settings) -> list[Clip]
│  ├─ downloader.py         # download_preview(clip), download_final(clip)
│  ├─ renderer.py           # LAYOUT, compute_square_geometry(), build_clip_command(), concat()
│  ├─ ffmpeg.py             # find_ffmpeg(), run(cmd, on_progress, cancel), probe()
│  ├─ settings.py           # load()/save(), 기본값, 경로 상수
│  ├─ textwrap_kr.py        # 폭 추정·줄바꿈
│  └─ pipeline.py           # analyze(), fetch_previews(), render() — progress 콜백 + cancel Event
├─ gui/
│  ├─ main_window.py
│  ├─ url_panel.py          # URL 입력 + 타이틀 + 분석 버튼
│  ├─ clip_table.py         # ClipTableModel(QAbstractTableModel) + 델리게이트
│  ├─ preview_widget.py     # 9:16 미리보기 (QGraphicsView + QGraphicsVideoItem + 텍스트)
│  ├─ settings_dialog.py
│  ├─ status_bar.py
│  └─ workers.py            # QRunnable 래퍼 + Signals(progress, finished, failed)
├─ assets/fonts/Pretendard-Bold.otf
├─ tests/
├─ pyproject.toml
├─ README.md                # 설치, API 키 발급 절차, 사용법
└─ .gitignore
```

각 모듈의 책임과 의존:

| 모듈 | 하는 일 | 의존 |
|---|---|---|
| `youtube_api` | URL→ID, 영상 정보(제목·채널·게시일·길이·댓글 수), 댓글 스레드+답글 전체 페이지 수집 | requests |
| `timestamps` | 댓글 원문(`textOriginal`)에서 (초, 자막) 추출 | 없음 |
| `highlights` | 묶기·점수·병합·선정·정렬 | models |
| `downloader` | yt-dlp로 구간 다운로드, 캐시 경로 관리 | yt_dlp, ffmpeg 경로 |
| `renderer` | 기하 계산, 필터그래프/명령 생성, 합치기 | ffmpeg, textwrap_kr |
| `pipeline` | 위 모듈을 순서대로 호출하는 3개 작업 | 전부 |
| `gui.*` | 표시·입력·스레드 | pipeline, models |

---

## 6. 데이터 모델 (`core/models.py`)

```python
@dataclass
class Settings:
    api_key: str = ""
    pre_seconds: int = 3
    post_seconds: int = 15
    max_total_seconds: int = 180
    cluster_window_seconds: float = 5.0
    output_dir: str = "~/Videos/StampCut"
    title_template: str = "{date} {channel} 하이라이트"   # date=YY.MM.DD (KST), channel=채널명
    background_color: str = "#000000"
    font_path: str = ""                 # 빈 값 = 동봉 폰트
    show_time_in_caption: bool = True
    ffmpeg_path: str = ""               # 빈 값 = 자동 탐색
    parallel_downloads: int = 2
    preview_margin_pre: int = 30
    preview_margin_post: int = 60

@dataclass
class VideoInfo:
    index: int              # URL 입력 순번 (0부터)
    video_id: str
    url: str
    title: str
    short_name: str         # "3게임" 등, 제목에서 추출. 없으면 "영상 N"
    channel_title: str
    published_at: datetime  # UTC
    duration: int           # 초
    comment_count: int

@dataclass
class Mention:
    video_id: str
    seconds: int
    caption: str            # 같은 줄에서 타임스탬프를 뺀 텍스트 (비어 있을 수 있음)
    comment_id: str
    author: str
    like_count: int
    is_reply: bool

class ClipStatus(Enum): PENDING, DOWNLOADING, READY, ERROR, OVER_LIMIT, DISABLED

@dataclass
class Clip:
    id: str                             # uuid4
    video: VideoInfo
    t: int                              # 대표 시각(초) = 묶음 내 가장 이른 언급
    mentions: list[Mention]
    score: float
    caption: str                        # 편집 가능
    pre: int | None = None              # None = Settings 기본값
    post: int | None = None
    enabled: bool = True
    zoom: float = 1.0                   # 0.5 ~ 3.0
    pan_x: float = 0.5                  # 0 ~ 1 (0.5 = 중앙)
    pan_y: float = 0.5
    status: ClipStatus = PENDING
    error: str = ""
    preview_path: Path | None = None
    preview_start: int | None = None    # 미리보기 파일이 시작하는 원본 절대 시각
    final_path: Path | None = None

    def start(self, s: Settings) -> int: max(0, t - (pre if pre is not None else s.pre_seconds))
    def end(self, s: Settings) -> int:   min(video.duration, t + (post if post is not None else s.post_seconds))
    def duration(self, s) -> int:        end - start        # 영상 경계에서 잘리면 짧아짐

@dataclass
class Project:
    urls: list[str]
    title: str
    videos: list[VideoInfo]
    clips: list[Clip]
```

---

## 7. 알고리즘

### 7.1 댓글 수집 (`youtube_api`)

- `videos.list?part=snippet,contentDetails,statistics&id=<최대 50개 콤마>` → 제목, 채널명, `publishedAt`, `PT25M45S` 길이 파싱, 댓글 수.
- `commentThreads.list?part=snippet,replies&videoId=&maxResults=100&textFormat=plainText&order=time` 을 `nextPageToken`이 없을 때까지 반복.
- 스레드의 `totalReplyCount`가 포함된 답글 수보다 크면 `comments.list?part=snippet&parentId=&maxResults=100` 으로 나머지 답글 수집.
- 오류 매핑: 400/403 `keyInvalid`·`API key not valid` → `ApiKeyError`, 403 `quotaExceeded` → `QuotaError`, 403 `commentsDisabled` → 해당 영상 댓글 0개로 처리, 404/빈 items → `VideoNotFound`.

### 7.2 타임스탬프 추출 (`timestamps.extract_mentions`)

- 입력: `VideoInfo`, 댓글 하나(원문 `textOriginal`, id, author, likes, is_reply).
- 원문을 줄로 나눈다. 각 줄에서 아래 패턴을 모두 찾는다.
  - 콜론형: `(?<!\d)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?!\d)` → `h:mm:ss` 또는 `m:ss`
  - 한글형: `(?:(\d{1,2})\s*시간)?\s*(?:(\d{1,3})\s*분)(?:\s*(\d{1,2})\s*초)?` 및 `(\d{1,2})\s*시간(?:\s*(\d{1,2})\s*분)?`(분 없이 시간만)
- 검증: 초 < 60, (시간이 있으면) 분 < 60, **총 초 ≤ 영상 길이** — 아니면 버림.
- 자막 = 해당 줄에서 매치된 타임스탬프 문자열을 모두 제거한 뒤 앞뒤의 공백과 `-:,~·|()[]` 를 정리한 것. 한 줄에 타임스탬프가 여러 개면 각각 Mention을 만들고 자막은 공유.
- 같은 댓글 안에서 같은 초가 중복되면 하나만.

### 7.3 후보 클립 만들기 (`highlights.build_clips`)

```
1. 영상별로 Mention을 seconds 순 정렬.
   직전 언급과의 간격이 cluster_window(5초) 이하이면 같은 묶음 (single-linkage).
2. 묶음 → Clip: t = min(seconds), mentions = 묶음 전체
   score = 서로 다른 author 수 + 0.1 × ln(1 + like 합)
   caption = like 최다 언급의 caption, 동점이면 가장 긴 것 (빈 문자열은 후순위)
3. 같은 영상에서 기본 pre/post로 계산한 구간이 겹치는 인접 Clip은 병합:
   t = 이른 쪽 t, post = (늦은 쪽 t − 이른 쪽 t) + 기본 post 를 명시값으로 저장,
   mentions 합집합, score 재계산, caption = 각 caption을 " · "로 연결 (빈 것 제외).
   겹침이 없어질 때까지 반복.
4. 선정: score↓, like 합↓, (video.index↑, t↑) 순으로 훑으며
   누적 duration + clip.duration ≤ max_total_seconds 이면 enabled=True,
   아니면 enabled=False, status=OVER_LIMIT (건너뛰고 다음 후보 계속 검사).
5. 반환 순서: video.index↑, t↑.
```

### 7.4 타이틀 기본값

`title_template.format(date=첫 URL 영상의 publishedAt을 KST로 바꾼 YY.MM.DD, channel=channel_title)`. 사용자가 타이틀 칸을 비워 뒀을 때만 채운다.

---

## 8. 파이프라인

### 8.1 작업 구조 (`core/pipeline.py`)

세 작업 모두 `progress(stage: str, done: int, total: int, message: str)` 콜백과 `cancel: threading.Event`를 받는다. GUI의 `workers.py`가 이를 `QRunnable`로 감싸 시그널로 바꾼다.

| 작업 | 트리거 | 단계 |
|---|---|---|
| `analyze(urls, settings)` | [댓글 분석] | URL 파싱 → 영상 정보 → 댓글 수집(영상별) → 추출 → `build_clips` → `Project` 반환 |
| `fetch_previews(project, settings)` | analyze 완료 직후 자동 | 클립별 `download_preview`, `parallel_downloads`개 동시. 클립별로 status 갱신 콜백 |
| `render(project, settings, output_path)` | [하이라이트 만들기] | enabled 클립별 `download_final` → `build_clip_command` 실행 → `concat` → 임시 삭제 |

### 8.2 다운로드 (`core/downloader.py`)

yt-dlp를 라이브러리로 사용(`yt_dlp.YoutubeDL`). 공통 옵션: `ffmpeg_location`, `quiet`, `noprogress`, `progress_hooks`, `merge_output_format="mp4"`, `force_keyframes_at_cuts=True`, `download_ranges=download_range_func(None, [(start, end)])`.

| | 미리보기 | 최종 |
|---|---|---|
| 범위 | `[max(0, t−30), min(duration, t+60)]` | `[clip.start, clip.end]` |
| format | `bv*[height<=360]+ba/b[height<=360]` | `bv*[height<=1080]+ba/b[height<=1080]` |
| 파일 | `cache\{id}\preview_{t}.mp4` (+ `preview_start` 기록) | `cache\{id}\final_{start}_{end}.mp4` |

- 캐시 파일이 이미 있고 크기 > 0이면 다시 받지 않는다.
- 미리보기 범위가 현재 `pre/post`를 덮지 못하면(사용자가 여유보다 크게 늘림) 그 클립만 넓은 범위로 다시 받는다.
- 진행률 훅의 `downloaded_bytes/total_bytes`를 클립 단위 %로 전달.

### 8.3 정방형 기하 (`renderer.compute_square_geometry`)

미리보기 위젯과 ffmpeg 명령이 **같은 함수**를 쓴다.

```
입력: W, H (원본), S=1080, zoom z, pan_x, pan_y
1. sh = round_even(S × z), sw = round_even(W × sh / H)
2. padW = max(sw, S), padH = max(sh, S)
   pad_x = round((padW − sw) × pan_x), pad_y = round((padH − sh) × pan_y)   # 줌 아웃 시 검정 여백 위치
3. crop_x = round((padW − S) × pan_x), crop_y = round((padH − S) × pan_y)
출력: SquareGeometry(sw, sh, padW, padH, pad_x, pad_y, crop_x, crop_y)
예: 1920×1080, z=1, pan=(0.5, 0.5) → sw=1920, sh=1080, pad 없음, crop=(420, 0)
```

### 8.4 클립 렌더 (`renderer.build_clip_command`)

입력 파일: `final_path`(구간 파일, 0초부터 `clip.duration`초). 출력: `render\{job}\clip_{n:03}.mp4`.

```
-i final.mp4 [-f lavfi -i anullsrc=r=48000:cl=stereo   ← 원본에 오디오 없을 때만]
-filter_complex
  [0:v] scale=sw:sh, pad=padW:padH:pad_x:pad_y:black, crop=1080:1080:crop_x:crop_y [sq];
  color=c=<background>:s=1080x1920:r=30:d=<dur> [bg];
  [bg][sq] overlay=0:420 [c0];
  [c0] drawtext=textfile=title.txt:fontfile=…:fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(420-text_h)/2 [c1];
  [c1] drawtext=textfile=time.txt:fontsize=36:fontcolor=#FFD60A:x=(w-text_w)/2:y=1510 [c2];     ← show_time_in_caption일 때만
  [c2] drawtext=textfile=caption.txt:fontsize=60:fontcolor=white:borderw=4:bordercolor=black:line_spacing=8:x=(w-text_w)/2:y=1560 [v];
  [0:a|1:a] afade=t=in:d=0.2, afade=t=out:st=<dur-0.2>:d=0.2 [a]
-map [v] -map [a] -t <dur>
-c:v libx264 -preset medium -crf 18 -r 30 -pix_fmt yuv420p
-c:a aac -b:a 192k -ar 48000 -ac 2 -movflags +faststart
```

- 모든 텍스트는 UTF-8 임시 파일(`textfile=`)로 넘긴다. 경로 구분자·콜론은 ffmpeg 규칙대로 이스케이프.
- 줄바꿈은 `textwrap_kr`이 미리 넣는다(§3.3 규칙).
- 원본 W×H는 `ffprobe`로 읽는다(회전 메타데이터 반영).

### 8.5 합치기와 출력

- `concat.txt`에 중간 파일 나열 → `ffmpeg -f concat -safe 0 -i concat.txt -c copy -movflags +faststart 출력`.
- 출력 파일명: 타이틀에서 `\ / : * ? " < > |` 를 `_`로 치환 + `.mp4`. 이미 있으면 ` (2)`, ` (3)` …
- 성공하면 `render\{job}\` 삭제. 실패하면 보존(디버깅용) 후 다음 성공 시 정리.

### 8.6 진행률

ffmpeg를 `-progress pipe:1 -nostats`로 실행해 `out_time_ms`를 읽어 클립 %를 계산. 전체 진행률 = (최종 다운로드 40% + 클립 렌더 50% + 합치기 10%)의 가중 합. 취소 시 실행 중인 프로세스를 종료하고 임시 파일 정리.

---

## 9. 설정과 파일 위치

- `settings.json`은 `Settings` 필드를 그대로 저장. 없는 키는 기본값, 알 수 없는 키는 무시. API 키는 평문(개인 PC용).
- 첫 실행 시 API 키가 없으면 설정 창을 먼저 연다.
- [yt-dlp 업데이트]는 `[sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]`를 실행해 출력을 보여주고, 완료 후 재시작을 안내한다.
- [캐시 비우기]는 `cache\` 전체 삭제(진행 중 작업이 없을 때만).

---

## 10. 에러 처리

원칙: 모든 긴 작업은 백그라운드. 실패는 **해당 영상/클립에만** 표시하고 나머지는 계속.

| 상황 | 동작 |
|---|---|
| API 키 없음/무효 | 설정 창 열고 안내. README에 발급 절차 |
| 할당량 초과 | "내일 오후 4시(태평양 자정)에 초기화됩니다" 안내 |
| URL 형식 오류 | 해당 줄 빨간 표시, 분석 시작 안 함 |
| 비공개·삭제·댓글 비활성 영상 | 그 영상만 오류/0개로 표시, 나머지 진행 |
| 타임스탬프 댓글 전무 | 표 비우고 안내 |
| 미리보기 다운로드 실패 | 클립 status=ERROR, 사유 툴팁, 행 우클릭 [재시도]. 렌더는 가능 |
| 최종 다운로드 실패 | "실패한 클립 빼고 계속 / 취소" 선택 |
| yt-dlp가 유튜브 변경으로 실패 | 위 오류 메시지에 "설정 → yt-dlp 업데이트" 안내 |
| ffmpeg 없음 | 렌더 버튼 비활성, 설정 창에서 경로 지정 |
| 클립 렌더 실패 | 중단. stderr 마지막 30줄을 펼침 창으로. 임시 파일 보존 |
| 출력 폴더 쓰기 불가 / 디스크 부족(예상 크기의 2배 미만) | 렌더 전에 확인해 안내 |
| 작업 중 창 닫기 | 확인 대화상자 → cancel Event → 스레드 종료 대기(최대 5초) |

모든 예외는 로그 파일에 스택과 함께 기록.

---

## 11. 테스트

`core`는 네트워크·Qt 없이 테스트한다.

| 대상 | 케이스 |
|---|---|
| `timestamps` | `7:05 기훈 선방`→(425,"기훈 선방"); `원더골 12:38`→(758,"원더골"); `1:02:33 골`; `12분 38초`; `1시간 2분`; `12분`; 길이 초과 `59:59`(1545초 영상)→없음; 한 줄 두 개 `10:42, 10:50 골`→2개 동일 자막; 여러 줄; 기호 정리 `- 12:38 : 원더골 -`; 빈 자막; 초 ≥60 거부 |
| `highlights` | 5초 이내 묶임/6초 분리; t=최초; 점수(작성자 수, 좋아요); 자막 선택 규칙; 겹침 병합(두 번, 세 번 연쇄); 3분 채우기와 OVER_LIMIT; 영상 경계 클램프 반영; 최종 정렬 |
| `renderer` | 기하: z=1 중앙, z=2 pan=(0,1), z=0.5 패딩, 세로 원본(1080×1920); 필터그래프 문자열 스냅샷; 오디오 없음 분기; 파일명 치환·중복 |
| `textwrap_kr` | 폭 추정, 줄바꿈, 2줄 초과 말줄임 |
| `youtube_api` | `responses`로 페이지네이션 2페이지, 답글 추가 수집, 오류 코드별 예외, URL 형식 6종 |
| `settings` | 기본값, 저장/복원, 누락·미지 키 |
| `pipeline` | downloader/ffmpeg를 가짜로 바꿔 analyze→previews→render 흐름과 progress 호출·cancel 확인 |
| GUI (`pytest-qt`) | 창 생성; 가짜 Project로 표 채움; 체크 토글→총 길이 갱신; 미리보기 드래그→pan 변화; 스핀박스→duration 갱신 |
| 통합 (`-m network`, 수동) | 실제 채널 영상 1개: 미리보기 구간 다운로드 + 클립 1개 렌더 → ffprobe로 1080×1920, 길이 ±0.5초 확인 |

---

## 12. 설치 · 실행 · 배포

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m stampcut
pytest
```

`pyproject.toml`: 의존성 `PySide6`, `yt-dlp`, `requests`; dev `pytest`, `pytest-qt`, `responses`. README에 Google Cloud Console에서 YouTube Data API v3 사용 설정 → 사용자 인증 정보 → API 키 발급 절차를 스크린샷 없이 단계별로 적는다.

배포(선택, 마지막): PyInstaller 원폴더 빌드 `build.ps1` — `assets/fonts` 포함, `bin\ffmpeg.exe` 동봉. 본인만 쓰는 동안은 소스 실행으로 충분.

`.gitignore`: `.venv/`, `__pycache__/`, `.superpowers/`, `build/`, `dist/`, `*.spec`, `.pytest_cache/`.

---

## 13. 범위 밖

공을 따라가는 움직이는 크롭 · 16:9 출력 · 경기별 타이틀 카드 · 비공개 영상(OAuth) · 클립 간 전환 효과 · 프로젝트 저장/불러오기 · 비공식 댓글 스크래핑 폴백 · 다국어 UI.
