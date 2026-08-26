# UI 개선: 좌우 레이아웃 재배치 · 표 전체 표시 · 재생위치 조정 설계

날짜: 2026-08-26
상태: 승인됨

## 목적

1. 컷 목록 표의 7열(✓/영상/시간/자막/앞/뒤/상태)이 가로 스크롤 없이 한 번에 보이게 한다.
2. 메인 화면을 좌우 구성으로 재배치한다: **왼쪽** 위에서부터 URL 입력 → 타이틀 → 세부 설정(편집
   컨트롤) → 컷 목록, **오른쪽** 9:16 영상 미리보기 + 재생바.
3. 미리보기에 재생위치 조정(시크 슬라이더 + ±5초/±1초 버튼)을 추가한다.

## 결정 사항 (사용자 확정)

- "세부 설정" = 현재 미리보기 옆 패널의 편집 컨트롤 전부(줌, 클립 앞/뒤, 타이틀 스타일, 자막
  텍스트·스타일, 기본값으로). 오른쪽에는 영상 + 재생 컨트롤만 남는다.
- 재생위치 조정은 슬라이더 + ±버튼 병행.
- 구현 방식: PreviewWidget이 로직·시그널을 유지한 채 편집 컨트롤을 `controls_panel`(별도
  QWidget)로 분리해 노출하고, MainWindow가 왼쪽 열에 배치한다 (방식 A).

## 목표 레이아웃

```
┌─툴바(⚙ 설정)───────────────────────────────────────┐
│ ┌─왼쪽 열(최소 ≈420px)──┐ ║ ┌─오른쪽(stretch)──────┐ │
│ │ 유튜브 URL (여러 줄)    │ ║ │                      │ │
│ │ 타이틀 [댓글 분석]      │ ║ │    9:16 미리보기      │ │
│ │ ┌세부 설정───────────┐ │ ║ │                      │ │
│ │ │ 줌 / 클립 앞·뒤     │ │ ║ │                      │ │
│ │ │ 타이틀 Y·색         │ │ ║ ├──────────────────────┤ │
│ │ │ 자막 텍스트·Y·색     │ │ ║ │ ▶ -5초 -1초 ━●━━     │ │
│ │ │ [기본값으로]        │ │ ║ │   +1초 +5초 0:07/0:18 │ │
│ │ └───────────────────┘ │ ║ └──────────────────────┘ │
│ │ 컷 목록 표 (7열 전부,   │ ║                          │
│ │  남은 세로 공간 차지)    │ ║  ← QSplitter(폭 조절)     │
│ └───────────────────────┘ ║                          │
├─상태줄(요약·진행바·렌더 버튼, 전체 폭)───────────────────┤
```

## 1. UrlPanel (url_panel.py)

- 가로 2열 그리드 → 세로 배치: `유튜브 URL (한 줄에 하나)` 라벨, `urls_edit`(높이 96 유지),
  `타이틀 (상단 띠)` 라벨, 마지막 줄에 `title_edit` + `analyze_btn`(가로 나란히).
- 위젯 속성명(`urls_edit`, `title_edit`, `analyze_btn`)과 시그널·메서드는 전부 유지 —
  기존 테스트 무수정 통과.

## 2. 컷 목록 표 (main_window.py의 표 설정)

- 헤더 리사이즈 모드: `자막`(COL_CAPTION)만 `Stretch`(현행 유지), 나머지 6열은
  `ResizeToContents`.
- `setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)` — 어떤 폭에서도 7열이 다 보이고
  자막 열이 남는 폭을 흡수/양보한다.
- 왼쪽 열 컨테이너에 `setMinimumWidth(420)`을 줘 극단적 축소를 막는다.

## 3. PreviewWidget 재구성 (preview_widget.py)

### 자체 레이아웃 (오른쪽 패널)

- `QVBoxLayout(self)`: `view`(stretch 1) + 재생바(QHBoxLayout).
- 재생바 구성(왼→오): `play_btn`(▶/⏸, 기존), `back5_btn`("-5초"), `back1_btn`("-1초"),
  `seek_slider`(QSlider Horizontal, stretch 1), `fwd1_btn`("+1초"), `fwd5_btn`("+5초"),
  `pos_label`(기존 "0:00 / 0:00").

### controls_panel (왼쪽 "세부 설정")

- `self.controls_panel = QWidget()` — **부모 없이 생성**하고 PreviewWidget 레이아웃에는 넣지
  않는다. MainWindow가 왼쪽 열 레이아웃에 추가하면서 재부모화한다. (PreviewWidget 단독
  생성(테스트) 시에는 표시되지 않지만 위젯·시그널은 정상 동작; `.show()`를 호출하지 않으므로
  떠 있는 창이 생기지 않는다.)
- 내용: 기존 QFormLayout 그대로 — 줌 행, 클립(앞/뒤) 행, 타이틀(Y·색) 행,
  자막(텍스트·Y·색) 행, `reset_btn`. 재생 행(play_btn·pos_label)은 재생바로 이동했으므로
  제외.
- 모든 컨트롤 속성명 유지(`zoom_slider`, `pre_spin`, `post_spin`, `caption_edit`,
  `title_y_spin`, `title_color_btn`, `caption_y_spin`, `caption_color_btn`, `reset_btn`).

### 재생위치 조정 (신규)

- 순수 함수 `seek_target(pos_ms: int, delta_s: int, start_ms: int, end_ms: int) -> int`
  (모듈 수준): `pos + delta*1000`을 `[start, max(start, end - 200)]`로 클램프해 반환
  (끝 200ms 여유는 즉시 루프-백 방지).
- `seek_slider` 범위: `0 .. max(0, end-start)` (ms, `_window_ms()` 기준). 클립이 없거나
  미리보기 미로드면 0..0 + 비활성.
- `_update_seek_range()`: 범위·활성 상태 갱신 (`blockSignals`로 감싸 범위 변경이 시크를
  유발하지 않게 한다). `set_clip`, `sync_from_clip`, `_on_pre`, `_on_post`,
  `refresh_media`에서 호출 (앞/뒤 초가 바뀌면 구간이 변하므로).
- 사용자 조작: `seek_slider.valueChanged(v)` → `player.setPosition(start + v)` —
  드래그와 트랙 클릭(페이지 이동) 모두 시크된다. 프로그램적 갱신(`_on_position`,
  `_update_seek_range`)은 전부 `blockSignals`로 감싸므로 valueChanged는 사용자 조작에서만
  발화 — 되먹임 없음.
  ±버튼 → `_seek_by(±5 | ±1)`: `player.setPosition(seek_target(player.position(), delta,
  start, end))`.
- 재생 중 갱신: 기존 `_on_position(ms)`이 pos_label 갱신에 더해
  `seek_slider`를 `blockSignals`로 감싸 `setValue(clamp(ms - start, 0, end-start))`.
  기존 루프 재생(끝 도달 → 처음) 로직은 유지.
- `_set_controls_enabled` 목록에 `seek_slider`, `back5_btn`, `back1_btn`, `fwd1_btn`,
  `fwd5_btn` 추가 (클립 선택 시에만 활성; 스타일 컨트롤은 기존대로 항상 활성).

## 4. MainWindow (main_window.py)

- 왼쪽 열: `QWidget` + `QVBoxLayout` — `url_panel`, `QGroupBox("세부 설정")`(안에
  `preview.controls_panel`), `table`(stretch 1). `setMinimumWidth(420)`.
- `QSplitter(Qt.Horizontal)`: 왼쪽 열 | `preview`. `setStretchFactor(0, 0)`, `(1, 1)`,
  `setSizes([460, 820])`.
- central `QVBoxLayout`: 스플리터(stretch 1) + `status_panel` (상단 url_panel 배치는 제거 —
  왼쪽 열로 이동).
- `_set_busy`: 기존 `self.preview.setEnabled(not busy)`에 더해
  `self.preview.controls_panel.setEnabled(not busy)` — controls_panel이 preview의 자식이
  아니게 되므로 별도로 잠가야 한다 (렌더 중 편집 차단 유지).

## 5. 테스트

- **기존 유지**: url_panel·clip_table·preview(속성 기반) 테스트는 무수정 통과가 목표.
  preview의 레이아웃 의존 테스트가 있으면 최소 수정.
- **신규 (preview)**: `seek_target` 클램프(구간 안/앞/뒤/빈 구간); `_update_seek_range`가
  pre/post 반영; 클립 없으면 시크 컨트롤 비활성; `_on_position`이 슬라이더를 시그널 없이
  갱신(재귀 없음); `sliderMoved` → `setPosition(start+v)` 호출(QMediaPlayer는 소스 없이
  position 0 유지이므로 monkeypatch로 호출 인자 검증).
- **신규 (main_window)**: 왼쪽 열에 url_panel·세부 설정 그룹·표가 배치되는 스모크;
  `_set_busy(True)`가 controls_panel도 비활성화; 표 리사이즈 모드(자막 Stretch, 나머지
  ResizeToContents)와 가로 스크롤바 정책.

## 완료 기준

- 표 7열이 가로 스크롤 없이 항상 보인다 (자막 열이 폭 흡수).
- 왼쪽 위→아래: URL → 타이틀 → 세부 설정 → 컷 목록; 오른쪽: 영상 + 재생바. 스플리터로 폭 조절.
- 시크 슬라이더·±버튼으로 클립 구간 안에서 재생위치 이동, 재생 중 슬라이더가 따라 움직임.
- 렌더 중(busy)에는 세부 설정·시크 조작이 잠긴다.
- `pytest -q -m "not network"` 전부 통과.
