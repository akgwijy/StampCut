# 타이틀·자막 스타일 편집 (위치·색상) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 타이틀과 컷 자막의 세로 위치·색상을 편집 메인 화면(미리보기 드래그 + 옆 패널 컨트롤)에서 수정하고 설정 파일에 저장한다.

**Architecture:** `Settings`에 스타일 필드 4개(`title_y/title_color/caption_y/caption_color`)를 추가하고, 렌더러와 미리보기가 `LAYOUT` 고정값 대신 이 값을 읽는다. 미리보기 `_DragView`는 클릭 지점에 텍스트가 있으면 세로 드래그로, 아니면 기존 영상 팬으로 동작한다. 스타일 변경은 `style_changed` 시그널로 메인 창에 전달돼 settings.json에 저장된다.

**Tech Stack:** Python 3.12, PySide6, pytest + pytest-qt. 스펙: `docs/superpowers/specs/2026-08-26-text-style-editing-design.md`

## Global Constraints

- 기본값(`title_y=210, caption_y=1552, 색 #FFFFFF`)에서 렌더 출력·미리보기는 기존과 **완전히 동일**해야 한다 (기존 테스트가 그대로 통과해야 함)
- 이동은 **세로만**. 가로는 항상 중앙 정렬 (`x=(w-text_w)/2`)
- `title_y`는 텍스트 블록의 **세로 중심**, `caption_y`는 **상단** 기준. 시간 표시는 항상 자막 위 42px (`TIME_GAP = 42`)
- 자막 위치·색상은 모든 컷 공유. 자막 텍스트만 컷별
- Y 값은 0~1920으로 클램프. 색상은 `#rrggbb` 문자열 (QColor.name()은 소문자를 반환하므로 소문자 허용)
- UI는 메인 화면(미리보기)에만 추가. 설정 창(settings_dialog.py)은 **수정하지 않는다** — `result_settings()`가 `dataclasses.replace(self._initial, ...)`라 새 필드는 자동 보존됨
- 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` 추가 (기존 관례)
- 테스트 실행은 항상 `.venv/Scripts/python.exe -m pytest ...` (venv 직접 경로)

---

### Task 1: Settings 스타일 필드 4개

**Files:**
- Modify: `stampcut/core/models.py` (Settings 데이터클래스, 12~26행 부근)
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: 기존 `Settings` 데이터클래스, `stampcut.core.settings.load/save`
- Produces: `Settings.title_y: int = 210`, `Settings.title_color: str = "#FFFFFF"`, `Settings.caption_y: int = 1552`, `Settings.caption_color: str = "#FFFFFF"` — 이후 모든 태스크가 이 필드명을 그대로 사용

- [ ] **Step 1: 실패하는 테스트 — `tests/test_settings.py` 끝에 추가**

```python
def test_style_fields_roundtrip_and_legacy_defaults(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(title_y=300, title_color="#ff0000", caption_y=1400, caption_color="#00ff00")
    sm.save(s, p)
    assert sm.load(p) == s
    # 스타일 필드가 없는 구버전 설정 파일 → 기본값 (기존 출력과 동일한 배치)
    p.write_text(json.dumps({"api_key": "K"}), "utf-8")
    old = sm.load(p)
    assert (old.title_y, old.title_color) == (210, "#FFFFFF")
    assert (old.caption_y, old.caption_color) == (1552, "#FFFFFF")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_settings.py::test_style_fields_roundtrip_and_legacy_defaults -v`
Expected: FAIL — `TypeError: Settings.__init__() got an unexpected keyword argument 'title_y'`

- [ ] **Step 3: 구현 — `stampcut/core/models.py`의 `Settings`에 필드 추가**

`preview_margin_post: int = 60` 줄 아래에:

```python
    title_y: int = 210  # 타이틀 블록 세로 중심 (기존 420px 밴드 중앙과 동일)
    title_color: str = "#FFFFFF"
    caption_y: int = 1552  # 자막 블록 상단
    caption_color: str = "#FFFFFF"
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_settings.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/core/models.py tests/test_settings.py
git commit -m "feat(core): title/caption style fields in Settings"
```

---

### Task 2: 렌더러가 settings 스타일을 사용

**Files:**
- Modify: `stampcut/core/renderer.py` (LAYOUT 아래에 `TIME_GAP` 추가, `build_clip_command` 139~153행 부근)
- Test: `tests/test_renderer_commands.py`

**Interfaces:**
- Consumes: Task 1의 `Settings.title_y/title_color/caption_y/caption_color`, 기존 `_clamp`, `ff_color`, `_drawtext`
- Produces: 모듈 상수 `TIME_GAP: int = 42` (Task 3의 미리보기가 import), drawtext y/색이 settings를 따르는 `build_clip_command`

- [ ] **Step 1: 실패하는 테스트 — `tests/test_renderer_commands.py`에 추가**

```python
def test_custom_text_style_from_settings(tmp_path, make_video, make_clip):
    s = Settings(title_y=300, title_color="#ff8800", caption_y=1400, caption_color="#00ff00")
    fc = filter_complex(build(tmp_path, make_clip(make_video(), t=758), s)[0])
    assert "y=300-text_h/2" in fc and "fontcolor=0xff8800" in fc  # 타이틀: 세로 중심 기준
    assert "y=1400" in fc and "fontcolor=0x00ff00" in fc          # 자막: 상단 기준
    assert "y=1358" in fc                                          # 시간: 자막 위 42px


def test_style_y_clamped_to_canvas(tmp_path, make_video, make_clip):
    s = Settings(title_y=-50, caption_y=99999)
    fc = filter_complex(build(tmp_path, make_clip(make_video(), t=758), s)[0])
    assert "y=0-text_h/2" in fc and "y=1920" in fc and "y=1878" in fc
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_renderer_commands.py -v`
Expected: 새 테스트 2개 FAIL (y=1510/1552 고정값이 나옴), 기존 테스트는 PASS

- [ ] **Step 3: 구현 — `renderer.py`**

`ZOOM_MAX = 3.0` 줄 아래에 상수 추가:

```python
TIME_GAP = 42  # 시간 표시는 자막 상단에서 이만큼 위
```

`build_clip_command`에서 `g = compute_square_geometry(...)` 줄 다음에 클램프 추가:

```python
    title_y = int(_clamp(settings.title_y, 0, L["canvas_h"]))
    caption_y = int(_clamp(settings.caption_y, 0, L["canvas_h"]))
```

drawtext 3줄을 다음으로 교체 (y·색만 변경):

```python
    if title.strip():
        filters.append(_drawtext(last, "c1", title_txt, font_path, L["title_font"], ff_color(settings.title_color), "(w-text_w)/2", f"{title_y}-text_h/2", line_spacing=L["line_spacing"]))
        last = "c1"
    if settings.show_time_in_caption:
        filters.append(_drawtext(last, "c2", time_txt, font_path, L["time_font"], ff_color(L["time_color"]), "(w-text_w)/2", str(caption_y - TIME_GAP)))
        last = "c2"
    if clip.caption.strip():
        filters.append(_drawtext(last, "c3", caption_txt, font_path, L["caption_font"], ff_color(settings.caption_color), "(w-text_w)/2", str(caption_y), border=L["caption_border"], line_spacing=L["line_spacing"]))
        last = "c3"
```

주의: 기본값에서 `210-text_h/2`는 기존 `(420-text_h)/2`와 수학적으로 동일하고, `caption_y - TIME_GAP = 1552-42 = 1510`으로 기존과 같다. `LAYOUT`의 `title_band_h`/`time_y`/`caption_y` 키는 이 파일에서 더 이상 참조하지 않는다 (키 삭제는 미리보기까지 바꾼 뒤 Task 3에서).

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_renderer_commands.py -v`
Expected: 전부 PASS — 특히 `test_basic_command`(기본값이 y=1510, y=1552 그대로인지)가 여전히 PASS여야 한다

- [ ] **Step 5: Commit**

```powershell
git add stampcut/core/renderer.py tests/test_renderer_commands.py
git commit -m "feat(renderer): drawtext position/color from settings, time follows caption"
```

---

### Task 3: 미리보기 — settings 기반 배치·색 + 스타일 컨트롤

**Files:**
- Modify: `stampcut/gui/preview_widget.py`
- Modify: `stampcut/core/renderer.py` (LAYOUT에서 안 쓰는 키 3개 삭제)
- Modify: `tests/test_renderer_geometry.py` (삭제된 키 단언 제거)
- Test: `tests/test_preview_widget.py`

**Interfaces:**
- Consumes: Task 1의 Settings 필드, Task 2의 `TIME_GAP`
- Produces: `PreviewWidget.style_changed: Signal()` (Task 5의 메인 창이 연결), 위젯 속성 `title_y_spin/caption_y_spin: QSpinBox`, `title_color_btn/caption_color_btn: QPushButton`, 내부 메서드 `_set_title_color(color: str)`, `_set_caption_color(color: str)`, `_sync_style_controls()` (Task 4가 재사용)

- [ ] **Step 1: 실패하는 테스트 — `tests/test_preview_widget.py`에 추가**

```python
def test_style_controls_work_without_clip(qtbot):
    s = Settings()
    w = PreviewWidget(s, "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_title("문성FC 하이라이트")
    assert w.title_y_spin.isEnabled() and w.caption_y_spin.isEnabled()  # 클립 없어도 활성화
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w.title_y_spin.setValue(300)
    assert s.title_y == 300
    r = w.title_item.boundingRect()
    assert abs(w.title_item.pos().y() - (300 - r.height() / 2)) < 1.0  # 세로 중심 기준
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w._set_title_color("#ff0000")
    assert s.title_color == "#ff0000"
    assert w.title_item.brush().color().name() == "#ff0000"


def test_caption_style_moves_caption_and_time(qtbot, make_video, make_clip):
    s = Settings()
    w = PreviewWidget(s, "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_clip(make_clip(make_video(), t=758, caption="원더골"))
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w.caption_y_spin.setValue(1400)
    assert s.caption_y == 1400
    assert w.caption_item.pos().y() == 1400.0          # 상단 기준
    assert w.time_item.pos().y() == 1358.0             # 자막 위 42px
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w._set_caption_color("#00ff00")
    assert w.caption_item.brush().color().name() == "#00ff00"


def test_set_settings_syncs_style_controls(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_settings(Settings(title_y=111, caption_y=1222, title_color="#123456", caption_color="#654321"))
    assert (w.title_y_spin.value(), w.caption_y_spin.value()) == (111, 1222)
    assert w.title_item.brush().color().name() == "#123456"
    assert w.caption_item.brush().color().name() == "#654321"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py -v`
Expected: 새 테스트 3개 ERROR/FAIL (`AttributeError: ... has no attribute 'title_y_spin'`), 기존 테스트 PASS

- [ ] **Step 3: 구현 — `preview_widget.py`**

(a) import 변경:

```python
from PySide6.QtWidgets import (..., QColorDialog, ...)  # 기존 목록에 QColorDialog 추가
from stampcut.core.renderer import LAYOUT, TIME_GAP, ZOOM_MAX, ZOOM_MIN, SquareGeometry, compute_square_geometry
```

(b) 시그널 추가 (`clip_changed` 아래):

```python
    style_changed = Signal()  # 타이틀·자막 위치/색 변경 (settings에 직접 반영됨; 메인 창이 저장)
```

(c) 생성자에서 텍스트 아이템 색을 settings에서:

```python
        self.title_item = self._text_item(L["title_font"], self.settings.title_color)
        self.time_item = self._text_item(L["time_font"], L["time_color"])
        self.caption_item = self._text_item(L["caption_font"], self.settings.caption_color, border=L["caption_border"])
```

(d) 생성자에서 `self.caption_edit = ...` 아래에 컨트롤 4개 추가:

```python
        self.title_color_btn = self._color_button(self.settings.title_color, self._on_title_color)
        self.title_y_spin = self._y_spin(self.settings.title_y, self._on_title_y)
        self.caption_color_btn = self._color_button(self.settings.caption_color, self._on_caption_color)
        self.caption_y_spin = self._y_spin(self.settings.caption_y, self._on_caption_y)
```

(e) 생성자의 `controls.addRow("자막", self.caption_edit)` 줄을 다음으로 교체:

```python
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
```

주의: `_set_controls_enabled`의 위젯 목록에 새 컨트롤을 **넣지 않는다** — 스타일은 전역 값이라 클립 선택과 무관하게 항상 활성화.

(f) `_text_item` 아래에 헬퍼 추가:

```python
    def _y_spin(self, value: int, handler) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, LAYOUT["canvas_h"])
        spin.setKeyboardTracking(False)
        spin.setValue(value)
        spin.valueChanged.connect(handler)
        return spin

    def _color_button(self, color: str, handler) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(28, 22)
        btn.setToolTip("색상 변경")
        self._paint_color_button(btn, color)
        btn.clicked.connect(handler)
        return btn

    @staticmethod
    def _paint_color_button(btn: QPushButton, color: str) -> None:
        btn.setStyleSheet(f"background-color: {color}; border: 1px solid #888888;")
```

(g) `set_settings`를 다음으로 교체하고 `_sync_style_controls` 추가:

```python
    def set_settings(self, settings: Settings) -> None:
        self.settings = settings
        self._apply_background()
        self._sync_style_controls()
        self.relayout()

    def _sync_style_controls(self) -> None:
        for w in (self.title_y_spin, self.caption_y_spin):
            w.blockSignals(True)
        self.title_y_spin.setValue(self.settings.title_y)
        self.caption_y_spin.setValue(self.settings.caption_y)
        for w in (self.title_y_spin, self.caption_y_spin):
            w.blockSignals(False)
        self._paint_color_button(self.title_color_btn, self.settings.title_color)
        self._paint_color_button(self.caption_color_btn, self.settings.caption_color)
```

(h) `_place_text`를 top/center 이중 기준으로 교체:

```python
    def _place_text(self, item: QGraphicsSimpleTextItem, text: str, top: float | None = None, center: float | None = None) -> None:
        item.setText(text)
        r = item.boundingRect()
        y = center - r.height() / 2 if center is not None else float(top or 0)
        item.setPos((LAYOUT["canvas_w"] - r.width()) / 2, y)
```

(i) `relayout`에서 텍스트 배치 부분을 settings 기반으로 교체 (영상 배치 부분은 그대로):

```python
        s = self.settings
        title_y = min(max(s.title_y, 0), L["canvas_h"])
        caption_y = min(max(s.caption_y, 0), L["canvas_h"])
        self.title_item.setBrush(QBrush(QColor(s.title_color)))
        self.caption_item.setBrush(QBrush(QColor(s.caption_color)))
        self._place_text(self.title_item, wrap(self.title, L["title_font"], L["max_text_width"], L["max_lines"]), center=title_y)
        show_time = bool(clip) and s.show_time_in_caption
        self.time_item.setVisible(show_time)
        if clip:
            self._place_text(self.time_item, format_time(clip.t), top=caption_y - TIME_GAP)
            self._place_text(self.caption_item, wrap(clip.caption, L["caption_font"], L["max_text_width"], L["max_lines"]), top=caption_y)
        self.caption_item.setVisible(bool(clip))
```

(j) 편집 핸들러 섹션에 추가:

```python
    def _on_title_y(self, value: int) -> None:
        self.settings.title_y = value
        self.relayout()
        self.style_changed.emit()

    def _on_caption_y(self, value: int) -> None:
        self.settings.caption_y = value
        self.relayout()
        self.style_changed.emit()

    def _pick_color(self, current: str) -> str | None:
        c = QColorDialog.getColor(QColor(current), self, "색상 선택")
        return c.name() if c.isValid() else None

    def _on_title_color(self) -> None:
        c = self._pick_color(self.settings.title_color)
        if c is not None:
            self._set_title_color(c)

    def _on_caption_color(self) -> None:
        c = self._pick_color(self.settings.caption_color)
        if c is not None:
            self._set_caption_color(c)

    def _set_title_color(self, color: str) -> None:
        self.settings.title_color = color
        self._paint_color_button(self.title_color_btn, color)
        self.relayout()
        self.style_changed.emit()

    def _set_caption_color(self, color: str) -> None:
        self.settings.caption_color = color
        self._paint_color_button(self.caption_color_btn, color)
        self.relayout()
        self.style_changed.emit()
```

(k) `renderer.py`의 `LAYOUT`에서 `"title_band_h": 420,` / `"time_y": 1510,` / `"caption_y": 1552,` 세 줄 삭제.

(l) `tests/test_renderer_geometry.py` 7행이 삭제된 키를 단언하므로 다음으로 교체:

```python
    assert LAYOUT["caption_border"] == 4
```

(기본 배치값 1510/1552는 이제 `test_renderer_commands.py::test_basic_command`의 filter_complex 단언과 `test_settings.py`의 기본값 단언이 지킨다.)

확인:

Run: `grep -rn "title_band_h\|\"time_y\"\|\"caption_y\"" stampcut tests`
Expected: 매치 없음 (`Settings.caption_y` 같은 속성 접근만 남음)

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py tests/test_renderer_commands.py tests/test_renderer_geometry.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/preview_widget.py stampcut/core/renderer.py tests/test_preview_widget.py tests/test_renderer_geometry.py
git commit -m "feat(gui): title/caption y-position and color controls in preview panel"
```

---

### Task 4: 미리보기에서 텍스트 세로 드래그

**Files:**
- Modify: `stampcut/gui/preview_widget.py` (`_DragView`, `PreviewWidget` 연결부)
- Test: `tests/test_preview_widget.py`

**Interfaces:**
- Consumes: Task 3의 `style_changed`, `_sync_style_controls`, 텍스트 아이템들
- Produces: `_DragView(scene, on_drag, hit_test=None, on_text_drag=None, on_drag_end=None)` — `hit_test(scene_pos: QPointF) -> str | None` ("title"/"caption"/None), `on_text_drag(kind: str, dy: float)`, `on_drag_end()` (텍스트 드래그로 끝났을 때만 호출). `PreviewWidget._hit_text`, `_on_text_drag`, `_on_text_drag_end`

- [ ] **Step 1: 실패하는 테스트 — `tests/test_preview_widget.py`에 추가**

파일 상단 import에 추가:

```python
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QGraphicsScene
from stampcut.gui.preview_widget import _DragView
```

테스트:

```python
def _mouse(type_, pos):
    p = QPointF(pos)
    return QMouseEvent(type_, p, p, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_dragview_routes_text_drag_vs_video_pan(qtbot):
    scene = QGraphicsScene(0, 0, 100, 100)
    calls = []
    target = {"v": "title"}
    view = _DragView(
        scene,
        lambda dx, dy: calls.append(("pan", dx, dy)),
        hit_test=lambda pos: target["v"],
        on_text_drag=lambda kind, dy: calls.append((kind, dy)),
        on_drag_end=lambda: calls.append(("end",)),
    )
    qtbot.addWidget(view)
    view.mousePressEvent(_mouse(QEvent.MouseButtonPress, QPoint(10, 10)))
    view.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(10, 25)))
    view.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, QPoint(10, 25)))
    assert ("title", 15.0) in calls and ("end",) in calls
    assert not [c for c in calls if c[0] == "pan"]
    calls.clear()
    target["v"] = None  # 텍스트 밖 → 영상 팬
    view.mousePressEvent(_mouse(QEvent.MouseButtonPress, QPoint(10, 80)))
    view.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(15, 90)))
    view.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, QPoint(15, 90)))
    assert ("pan", 5.0, 10.0) in calls and ("end",) not in calls


def test_hit_text_finds_title_and_caption(qtbot, make_video, make_clip):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_title("문성FC 하이라이트")
    w.set_clip(make_clip(make_video(), t=758, caption="원더골"))
    assert w._hit_text(w.title_item.sceneBoundingRect().center()) == "title"
    assert w._hit_text(w.caption_item.sceneBoundingRect().center()) == "caption"
    assert w._hit_text(QPointF(540, 900)) is None  # 정방형 한가운데 = 영상 영역


def test_text_drag_updates_settings_and_emits_on_release(qtbot):
    s = Settings()
    w = PreviewWidget(s, "Malgun Gothic")
    qtbot.addWidget(w)
    w.set_title("문성FC 하이라이트")
    w._on_text_drag("title", 50.0)
    assert s.title_y == 260 and w.title_y_spin.value() == 260  # 스핀박스 동기화
    with qtbot.waitSignal(w.style_changed, timeout=1000):
        w._on_text_drag_end()
    w._on_text_drag("caption", -100000.0)
    assert s.caption_y == 0  # 클램프
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py -v`
Expected: 새 테스트 3개 FAIL/ERROR (`_DragView.__init__() got an unexpected keyword argument 'hit_test'` 등), 기존 테스트 PASS

- [ ] **Step 3: 구현 — `preview_widget.py`**

(a) `_DragView`를 다음으로 교체:

```python
class _DragView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, on_drag, hit_test=None, on_text_drag=None, on_drag_end=None) -> None:
        super().__init__(scene)
        self._on_drag = on_drag
        self._hit_test = hit_test or (lambda pos: None)
        self._on_text_drag = on_text_drag or (lambda kind, dy: None)
        self._on_drag_end = on_drag_end or (lambda: None)
        self._last: QPointF | None = None
        self._target: str | None = None
        self.setBackgroundBrush(QColor("#202020"))
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCursor(Qt.OpenHandCursor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._last = self.mapToScene(event.position().toPoint())
            self._target = self._hit_test(self._last)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._last is not None:
            p = self.mapToScene(event.position().toPoint())
            d = p - self._last
            self._last = p
            if self._target:
                self._on_text_drag(self._target, d.y())
            else:
                self._on_drag(d.x(), d.y())

    def mouseReleaseEvent(self, event) -> None:
        dragged = self._target
        self._last = None
        self._target = None
        self.setCursor(Qt.OpenHandCursor)
        if dragged:
            self._on_drag_end()
```

(b) 생성자의 `self.view = _DragView(self.scene, self._on_drag)`를 다음으로 교체:

```python
        self.view = _DragView(self.scene, self._on_drag, self._hit_text, self._on_text_drag, self._on_text_drag_end)
```

(c) 편집 핸들러 섹션에 추가:

```python
    def _hit_text(self, pos: QPointF) -> str | None:
        if self.title_item.isVisible() and self.title_item.sceneBoundingRect().contains(pos):
            return "title"
        if self.caption_item.isVisible() and self.caption_item.sceneBoundingRect().contains(pos):
            return "caption"
        return None

    def _on_text_drag(self, kind: str, dy: float) -> None:
        L = LAYOUT
        if kind == "title":
            self.settings.title_y = int(round(min(max(self.settings.title_y + dy, 0), L["canvas_h"])))
        else:
            self.settings.caption_y = int(round(min(max(self.settings.caption_y + dy, 0), L["canvas_h"])))
        self._sync_style_controls()
        self.relayout()

    def _on_text_drag_end(self) -> None:
        self.style_changed.emit()
```

주의: 드래그 중에는 `style_changed`를 내지 않는다 (릴리스 때 한 번). 스핀박스 동기화는 `_sync_style_controls`가 blockSignals로 재귀를 막는다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_widget.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/preview_widget.py tests/test_preview_widget.py
git commit -m "feat(gui): drag title/caption vertically in the preview"
```

---

### Task 5: 메인 창 연결 — 스타일 변경을 설정 파일에 저장

**Files:**
- Modify: `stampcut/gui/main_window.py` (preview 생성부 ~102행, 설정 섹션 ~127행)
- Modify: `README.md` (사용법에 한 줄)
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: Task 3~4의 `PreviewWidget.style_changed`, 기존 `settings_mod.save`
- Produces: `MainWindow._on_style_changed()` — 공유 Settings 객체를 settings.json에 저장

- [ ] **Step 1: 실패하는 테스트 — `tests/test_main_window.py`에 추가**

```python
def test_style_change_saves_settings(qtbot, monkeypatch):
    saved = []
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: saved.append(s))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.preview.title_y_spin.setValue(300)
    assert saved and saved[-1] is w.settings and w.settings.title_y == 300
    saved.clear()
    w.preview._on_text_drag("caption", -30.0)
    assert not saved  # 드래그 중에는 저장하지 않음
    w.preview._on_text_drag_end()
    assert saved and w.settings.caption_y == 1522
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main_window.py::test_style_change_saves_settings -v`
Expected: FAIL — `assert saved` (style_changed가 아직 연결 안 됨)

- [ ] **Step 3: 구현 — `main_window.py`**

`self.preview.clip_changed.connect(self._on_clip_edited)` 줄 아래에:

```python
        self.preview.style_changed.connect(self._on_style_changed)
```

`# --- 설정 ---` 섹션의 `apply_settings` 아래에:

```python
    def _on_style_changed(self) -> None:
        # PreviewWidget이 공유 Settings 객체를 직접 고쳤으므로 저장만 하면 된다
        settings_mod.save(self.settings)
```

`README.md`의 사용법(미리보기 조작을 설명하는 부분)에 한 줄 추가:

```markdown
- 미리보기에서 타이틀·자막을 위아래로 드래그해 위치를 옮기고, 옆 패널의 Y 값·색상 버튼으로 세밀하게 조정할 수 있습니다 (모든 컷 공통, 설정에 저장됨).
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `.venv/Scripts/python.exe -m pytest -q -m "not network"`
Expected: 전부 PASS (기존 167개 + 새 테스트)

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/main_window.py tests/test_main_window.py README.md
git commit -m "feat(gui): persist title/caption style edits to settings"
```

---

## 완료 기준

- `pytest -q -m "not network"` 전부 통과
- 기본값에서 렌더 filter_complex가 기존과 동일 (`test_basic_command` 무수정 통과)
- 앱에서 손으로 확인: 미리보기에서 타이틀·자막 드래그 → 스핀박스 동기화, 색상 버튼 → 다이얼로그 → 즉시 반영, 앱 재시작 후 유지, 렌더 결과에 반영
