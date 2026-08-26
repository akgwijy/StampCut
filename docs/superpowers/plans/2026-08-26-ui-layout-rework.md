# UI 레이아웃 재배치 · 표 전체 표시 · 재생위치 조정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 메인 화면을 좌(URL→타이틀→세부 설정→컷 목록) / 우(영상+재생바) 구성으로 재배치하고, 컷 표 7열을 가로 스크롤 없이 표시하며, 미리보기에 시크 슬라이더+±버튼을 추가한다.

**Architecture:** PreviewWidget이 로직·시그널·위젯 속성을 전부 유지한 채 편집 컨트롤을 부모 없는 `controls_panel` QWidget으로 분리해 노출하고, 자신은 영상 뷰+재생바(시크 포함)만 배치한다. MainWindow가 왼쪽 열(QVBoxLayout)에 url_panel·"세부 설정" 그룹박스(controls_panel)·표를 쌓고 QSplitter 오른쪽에 preview를 둔다. 스펙: `docs/superpowers/specs/2026-08-26-ui-layout-rework-design.md`

**Tech Stack:** Python 3.12, PySide6, pytest + pytest-qt

## Global Constraints

- 위젯 **속성명은 전부 유지** (`urls_edit`, `title_edit`, `analyze_btn`, `zoom_slider`, `pre_spin`, `post_spin`, `caption_edit`, `title_y_spin`, `title_color_btn`, `caption_y_spin`, `caption_color_btn`, `reset_btn`, `play_btn`, `pos_label`) — 기존 테스트 무수정 통과가 목표.
- `controls_panel`은 **부모 없이 생성**, PreviewWidget 레이아웃에 넣지 않는다. MainWindow가 재부모화. `.show()` 금지.
- 시크는 클립 구간 기준: `seek_target(pos_ms, delta_s, start_ms, end_ms)`는 `[start, max(start, end-200)]` 클램프 (끝 200ms 여유 = 즉시 루프-백 방지).
- 슬라이더 프로그램적 갱신(`_on_position`, `_update_seek_range`)은 전부 `blockSignals`로 감싼다 — `valueChanged`는 사용자 조작에서만 발화.
- 표: `자막`(COL_CAPTION)만 Stretch, 나머지 ResizeToContents, 가로 스크롤바 Off, 왼쪽 열 `setMinimumWidth(420)`.
- 렌더 중(busy): 기존 `preview.setEnabled(False)`에 더해 `preview.controls_panel.setEnabled(False)` 필수 (분리로 인해 자동 전파가 끊김).
- 스타일 컨트롤(타이틀/자막 Y·색)은 클립 무관 활성 유지; 시크 컨트롤은 클립 선택 시에만 활성(`_set_controls_enabled`).
- Test runs: `.venv/Scripts/python.exe -m pytest ...` from `e:\workspace\StampCut`.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: UrlPanel 세로 배치

**Files:**
- Modify: `stampcut/gui/url_panel.py` (import와 `__init__`의 레이아웃 블록 26~33행)
- Test: `tests/test_url_panel.py`

**Interfaces:**
- Consumes: 기존 `UrlPanel` 위젯 속성
- Produces: 동일 속성의 세로 배치 UrlPanel — URL 라벨·입력(위), 타이틀 라벨·입력+분석 버튼(아래). Task 4의 왼쪽 열이 이 위젯을 그대로 쌓는다.

- [ ] **Step 1: 실패하는 테스트 — `tests/test_url_panel.py`에 추가**

```python
def test_vertical_stacking(qtbot):
    w = UrlPanel()
    qtbot.addWidget(w)
    w.layout().activate()
    # URL 입력이 위, 타이틀이 아래, 분석 버튼은 타이틀 오른쪽
    assert w.urls_edit.geometry().bottom() < w.title_edit.geometry().top()
    assert w.analyze_btn.geometry().left() > w.title_edit.geometry().left()
    assert abs(w.analyze_btn.geometry().center().y() - w.title_edit.geometry().center().y()) < 20
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_url_panel.py::test_vertical_stacking -v`
Expected: FAIL — 현재는 URL(왼쪽)·타이틀(오른쪽) 그리드라 `urls_edit.bottom() < title_edit.top()`이 거짓

- [ ] **Step 3: 구현 — `url_panel.py`**

import 변경 (Qt 불필요, QGridLayout→QVBoxLayout/QHBoxLayout):

```python
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget
```

`__init__`의 레이아웃 블록(기존 `layout = QGridLayout(self)`부터 `setColumnStretch`까지)을 교체:

```python
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("유튜브 URL (한 줄에 하나)"))
        layout.addWidget(self.urls_edit)
        layout.addWidget(QLabel("타이틀 (상단 띠)"))
        trow = QHBoxLayout()
        trow.addWidget(self.title_edit, 1)
        trow.addWidget(self.analyze_btn)
        layout.addLayout(trow)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_url_panel.py -v`
Expected: 전부 PASS (기존 테스트 무수정)

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/url_panel.py tests/test_url_panel.py
git commit -m "feat(gui): stack URL input above title in url panel"
```

---

### Task 2: PreviewWidget 재구성 — controls_panel 분리 + 영상/재생바 세로 배치

**Files:**
- Modify: `stampcut/gui/preview_widget.py` (`__init__`의 레이아웃 블록 168~202행)
- Test: `tests/test_preview_widget.py`

**Interfaces:**
- Consumes: 기존 PreviewWidget 위젯·시그널 전부
- Produces: `PreviewWidget.controls_panel: QWidget` (부모 없음, 줌/클립/타이틀/자막/기본값으로 폼 포함 — Task 4의 MainWindow가 왼쪽 열에 재부모화), PreviewWidget 자체 레이아웃 = `view`(stretch) + `transport: QHBoxLayout`(play_btn, pos_label — Task 3이 시크 위젯을 이 레이아웃에 추가하므로 `self.transport` 속성으로 보관)

- [ ] **Step 1: 실패하는 테스트 — `tests/test_preview_widget.py`에 추가**

```python
def test_controls_panel_is_detachable(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    # 편집 컨트롤은 부모 없는 controls_panel 안에 (MainWindow가 왼쪽 열로 가져감)
    assert w.controls_panel.parent() is None
    for widget in (w.zoom_slider, w.pre_spin, w.post_spin, w.caption_edit, w.title_y_spin, w.caption_y_spin, w.reset_btn):
        assert widget.parent() is w.controls_panel or widget.parentWidget() is w.controls_panel
    # 영상 뷰·재생 컨트롤은 PreviewWidget 자신에
    assert w.view.parentWidget() is w
    assert w.play_btn.parentWidget() is w and w.pos_label.parentWidget() is w
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py::test_controls_panel_is_detachable -v`
Expected: FAIL — `AttributeError: ... has no attribute 'controls_panel'`

- [ ] **Step 3: 구현 — `preview_widget.py` `__init__`의 레이아웃 블록 교체**

기존 블록(`controls = QFormLayout()`부터 `layout.addLayout(side, 2)`까지)을 다음으로 교체:

```python
        self.controls_panel = QWidget()
        controls = QFormLayout(self.controls_panel)
        zrow = QHBoxLayout()
        zrow.addWidget(self.zoom_slider)
        zrow.addWidget(self.zoom_label)
        controls.addRow("줌", zrow)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("앞"))
        srow.addWidget(self.pre_spin)
        srow.addWidget(QLabel("뒤"))
        srow.addWidget(self.post_spin)
        controls.addRow("클립", srow)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Y"))
        trow.addWidget(self.title_y_spin)
        trow.addWidget(self.title_color_btn)
        trow.addStretch(1)
        controls.addRow("타이틀", trow)
        crow = QHBoxLayout()
        crow.addWidget(self.caption_edit, 1)
        crow.addWidget(self.caption_y_spin)
        crow.addWidget(self.caption_color_btn)
        controls.addRow("자막", crow)
        controls.addRow(self.reset_btn)

        self.transport = QHBoxLayout()
        self.transport.addWidget(self.play_btn)
        self.transport.addWidget(self.pos_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addLayout(self.transport)
        self._set_controls_enabled(False)
```

주의: 재생 행(play_btn·pos_label)은 폼에서 빠지고 transport로 이동. `QHBoxLayout`(기존 최상위)은 더 이상 안 쓰므로 남는 import 없는지 확인 (QHBoxLayout은 행들에 계속 사용됨).

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py -v`
Expected: 전부 PASS — 기존 테스트는 속성 기반이라 무수정 통과해야 한다

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/preview_widget.py tests/test_preview_widget.py
git commit -m "refactor(gui): split preview into video pane and detachable controls panel"
```

---

### Task 3: 재생위치 조정 — 시크 슬라이더 + ±5초/±1초 버튼

**Files:**
- Modify: `stampcut/gui/preview_widget.py`
- Test: `tests/test_preview_widget.py`

**Interfaces:**
- Consumes: Task 2의 `self.transport`, 기존 `_window_ms()`, `_on_position`, `player`
- Produces: 모듈 함수 `seek_target(pos_ms: int, delta_s: int, start_ms: int, end_ms: int) -> int`, 위젯 `seek_slider: QSlider`, `back5_btn/back1_btn/fwd1_btn/fwd5_btn: QPushButton`, 메서드 `_update_seek_range()`, `_on_seek(value)`, `_seek_by(delta_s)`

- [ ] **Step 1: 실패하는 테스트 — `tests/test_preview_widget.py`에 추가**

import에 `seek_target` 추가 (`from stampcut.gui.preview_widget import ...` 줄).

```python
def test_seek_target_clamps_to_window():
    assert seek_target(5000, 5, 0, 18000) == 10000
    assert seek_target(1000, -5, 0, 18000) == 0
    assert seek_target(17500, 5, 0, 18000) == 17800  # 끝 200ms 앞에서 멈춤
    assert seek_target(60000, -5, 55000, 73000) == 55000
    assert seek_target(0, -1, 0, 0) == 0  # 빈 구간


def test_seek_controls_disabled_without_clip(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    for widget in (w.seek_slider, w.back5_btn, w.back1_btn, w.fwd1_btn, w.fwd5_btn):
        assert not widget.isEnabled()


def test_seek_slider_and_buttons_drive_player(qtbot, monkeypatch, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    clip.preview_start = 700  # 구간: (755-700)s..(773-700)s → 55000..73000ms, 길이 18000
    w.set_clip(clip)
    assert w.seek_slider.maximum() == 18000
    assert w.seek_slider.isEnabled()
    calls = []
    monkeypatch.setattr(w.player, "setPosition", lambda v: calls.append(v))
    w.seek_slider.setValue(3000)  # 사용자 조작 → 구간 시작 + 3초
    assert calls[-1] == 58000
    monkeypatch.setattr(w.player, "position", lambda: 60000)
    w._seek_by(5)
    assert calls[-1] == 65000
    w._seek_by(-5)
    assert calls[-1] == 55000  # 시작 클램프
    w.pre_spin.setValue(5)  # 앞 5초 → 구간 53000..73000, 길이 20000
    assert w.seek_slider.maximum() == 20000


def test_on_position_syncs_slider_without_feedback(qtbot, monkeypatch, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    clip = make_clip(make_video(), t=758)
    clip.preview_start = 700
    w.set_clip(clip)
    calls = []
    monkeypatch.setattr(w.player, "setPosition", lambda v: calls.append(v))
    w._on_position(58000)  # 재생 위치 갱신 → 슬라이더만 움직이고 시크(되먹임)는 없어야 함
    assert w.seek_slider.value() == 3000
    assert calls == []
    assert w.pos_label.text() == "0:03 / 0:18"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py -v`
Expected: 새 테스트 4개 FAIL/ERROR (`ImportError: cannot import name 'seek_target'` 등), 기존 테스트 PASS

- [ ] **Step 3: 구현 — `preview_widget.py`**

(a) 모듈 수준 함수 (`load_font_family` 아래):

```python
def seek_target(pos_ms: int, delta_s: int, start_ms: int, end_ms: int) -> int:
    """구간 안에서 delta_s초 이동한 목표 위치(ms). 끝 200ms 앞에서 멈춰 즉시 루프-백을 피한다."""
    hi = max(start_ms, end_ms - 200)
    return min(hi, max(start_ms, pos_ms + delta_s * 1000))
```

(b) `__init__`에서 `self.pos_label = QLabel("0:00 / 0:00")` 아래에 위젯 추가:

```python
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.valueChanged.connect(self._on_seek)
        self.back5_btn = QPushButton("-5초")
        self.back5_btn.clicked.connect(lambda: self._seek_by(-5))
        self.back1_btn = QPushButton("-1초")
        self.back1_btn.clicked.connect(lambda: self._seek_by(-1))
        self.fwd1_btn = QPushButton("+1초")
        self.fwd1_btn.clicked.connect(lambda: self._seek_by(1))
        self.fwd5_btn = QPushButton("+5초")
        self.fwd5_btn.clicked.connect(lambda: self._seek_by(5))
```

(c) Task 2의 transport 블록을 다음으로 교체:

```python
        self.transport = QHBoxLayout()
        self.transport.addWidget(self.play_btn)
        self.transport.addWidget(self.back5_btn)
        self.transport.addWidget(self.back1_btn)
        self.transport.addWidget(self.seek_slider, 1)
        self.transport.addWidget(self.fwd1_btn)
        self.transport.addWidget(self.fwd5_btn)
        self.transport.addWidget(self.pos_label)
```

(d) `_set_controls_enabled`의 위젯 튜플에 시크 위젯 추가:

```python
    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self.play_btn, self.zoom_slider, self.pre_spin, self.post_spin, self.caption_edit, self.reset_btn,
                  self.seek_slider, self.back5_btn, self.back1_btn, self.fwd1_btn, self.fwd5_btn):
            w.setEnabled(on)
```

(e) 재생 섹션에 메서드 추가:

```python
    def _update_seek_range(self) -> None:
        start, end = self._window_ms()
        self.seek_slider.blockSignals(True)
        self.seek_slider.setRange(0, max(0, end - start))
        self.seek_slider.blockSignals(False)

    def _on_seek(self, value: int) -> None:
        start, end = self._window_ms()
        if end:
            self.player.setPosition(start + value)

    def _seek_by(self, delta_s: int) -> None:
        start, end = self._window_ms()
        if end:
            self.player.setPosition(seek_target(self.player.position(), delta_s, start, end))
```

(f) `_on_position`을 다음으로 교체 (슬라이더 동기화 추가):

```python
    def _on_position(self, ms: int) -> None:
        start, end = self._window_ms()
        if end and (ms >= end or ms < start - 500):
            self.player.setPosition(start)
            return
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(min(max(0, ms - start), max(0, end - start)))
        self.seek_slider.blockSignals(False)
        self.pos_label.setText(f"{format_time(max(0, ms - start) // 1000)} / {format_time(max(0, end - start) // 1000)}")
```

(g) 구간이 바뀌는 지점마다 `_update_seek_range()` 호출:
- `set_clip`의 `if clip is None:` 분기에서 `self.relayout()` 다음 줄에 추가
- `sync_from_clip` 끝(`self.relayout()` 다음)에 추가
- `_on_pre`와 `_on_post`에서 `self._emit()` 앞에 추가
- `refresh_media`에서 `self.player.setPosition(...)` 앞에 추가

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/preview_widget.py tests/test_preview_widget.py
git commit -m "feat(gui): seek slider and step buttons in the preview transport bar"
```

---

### Task 4: MainWindow 좌우 재배치 · 표 7열 전부 표시 · busy 잠금 · README

**Files:**
- Modify: `stampcut/gui/main_window.py` (import, `__init__`의 표 설정 93행 부근과 레이아웃 블록 115~125행, `_set_busy` 336행 부근)
- Modify: `README.md`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: Task 1의 세로 UrlPanel, Task 2~3의 `preview.controls_panel`·재생바
- Produces: 좌(url_panel→세부 설정 그룹→표)/우(preview) 스플리터 배치, busy 시 controls_panel 잠금

- [ ] **Step 1: 실패하는 테스트 — `tests/test_main_window.py`에 추가**

```python
def test_left_column_layout_and_full_width_table(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHeaderView
    from stampcut.gui.clip_table import COL_CAPTION

    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    left = w.url_panel.parentWidget()
    assert w.table.parentWidget() is left  # URL과 표가 같은 왼쪽 열에
    assert left.minimumWidth() == 420
    assert w.preview.controls_panel.window() is w  # 세부 설정이 메인 창 안에 배치됨
    assert w.preview.parentWidget() is not left  # 미리보기는 오른쪽
    header = w.table.horizontalHeader()
    for col in range(w.model.columnCount()):
        expected = QHeaderView.Stretch if col == COL_CAPTION else QHeaderView.ResizeToContents
        assert header.sectionResizeMode(col) == expected
    assert w.table.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


def test_busy_locks_detached_controls_panel(qtbot):
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w._set_busy(True)
    assert not w.preview.controls_panel.isEnabled()
    w._set_busy(False)
    assert w.preview.controls_panel.isEnabled()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main_window.py -v`
Expected: 새 테스트 2개 FAIL (표가 왼쪽 열이 아닌 스플리터 직속, controls_panel 미배치·미잠금), 기존 테스트 PASS

- [ ] **Step 3: 구현 — `main_window.py`**

(a) QtWidgets import 목록에 `QGroupBox` 추가.

(b) 표 설정: 기존 `self.table.horizontalHeader().setSectionResizeMode(COL_CAPTION, QHeaderView.Stretch)` 줄을 다음으로 교체:

```python
        header = self.table.horizontalHeader()
        for col in range(self.model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == COL_CAPTION else QHeaderView.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
```

(c) 레이아웃 블록(기존 `splitter = QSplitter(...)`부터 `self.setCentralWidget(central)`까지)을 교체:

```python
        left = QWidget()
        left.setMinimumWidth(420)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.url_panel)
        style_box = QGroupBox("세부 설정")
        QVBoxLayout(style_box).addWidget(self.preview.controls_panel)
        left_layout.addWidget(style_box)
        left_layout.addWidget(self.table, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 820])
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status_panel)
        self.setCentralWidget(central)
```

(기존 `layout.addWidget(self.url_panel)` 상단 배치 줄은 사라진다 — 왼쪽 열로 이동.)

(d) `_set_busy`에 한 줄 추가:

```python
    def _set_busy(self, busy: bool) -> None:
        self.url_panel.set_busy(busy)
        self.status_panel.set_busy(busy)
        self.table.setEnabled(not busy)
        self.preview.setEnabled(not busy)
        self.preview.controls_panel.setEnabled(not busy)
        self.settings_action.setEnabled(not busy)
```

(e) `README.md` 사용법의 미리보기 관련 항목 아래에 한 줄 추가:

```markdown
- 화면은 왼쪽(URL·타이틀·세부 설정·컷 목록) / 오른쪽(9:16 미리보기) 구성이며, 미리보기 아래 재생바(슬라이더, -5초/-1초/+1초/+5초)로 재생 위치를 옮길 수 있습니다.
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `.venv/Scripts/python.exe -m pytest -q -m "not network"`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/main_window.py tests/test_main_window.py README.md
git commit -m "feat(gui): left column layout, full-width clip table, busy lock for detached controls"
```

---

## 완료 기준

- `pytest -q -m "not network"` 전부 통과
- 앱에서 손으로 확인: 왼쪽 위→아래 URL→타이틀→세부 설정→컷 목록, 오른쪽 영상+재생바; 표 7열이 가로 스크롤 없이 보임; 시크 슬라이더·±버튼으로 구간 안 이동; 렌더 중 세부 설정·시크 잠김
