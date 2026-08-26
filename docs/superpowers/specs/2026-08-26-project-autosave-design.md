# 작업 자동 저장·복원 + 표 스핀박스 화살표 제거 설계

날짜: 2026-08-26
상태: 승인됨

## 목적

1. 컷 표의 앞/뒤 편집 스핀박스에서 위/아래 화살표를 없애고 숫자를 바로 타이핑하게 한다.
2. 편집 상태(URL·타이틀·컷별 자막/앞뒤/줌/이동/체크)를 자동 저장하고, 앱을 다시 켜면
   자동 복원해 이어서 작업할 수 있게 한다.

## 결정 사항 (사용자 확정)

- 저장 방식: **자동 저장 + 시작 시 자동 복원** (버튼 없음). 새 "댓글 분석"을 돌리면 새 작업으로 교체.

## 1. 표 스핀박스 화살표 제거 (clip_table.py)

- `SecondsDelegate.createEditor`: `sb.setButtonSymbols(QAbstractSpinBox.NoButtons)` 추가.
  범위 검증(0~120)·"초" 접미사는 유지. import에 `QAbstractSpinBox` 추가.

## 2. 프로젝트 직렬화 모듈 (신규 `stampcut/core/project_io.py`)

Qt 의존 없음. JSON 스키마 버전 `VERSION = 1`.

### API

- `project_path() -> Path` = `settings.config_dir() / "project.json"`
- `save(project: Project, path: Path) -> None` — 원자적 쓰기: 같은 폴더의 `.tmp`에 쓴 뒤
  `os.replace`. 부모 폴더 `mkdir(parents=True, exist_ok=True)`.
- `load(path: Path) -> Project | None` — 파일 없음/JSON 깨짐/`version != 1`/필수 키 누락 등
  어떤 오류든 조용히 `None` (새 작업으로 시작). 예외를 밖으로 내보내지 않는다.

### 직렬화 규칙

- `VideoInfo`: 전 필드. `published_at`은 `isoformat()` ↔ `datetime.fromisoformat`.
- `Mention`: 전 필드.
- `Clip`: `id, t, score, caption, pre, post, enabled, over_limit, zoom, pan_x, pan_y,
  preview_start, preview_end, mentions` + `video_id`(소속 영상 참조),
  `preview_path`/`final_path`는 문자열 또는 null.
- `Project`: `urls, title, videos, clips` (warnings는 저장하지 않음 — 복원 시 `[]`).
- **복원 시 상태 재계산**: `preview_path`가 있고 파일이 실제로 존재하면
  `status = READY`, 아니면 `preview_path = None` + `status = PENDING` (→ 기존
  `fetch_previews`의 `preview_covers` 검사로 자동 재다운로드). `final_path`도 파일이
  없으면 `None`. `error = ""`, `DOWNLOADING/ERROR` 상태는 저장·복원하지 않는다.
- `video_id`가 `videos`에 없는 클립은 건너뛴다 (파일 손상 방어).

## 3. MainWindow 자동 저장·복원 (main_window.py)

### 생성자 인자

- `MainWindow(settings=None, project_file: Path | None = None)` — **None이면 자동 저장·복원
  완전 비활성** (기존 테스트 안전). 실제 경로는 app.py에서만 주입.

### 복원 (시작 시)

- `__init__` 끝에서: `project_file`이 있고 `project_io.load()`가 Project를 돌려주면
  `_adopt_project(project, restored=True)`.
- `_adopt_project(project, restored=False)` — `_on_analyzed`와 복원이 공유하는 공통 경로:
  self.project 설정, `url_panel.urls_edit.setPlainText("\n".join(project.urls))`(복원 시에만),
  타이틀·모델·프리뷰 타이틀·요약 갱신, 클립 있으면 `selectRow(0)` + ffmpeg 있으면
  `start_previews(project.clips)`. 복원 시 상태줄 "이전 작업을 불러왔습니다".
  `_on_analyzed`는 `_adopt_project(project)` 호출 + 기존의 분석 전용 처리(빈 클립 안내,
  warnings, busy 해제, analysis_done)만 남긴다.

### 자동 저장

- 디바운스: `self._autosave_timer = QTimer(self)` singleShot, interval 1500ms,
  timeout → `_flush_autosave()`.
- `_schedule_autosave()`: `project_file`과 `self.project`가 있을 때만 `timer.start()` (재시작 = 디바운스).
- `_flush_autosave()`: 타이머 중지 후 `project_io.save(self.project, self.project_file)`;
  `OSError`는 로그만 남기고 무시 (편집 흐름을 막지 않는다).
- 호출 지점: `_on_clip_edited`, `_on_table_changed`, `_on_title_changed`,
  `_on_clip_updated`(미리보기 완료로 경로·상태 변경), `_adopt_project`(분석 직후 1회),
  `_on_rendered`. `closeEvent`에서 `event.accept()` 전에 타이머가 활성이거나 project가 있으면
  `_flush_autosave()` 즉시 실행.

### app.py

- `win = MainWindow(project_file=project_io.project_path())`.

## 4. 테스트

- **clip_table**: `SecondsDelegate.createEditor` 결과의 `buttonSymbols() == NoButtons`,
  범위·접미사 유지.
- **project_io**: 편집값(자막·pre/post·zoom·pan·enabled 변경) 왕복 보존; 미리보기 파일이
  실존하면 READY, 없으면 PENDING+preview_path None; 손상 파일/버전 불일치/파일 없음 → None;
  원자적 쓰기(저장 후 .tmp 잔여물 없음); 모르는 video_id 클립 스킵.
- **main_window**: `project_file` 없이 생성하면 저장 파일이 안 생김(기존 테스트 그대로);
  `project_file=tmp` 주고 편집 → `_flush_autosave()` 후 파일 생성·내용 반영;
  저장된 파일로 재생성 → 표·타이틀·URL 복원; closeEvent가 마지막 상태를 저장.

## 완료 기준

- 표에서 앞/뒤 더블클릭 시 화살표 없는 입력칸에 숫자 타이핑.
- 컷 편집 후 앱 종료 → 재시작하면 URL·타이틀·컷 편집값 그대로 복원, 캐시된 미리보기는
  재다운로드 없이 바로 재생, 없는 것만 다시 받음.
- `pytest -q -m "not network"` 전부 통과.
