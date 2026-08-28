# 배경 음악(BGM) 1차 + 전체 미리보기 설계

날짜: 2026-08-28
상태: 승인됨

## 목적

1. 출력 영상에 배경 음악을 깔 수 있게 한다 — 폴더에서 음원 선택, 원본/BGM 볼륨,
   음원 시작점, 영상 내 구간 조절.
2. 최종 결과와 같은 화면의 **전체 미리보기**를 앱 안에서 재생하고, 그 위에 BGM을
   실시간으로 얹어 듣고 조절한 뒤, 확인이 끝나면 마지막에 믹스해 출력한다.
3. BGM만 따로 들어볼 수 있게 한다.

## 결정 사항 (사용자 확정)

- 진행 순서: **BGM 1차(이 문서) → 댓글 있는 영상 목록 → BGM 2차(내장 무료 음원)**. 각각 별도 스펙.
- "위치 조절" = **음원 시작점 + 영상 내 시작/끝 구간** 둘 다.
- 믹스 = **원본 볼륨과 BGM 볼륨을 각각 조절** (원본 0%면 BGM만 남음).
- 음원 선택 = **BGM 폴더 지정 + 드롭다운** 과 **찾아보기(파일)** 둘 다.
- 믹스 시점 = concat 뒤 **마지막 단계에서 한 번** (A안). 클립별 렌더·concat은 그대로.
- 전체 미리보기 = **저해상도(540x960) 미리보기 렌더 파일**을 버튼으로 생성해 재생 (P1).
  BGM은 파일에 굽지 않고 별도 플레이어로 동기 재생 → 볼륨·위치 변경 시 재렌더 없음.
- 미리보기 재생성은 **버튼으로만** (자동 재생성 없음). 클립이 바뀌면 "다시 만들기" 표시.

## 1. 데이터 모델·저장

### 1-1. models.py

```python
@dataclass
class AudioMix:
    original_volume: float = 1.0    # 원본 소리 0.0~1.0
    bgm_path: str = ""              # 절대 경로. "" = BGM 없음
    bgm_volume: float = 0.3         # 0.0~1.0
    bgm_offset: float = 0.0         # 음원 시작점(초)
    bgm_start: float = 0.0          # 영상 내 시작(초)
    bgm_end: float | None = None    # 영상 내 끝(초). None = 영상 끝까지

    def has_bgm(self) -> bool:
        return bool(self.bgm_path)

    def is_default(self) -> bool:
        """믹스 단계를 건너뛰어도 되는 상태 (BGM 없고 원본 100%)."""
        return not self.has_bgm() and self.original_volume == 1.0
```

- `Project.audio: AudioMix = field(default_factory=AudioMix)` — 항상 존재, `None` 없음.
- `Settings.bgm_dir: str = ""` — 마지막으로 고른 BGM 폴더 (드롭다운 나열용).
- 볼륨은 선형 진폭 배율. 슬라이더 30 → `0.3` → ffmpeg `volume=0.3`, Qt `QAudioOutput.setVolume(0.3)`.

### 1-2. project_io.py

- `VERSION = 2`. 저장 시 `"audio": asdict(project.audio)` 추가.
- **로드는 v1과 v2 모두 허용**: v1이면 `audio = AudioMix()`. v2에서 `audio`가 없거나 dict가
  아니거나 필드가 깨졌으면 `AudioMix()`로 대체하고 프로젝트 나머지는 정상 복원
  (프로젝트 전체를 `None`으로 버리지 않는다).
- `bgm_path`는 파일이 없어도 그대로 복원한다 (패널이 "(파일 없음)" 표시, 렌더 시작 시 검증).
- 기타 알 수 없는 버전은 기존대로 `None`.

## 2. core

### 2-1. 렌더 프로필 (renderer.py)

```python
@dataclass(frozen=True)
class RenderProfile:
    scale: float = 1.0      # 캔버스·정방형·폰트·Y좌표·간격 배율
    preset: str = "medium"
    crf: int = 18

FINAL_PROFILE = RenderProfile()
PREVIEW_PROFILE = RenderProfile(scale=0.5, preset="ultrafast", crf=28)   # 540x960
```

- `build_clip_command(paths, clip, settings, title, probe, workdir, index, font_path,
  profile=FINAL_PROFILE, source=None, in_offset=0.0)`.
  - `source`: 입력 파일. `None`이면 `clip.final_path` (기존 동작).
  - `in_offset`: 초. `> 0`이면 `-ss {in_offset}`를 `-i` **앞**에 둔다 (입력 시킹).
    미리보기 구간 파일은 여유분이 있으므로 `clip.start(s) - clip.preview_start`.
  - `profile.scale`로 `canvas_w/h`, `square`, `square_y`, 폰트 3종, `title_y`, `caption_y`,
    `TIME_GAP`을 모두 곱해 짝수로 맞춘다(`_even`). `wrap()`에는 배율 적용 전
    폰트·폭을 넘겨 줄바꿈 결과가 최종과 같게 한다. `-preset`/`-crf`는 프로필 값.
  - 기본 인자로 호출하면 지금과 완전히 같은 명령을 만든다 (기존 테스트 불변).

### 2-2. 전체 미리보기 렌더 (pipeline.py)

```python
def render_preview(project, s, paths, progress=_noop, cancel=None) -> Path
```

- 대상 = `project.enabled_clips()`. 하나라도 `preview_covers(c, s)`가 아니면 다운로드 없이
  `ValueError("미리보기가 준비되지 않은 클립: 3게임 12:38, …")` (`_label` 형식).
- 작업 폴더 `settings.data_dir() / "preview" / <uuid>`. 클립마다 `ff.probe(preview_path)` →
  `build_clip_command(..., profile=PREVIEW_PROFILE, source=c.preview_path,
  in_offset=c.start(s) - c.preview_start)` → `ff.run` (진행률 stage `"preview_render"`,
  메시지 `"{label} 미리보기 렌더 중"`).
- concat(`-c copy`) → `data_dir()/preview/full_<uuid>.mp4` 로 `os.replace`. 반환값은 이 경로.
- 정리: 작업 폴더 삭제. `preview/` 안의 다른 `full_*.mp4`는 삭제 시도하되 `OSError`(재생 중
  잠김)는 무시. 취소/실패 시 작업 폴더만 지우고 결과 파일은 남기지 않는다.
- 고화질 다운로드·BGM 믹스는 하지 않는다.

```python
def preview_signature(project, s) -> str
```

- 다음을 JSON으로 직렬화해 sha1: `project.title`; 켜진 클립 순서대로
  `(video_id, t, effective_pre, effective_post, zoom, pan_x, pan_y, caption)`;
  설정 `title_y, title_color, caption_y, caption_color, background_color,
  show_time_in_caption, font_path`. 끈 클립·BGM 값은 포함하지 않는다.

### 2-3. BGM 믹스 명령 (renderer.py)

```python
BGM_FADE_IN = 1.0
BGM_FADE_OUT = 2.0

def build_mix_command(paths, video: Path, audio: AudioMix, total: float, out: Path) -> list[str]
```

- `total` = concat 결과의 길이(초, `ff.probe(...).duration`).
- 구간: `start = clamp(bgm_start, 0, total)`, `end = clamp(bgm_end or total, start, total)`,
  `section = end - start`. `section < 0.5`이면 BGM을 넣지 않는다(원본 볼륨만 적용).
- BGM 있음:
  ```
  ffmpeg -hide_banner -i {video} -stream_loop -1 -i {bgm_path}
    -filter_complex
      [0:a]volume={orig}[a0];
      [1:a]atrim=start={offset},asetpts=PTS-STARTPTS,
           atrim=duration={section},asetpts=PTS-STARTPTS,
           afade=t=in:d=1,afade=t=out:st={max(0, section-2)}:d=2,
           volume={bgm},adelay={start_ms}|{start_ms},apad[a1];
      [a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]
    -map 0:v -c:v copy -map [a] -c:a aac -b:a 192k -ar 48000 -ac 2
    -t {total} -movflags +faststart {out}
  ```
  - 반복 규칙: `-stream_loop -1` 로 곡을 무한 반복한 스트림에서 앞 `offset`초를 잘라내므로
    **첫 재생은 offset부터 곡 끝까지, 이후엔 곡 처음부터 반복**. 2-4의 계산과 동일.
  - `adelay`는 ms 정수, 스테레오라 `|`로 두 채널 모두 지정. `apad`로 a1이 a0보다 짧아도
    amix가 끝까지 간다.
- BGM 없음(원본 볼륨만 바뀜): `-filter_complex [0:a]volume={orig}[a]` 만.
- `audio.is_default()`면 호출하지 않는다(파이프라인이 생략).
- 값 형식은 `f"{x:.3f}"` 로 고정해 테스트가 문자열을 단언할 수 있게 한다.

### 2-4. BGM 위치 계산 (신규 `core/bgm_sync.py`, Qt 없음)

```python
def bgm_position(t: float, audio: AudioMix, bgm_duration: float, total: float) -> float | None
```

- `audio.has_bgm()`이 아니거나 `bgm_duration <= 0`이면 `None`.
- `end = bgm_end if bgm_end is not None else total`. `t < bgm_start` 또는 `t >= end`면 `None`.
- `e = t - bgm_start`, `D = bgm_duration`, `first = D - offset` (offset ≥ D면 `offset % D` 로 정규화).
  `e < first`이면 `offset + e`, 아니면 `(e - first) % D`.
- 미리보기(3-2)와 렌더(2-3)가 같은 규칙을 쓴다는 것이 핵심.

### 2-5. render 파이프라인 변경 (pipeline.py)

- 시작 시(다운로드 전) `project.audio.has_bgm()`이고 파일이 없으면
  `ValueError("배경 음악 파일을 찾을 수 없습니다: {path}")`.
- 기존 concat 뒤: `audio.is_default()`가 아니면 `progress("mix", 0, 1, "배경 음악 섞는 중")` →
  `total = ff.probe(paths, concat_out).duration` → `ff.run(build_mix_command(...), total_seconds=total)`
  → 결과를 `os.replace`로 `output_path`에. 기본값이면 지금처럼 concat 결과를 바로 옮긴다.
- 진행률 stage 문자열 `"mix"`는 상태 패널이 그대로 메시지로 보여준다(별도 처리 없음).

## 3. GUI

### 3-1. BGM 패널 (신규 `gui/bgm_panel.py`)

`class BgmPanel(QGroupBox)` 제목 "배경 음악". 왼쪽 열에서 "세부 설정" 그룹 바로 아래, 컷 표 위.

- 1행: `[폴더…]` 버튼 · 드롭다운 · `[찾아보기…]` 버튼.
  - 드롭다운 항목: 맨 위 "없음", 그 아래 `settings.bgm_dir`의 오디오 파일을 이름순
    (확장자 `.mp3 .wav .m4a .aac .flac .ogg .opus`, 대소문자 무시). 데이터 = 절대 경로.
  - 찾아보기로 고른 파일이 목록에 없으면 항목을 추가하고 선택한다(폴더 밖 파일 허용).
  - 복원된 `bgm_path`가 목록에 없으면 항목을 추가하고, 파일이 없으면 표시명에 " (파일 없음)".
  - 폴더 버튼: `QFileDialog.getExistingDirectory` → 목록 재구성, `bgm_dir_changed(str)`.
- 2행: "원본 볼륨" 슬라이더(0–100, 기본 100) + "%" 라벨 · "BGM 볼륨" 슬라이더(0–100, 기본 30) + 라벨.
- 3행: "음원 시작" `QDoubleSpinBox`(0–3600초, 0.5 단위, 소수 1자리) · "영상 구간"
  시작 `QDoubleSpinBox`(0–3600) · 끝 `QDoubleSpinBox`(0–3600, `specialValueText("끝까지")`, 0 = None).
- 4행: `[▶ BGM만 듣기]` 체크 가능 버튼. 자체 `QMediaPlayer`+`QAudioOutput`으로 `bgm_offset`
  위치부터 `bgm_volume`으로 재생. 곡 끝(`mediaStatus == EndOfMedia`)·재클릭·`set_busy(True)`·
  `stop()` 에서 정지하고 버튼 텍스트를 되돌린다. 재생 중 볼륨·시작점을 바꾸면 즉시 반영
  (시작점은 `setPosition`).
- 상태: `set_mix(mix: AudioMix | None)` — 패널이 `mix`를 **직접 참조**하고 컨트롤을 채운다
  (시그널 차단). `None`이면 전체 비활성. BGM 없음이면 BGM 볼륨·시작·구간·듣기 버튼 비활성,
  원본 볼륨은 항상 활성.
- 시그널: `changed()` — 컨트롤 변경으로 `mix`를 제자리 수정한 뒤 발생. `bgm_dir_changed(str)`.
- `set_busy(bool)`: 전체 컨트롤 enable/disable + 듣기 정지.

### 3-2. 미리보기 위젯 "전체" 모드 (preview_widget.py)

- 재생바 왼쪽에 `[클립]` `[전체]` 체크 가능 버튼(배타) + `[전체 미리보기 만들기]` 버튼
  (`full_preview_requested` 시그널) + 상태 라벨(최신/오래됨).
- 상태: `_full_path: Path | None`, `_full_signature: str | None`, `_full_stale: bool`, `_mode: "clip" | "full"`.
  - `set_full_preview(path, signature)`: 파일 등록, stale 해제, `[전체]` 활성, 전체 모드로 전환·재생.
  - `mark_full_preview_stale()`: 라벨 "클립이 바뀌었습니다 — 다시 만들기". 재생은 계속 가능.
  - `clear_full_preview()`: 파일 해제(`setSource(QUrl())`), 클립 모드로, `[전체]` 비활성.
  - `full_signature() -> str | None` — 메인 창이 비교용으로 읽는다.
- 전체 모드: `player`가 `_full_path`를 캔버스 전체(0,0,1080,1920)에 재생. `title_item`,
  `time_item`, `caption_item` 숨김(이미 구워짐). 시크 범위 0~`player.duration()`, `_window_ms`는
  `(0, duration)` 반환. 드래그(`_on_drag`)·텍스트 드래그 무시, `controls_panel` 비활성,
  `setLoops(Once)` (클립 모드는 지금처럼 Infinite). 클립 모드로 돌아오면 `set_clip(self.clip)`
  경로로 원래 상태 복구(오버레이 표시, 컨트롤 활성).
- BGM 동기 재생: `bgm_player: QMediaPlayer` + `bgm_audio: QAudioOutput`. `set_audio_mix(mix)`로
  참조를 받는다(`bgm_path`가 바뀌면 `setSource`, `durationChanged`로 `_bgm_duration` 갱신).
  - `_sync_bgm(t_ms)`: 전체 모드가 아니거나 `player`가 재생 중이 아니면 `bgm_player.pause()`.
    아니면 `pos = bgm_position(t/1000, mix, _bgm_duration, total)`; `None`이면 pause; 값이면
    필요 시 `play()`, `|bgm_player.position() - pos*1000| > 250` 이면 `setPosition`.
  - 호출 시점: `_toggle_play`(재생/일시정지), `_on_seek`, `_seek_by`, `_on_position` 매 tick,
    `set_audio_mix`(볼륨·시작점·구간 변경 즉시 반영), 모드 전환.
  - 볼륨: 전체 모드에서 `audio.setVolume(mix.original_volume)`, `bgm_audio.setVolume(mix.bgm_volume)`.
    클립 모드에서는 원본 1.0, BGM 정지.
  - `bgm_player.errorOccurred` → `bgm_error(str)` 시그널 (메인 창이 상태줄에 "BGM 재생 불가: …").
- `shutdown()`에서 `bgm_player`도 정지·소스 해제.

### 3-3. 메인 윈도우 연결 (main_window.py)

- 생성: `self.bgm_panel = BgmPanel()`; `left_layout`에 `style_box` 다음, `table` 앞.
  `bgm_panel.set_bgm_dir(settings.bgm_dir)`, 프로젝트 없으면 `set_mix(None)`.
- `bgm_panel.changed` → `preview.set_audio_mix(project.audio)` + `_schedule_autosave()`.
- `bgm_panel.bgm_dir_changed(d)` → `settings.bgm_dir = d`, `settings_mod.save`.
- `_adopt_project` → `bgm_panel.set_mix(project.audio)`, `preview.set_audio_mix(project.audio)`,
  `preview.clear_full_preview()`.
- `preview.full_preview_requested` → `start_full_preview()`: ffmpeg 없으면 경고; 켜진 클립
  없으면 경고; `_set_busy(True)`; `Worker(pipeline.render_preview, project, settings, ffpaths)`;
  finished → `preview.set_full_preview(path, preview_signature(project, settings))`, busy 해제;
  failed → busy 해제 + 경고(메시지 그대로; 미준비 클립이면 "행 우클릭 → 미리보기 다시 받기" 안내 덧붙임);
  cancelled → busy 해제.
- 최신성 검사 `_check_full_preview()`: `preview.full_signature()`가 있고
  `preview_signature(project, settings)`와 다르면 `preview.mark_full_preview_stale()`.
  호출: `_on_table_changed`, `_on_clip_edited`, `_on_title_changed`, `_on_style_changed`, `apply_settings`.
- `start_render`: 기존 검사 뒤 `project.audio.has_bgm()`이고 파일이 없으면 경고 후 중단.
- `_set_busy` → `bgm_panel.set_busy(busy)`. `closeEvent` → `bgm_panel.stop()` (preview.shutdown은 기존).
- `preview.bgm_error` → `status_panel.set_idle(f"BGM 재생 불가: {msg}")`.

## 4. 오류 처리

| 상황 | 동작 |
|---|---|
| 전체 미리보기: 미준비/실패 클립이 켜져 있음 | `render_preview`가 `ValueError`(클립 이름 나열) → 경고창 + "미리보기 다시 받기" 안내 |
| 전체 미리보기 ffmpeg 실패/취소 | 작업 폴더 정리, 기존 실패/취소 경로로 메시지 |
| BGM 파일 없음 | 렌더 시작 전·듣기 버튼 클릭 시 검증 → 경고, 진행 안 함 |
| BGM 포맷을 Qt가 재생 못 함 | `bgm_error` → 상태줄 안내. 렌더는 ffmpeg가 처리하므로 계속 가능 |
| 이전 `full_*.mp4`가 재생 중이라 삭제 실패 | 무시, 다음 생성 때 다시 시도 |
| 영상 구간 시작 ≥ 끝, 구간 0.5초 미만 | BGM 생략(원본 볼륨만). 패널은 막지 않는다 |

## 5. 테스트

기존 패턴 유지: 명령 문자열 단언(`test_renderer_commands.py`), `FakeDownloader`, `ff.run`/`ff.probe`
몽키패치(`test_pipeline.py`), pytest-qt.

- `renderer`
  - `build_mix_command`: `volume=0.300`/`volume=1.000`, `atrim=start=`/`atrim=duration=`,
    `adelay=`, `afade` 시각, `-stream_loop -1`이 BGM `-i` 앞, `-c:v copy`, `-t`, BGM 없음 분기,
    구간 0.5초 미만이면 BGM 생략.
  - `build_clip_command`: `PREVIEW_PROFILE` → `540x960`, `crop=540:540`, `overlay=0:210`,
    폰트 32/18/30, `y` 절반, `-preset ultrafast -crf 28`; `in_offset=7` → `-ss 7`이 `-i` 앞;
    기본 인자 → 기존 테스트 전부 통과.
- `bgm_sync.bgm_position`: 구간 전/후 `None`, 구간 안 `offset+e`, 첫 반복 경계, 이후 반복
  `(e-first) % D`, `bgm_end=None`이면 `total`까지, `duration<=0`이면 `None`.
- `project_io`: v2 왕복(audio 전 필드), v1 파일 → `AudioMix()`, audio 손상 → 기본값·클립 유지.
- `settings`: `bgm_dir` 저장/로드.
- `pipeline`
  - `render_preview`: 클립마다 `-ss`(start−preview_start)·`preview_path` 입력·`ultrafast`,
    concat 후 `full_*.mp4` 반환, 미준비 클립 → `ValueError`에 이름, 취소 → 작업 폴더 없음.
  - `render`: audio에 BGM 있으면 `ff.run` 호출이 하나 늘고 그 명령에 BGM 경로·`-c:v copy`;
    기본 audio면 호출 수 동일; BGM 파일 없으면 다운로드 전에 `ValueError`.
  - `preview_signature`: 클립 켜기/끄기·자막·줌·타이틀·스타일 변경 시 달라지고, 끈 클립의
    자막 변경·BGM 변경에는 같음.
- GUI
  - `BgmPanel`: 폴더 나열(확장자 필터·정렬), "없음" → `bgm_path == ""`, 슬라이더 → mix 값 +
    `changed`, 찾아보기 → 항목 추가·선택, 복원 경로 없는 파일 "(파일 없음)", `set_busy` 비활성.
  - `PreviewWidget`: `set_full_preview` → 전체 모드·오버레이 숨김·`[전체]` 활성; 클립 모드
    복귀 시 오버레이 복원; `set_audio_mix` → 두 출력 볼륨; `mark_full_preview_stale` 라벨;
    `clear_full_preview` → 소스 해제·버튼 비활성.
  - `MainWindow`: `_adopt_project` → 패널 채움; `bgm_panel.changed` → 자동저장 타이머 활성;
    `_set_busy(True)` → 패널 비활성; 타이틀 변경 → stale.
- `network` 마커: 짧은 클립 2개 렌더+concat 후 생성한 사인파 wav를 BGM으로 믹스 →
  `ffprobe`로 오디오 스트림 존재·길이 ≈ 영상 길이.

## 6. 범위 밖 (이 스펙에서 하지 않음)

- 내장 무료 음원 라이브러리 (BGM 2차 — 라이선스 검토 포함).
- 자동 덕킹(원본 소리에 맞춰 BGM 볼륨 자동 조절), 여러 BGM 트랙, 클립별 BGM.
- 전체 미리보기 자동 재생성, 미리보기에 BGM 굽기.
- 댓글 있는 영상 목록 (별도 스펙).
