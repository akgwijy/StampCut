# 작업 자동 저장·복원 + 화살표 없는 초 편집 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 표 앞/뒤 편집 스핀박스의 화살표를 없애고, 편집 상태(URL·타이틀·컷별 편집값)를 자동 저장해 앱 재시작 시 자동 복원한다.

**Architecture:** Qt 무의존 `stampcut/core/project_io.py`가 Project↔JSON을 담당(원자적 쓰기, 오류 시 None). MainWindow는 `project_file: Path | None` 인자를 받아(None=비활성, 테스트 안전) QTimer 1.5초 디바운스로 자동 저장하고, 시작 시 load 성공하면 `_adopt_project(restored=True)`로 복원 — `_on_analyzed`와 같은 공통 경로를 공유. 실제 경로는 app.py에서만 주입. 스펙: `docs/superpowers/specs/2026-08-26-project-autosave-design.md`

**Tech Stack:** Python 3.12, PySide6, pytest + pytest-qt

## Global Constraints

- `project_io`는 Qt import 금지. `VERSION = 1`; load는 파일 없음/JSON 깨짐/버전 불일치/키 누락 등 어떤 오류든 조용히 `None` (예외 전파 금지).
- save는 원자적: 같은 폴더 `.tmp`에 쓰고 `os.replace`. 저장 후 `.tmp` 잔여물 없음.
- 복원 시 상태 재계산: `preview_path` 파일이 실존하면 `READY`, 아니면 `preview_path/preview_start/preview_end = None` + `PENDING`. `final_path`도 파일 없으면 None. `DOWNLOADING/ERROR`는 저장·복원하지 않음. `video_id`가 videos에 없는 클립은 스킵.
- `MainWindow(settings=None, project_file: Path | None = None)` — **None이면 자동 저장·복원 완전 비활성**. 기존 테스트는 무수정 통과해야 함 (기존 생성 호출은 전부 project_file 없음).
- 자동 저장 디바운스 1500ms singleShot; `_flush_autosave`는 `OSError`를 로그만 남기고 무시.
- 표 스핀박스: `QAbstractSpinBox.NoButtons`, 범위 0~120·접미사 "초" 유지.
- Test runs: `.venv/Scripts/python.exe -m pytest ...` from `e:\workspace\StampCut`.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: 표 앞/뒤 스핀박스 화살표 제거

**Files:**
- Modify: `stampcut/gui/clip_table.py` (import 6행, `SecondsDelegate.createEditor` 137~141행)
- Test: `tests/test_clip_table.py`

**Interfaces:**
- Consumes: 기존 `SecondsDelegate`
- Produces: 화살표 없는 편집기 (동작 변화는 이것뿐)

- [ ] **Step 1: 실패하는 테스트 — `tests/test_clip_table.py` 끝에 추가**

```python
def test_seconds_delegate_has_no_arrow_buttons(qtbot):
    from PySide6.QtWidgets import QAbstractSpinBox, QWidget

    from stampcut.gui.clip_table import SecondsDelegate

    parent = QWidget()
    qtbot.addWidget(parent)
    editor = SecondsDelegate().createEditor(parent, None, None)
    assert editor.buttonSymbols() == QAbstractSpinBox.NoButtons  # 화살표 없이 직접 타이핑
    assert (editor.minimum(), editor.maximum()) == (0, 120)
    assert editor.suffix() == "초"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_clip_table.py::test_seconds_delegate_has_no_arrow_buttons -v`
Expected: FAIL — `buttonSymbols()`가 기본값 `UpDownArrows`

- [ ] **Step 3: 구현 — `clip_table.py`**

import 줄에 `QAbstractSpinBox` 추가:

```python
from PySide6.QtWidgets import QAbstractSpinBox, QSpinBox, QStyledItemDelegate
```

`createEditor`에 한 줄 추가:

```python
    def createEditor(self, parent, option, index):
        sb = QSpinBox(parent)
        sb.setRange(0, 120)
        sb.setSuffix("초")
        sb.setButtonSymbols(QAbstractSpinBox.NoButtons)  # 칸이 좁아 화살표 대신 직접 타이핑
        return sb
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_clip_table.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/clip_table.py tests/test_clip_table.py
git commit -m "fix(gui): arrow-less direct typing for pre/post seconds in the table"
```

---

### Task 2: project_io 직렬화 모듈

**Files:**
- Create: `stampcut/core/project_io.py`
- Test: `tests/test_project_io.py` (신규)

**Interfaces:**
- Consumes: `stampcut.core.models`의 `Project, VideoInfo, Clip, Mention, ClipStatus`; `stampcut.core.settings.config_dir`
- Produces: `project_path() -> Path`, `save(project: Project, path: Path) -> None`, `load(path: Path) -> Project | None`, 상수 `VERSION = 1` — Task 3의 MainWindow·app.py가 사용

- [ ] **Step 1: 실패하는 테스트 — `tests/test_project_io.py` 신규 작성**

```python
import json

from stampcut.core import project_io
from stampcut.core.models import ClipStatus, Mention, Project


def _project(make_video, make_clip, tmp_path, with_preview=False):
    v = make_video()
    clip = make_clip(v, 758, caption="원더골")
    clip.pre, clip.post = 5, 20
    clip.enabled = False
    clip.zoom, clip.pan_x, clip.pan_y = 1.5, 0.2, 0.8
    clip.mentions = [Mention(v.video_id, 758, "원더골", "cid", "작성자", 3, False)]
    if with_preview:
        p = tmp_path / "preview.mp4"
        p.write_bytes(b"x")
        clip.preview_path, clip.preview_start, clip.preview_end = p, 700, 800
        clip.status = ClipStatus.READY
    return Project([v.url], "26.08.14 하이라이트", [v], [clip])


def test_roundtrip_preserves_edits(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path, with_preview=True)
    project_io.save(project, pf)
    assert not pf.with_suffix(".tmp").exists()  # 원자적 쓰기 잔여물 없음
    loaded = project_io.load(pf)
    assert loaded is not None
    assert loaded.urls == project.urls and loaded.title == project.title
    assert loaded.videos[0] == project.videos[0]
    c0, c1 = project.clips[0], loaded.clips[0]
    assert (c1.id, c1.t, c1.caption, c1.pre, c1.post, c1.enabled) == (c0.id, 758, "원더골", 5, 20, False)
    assert (c1.zoom, c1.pan_x, c1.pan_y) == (1.5, 0.2, 0.8)
    assert c1.mentions == c0.mentions
    assert c1.status is ClipStatus.READY
    assert (c1.preview_path, c1.preview_start, c1.preview_end) == (c0.preview_path, 700, 800)


def test_missing_preview_file_resets_to_pending(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path, with_preview=True)
    project.clips[0].preview_path = tmp_path / "gone.mp4"  # 캐시가 지워진 상황
    project_io.save(project, pf)
    c = project_io.load(pf).clips[0]
    assert c.status is ClipStatus.PENDING
    assert c.preview_path is None and c.preview_start is None and c.preview_end is None


def test_bad_files_return_none(tmp_path):
    assert project_io.load(tmp_path / "none.json") is None
    p = tmp_path / "p.json"
    p.write_text("{broken", "utf-8")
    assert project_io.load(p) is None
    p.write_text(json.dumps({"version": 999, "urls": [], "title": "", "videos": [], "clips": []}), "utf-8")
    assert project_io.load(p) is None
    p.write_text(json.dumps({"version": 1}), "utf-8")  # 필수 키 누락
    assert project_io.load(p) is None


def test_unknown_video_id_clip_skipped(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["clips"][0]["video_id"] = "UNKNOWN"
    pf.write_text(json.dumps(data), "utf-8")
    loaded = project_io.load(pf)
    assert loaded is not None and loaded.clips == []


def test_project_path_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert project_io.project_path() == tmp_path / "StampCut" / "project.json"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_project_io.py -v`
Expected: 전부 ERROR — `ModuleNotFoundError: No module named 'stampcut.core.project_io'`

- [ ] **Step 3: 구현 — `stampcut/core/project_io.py` 신규 작성**

```python
"""프로젝트(작업 상태) JSON 저장·복원. Qt 의존 없음."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from stampcut.core import settings as settings_mod
from stampcut.core.models import Clip, ClipStatus, Mention, Project, VideoInfo

VERSION = 1


def project_path() -> Path:
    return settings_mod.config_dir() / "project.json"


def _clip_dict(c: Clip) -> dict:
    return {
        "id": c.id,
        "video_id": c.video.video_id,
        "t": c.t,
        "score": c.score,
        "caption": c.caption,
        "pre": c.pre,
        "post": c.post,
        "enabled": c.enabled,
        "over_limit": c.over_limit,
        "zoom": c.zoom,
        "pan_x": c.pan_x,
        "pan_y": c.pan_y,
        "preview_path": str(c.preview_path) if c.preview_path else None,
        "preview_start": c.preview_start,
        "preview_end": c.preview_end,
        "final_path": str(c.final_path) if c.final_path else None,
        "mentions": [asdict(m) for m in c.mentions],
    }


def save(project: Project, path: Path) -> None:
    data = {
        "version": VERSION,
        "urls": project.urls,
        "title": project.title,
        "videos": [{**asdict(v), "published_at": v.published_at.isoformat()} for v in project.videos],
        "clips": [_clip_dict(c) for c in project.clips],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def _load_clip(d: dict, by_id: dict[str, VideoInfo]) -> Clip | None:
    video = by_id.get(d["video_id"])
    if video is None:
        return None
    preview_path = Path(d["preview_path"]) if d.get("preview_path") else None
    preview_start, preview_end = d.get("preview_start"), d.get("preview_end")
    if preview_path is not None and preview_path.exists():
        status = ClipStatus.READY
    else:
        status = ClipStatus.PENDING
        preview_path = None
        preview_start = preview_end = None
    final_path = Path(d["final_path"]) if d.get("final_path") else None
    if final_path is not None and not final_path.exists():
        final_path = None
    return Clip(
        video=video,
        t=int(d["t"]),
        mentions=[Mention(**m) for m in d.get("mentions", [])],
        score=float(d["score"]),
        caption=str(d["caption"]),
        id=str(d["id"]),
        pre=d.get("pre"),
        post=d.get("post"),
        enabled=bool(d.get("enabled", True)),
        over_limit=bool(d.get("over_limit", False)),
        zoom=float(d.get("zoom", 1.0)),
        pan_x=float(d.get("pan_x", 0.5)),
        pan_y=float(d.get("pan_y", 0.5)),
        status=status,
        preview_path=preview_path,
        preview_start=preview_start,
        preview_end=preview_end,
        final_path=final_path,
    )


def load(path: Path) -> Project | None:
    """저장된 작업을 되살린다. 어떤 오류든 None — 새 작업으로 시작하게 한다."""
    try:
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict) or data.get("version") != VERSION:
            return None
        videos = []
        for raw in data["videos"]:
            v = dict(raw)
            v["published_at"] = datetime.fromisoformat(v["published_at"])
            videos.append(VideoInfo(**v))
        by_id = {v.video_id: v for v in videos}
        clips = [c for raw in data["clips"] if (c := _load_clip(raw, by_id)) is not None]
        return Project(urls=list(data["urls"]), title=str(data["title"]), videos=videos, clips=clips)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_project_io.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/core/project_io.py tests/test_project_io.py
git commit -m "feat(core): project JSON save/load with atomic write and cache-aware restore"
```

---

### Task 3: MainWindow 자동 저장·복원 + app.py 주입 + README

**Files:**
- Modify: `stampcut/gui/main_window.py`
- Modify: `stampcut/app.py` (MainWindow 생성부)
- Modify: `README.md`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: Task 2의 `project_io.save/load/project_path`
- Produces: `MainWindow(settings=None, project_file: Path | None = None)`, `_adopt_project(project, restored=False)`, `_schedule_autosave()`, `_flush_autosave()`

- [ ] **Step 1: 실패하는 테스트 — `tests/test_main_window.py`에 추가**

```python
def test_autosave_and_restore_roundtrip(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)  # 실제 다운로드 금지
    pf = tmp_path / "project.json"
    v = make_video()
    clip = make_clip(v, 758, caption="원더골")
    project = Project([v.url], "제목", [v], [clip])

    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    assert w.project is None and not pf.exists()  # 저장 파일이 없으면 새 작업
    w.project = project
    w.model.set_clips(project.clips)
    clip.caption = "수정된 자막"
    clip.pre = 7
    clip.zoom = 1.5
    w._on_clip_edited(clip)  # 편집 → 자동 저장 예약(디바운스)
    assert w._autosave_timer.isActive()
    w._flush_autosave()  # 테스트에서는 즉시 저장
    assert pf.exists()

    w2 = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w2)
    assert w2.project is not None and w2.model.rowCount() == 1
    restored = w2.model.clip_at(0)
    assert (restored.caption, restored.pre, restored.zoom) == ("수정된 자막", 7, 1.5)
    assert w2.url_panel.title() == "제목"
    assert w2.url_panel.urls() == [v.url]
    assert "불러왔" in w2.status_panel.message.text() or "ffmpeg" in w2.status_panel.message.text()


def test_no_project_file_disables_autosave(qtbot, make_video, make_clip):
    v = make_video()
    clip = make_clip(v, 758)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.project = Project([v.url], "제목", [v], [clip])
    w._on_clip_edited(clip)
    assert not w._autosave_timer.isActive()  # project_file 없음 → 예약 자체를 안 함
    w._flush_autosave()  # 예외 없이 무시


def test_close_event_flushes_autosave(qtbot, tmp_path, make_video, make_clip):
    pf = tmp_path / "project.json"
    v = make_video()
    clip = make_clip(v, 758)
    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    w.project = Project([v.url], "제목", [v], [clip])
    w._schedule_autosave()  # 디바운스 대기 중 종료
    w.close()
    assert pf.exists()
```

참고: `status_panel.message`는 StatusPanel의 기존 QLabel 속성이다 (status_bar.py).

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main_window.py -v -k "autosave or restore_roundtrip or flushes"`
Expected: 새 테스트 3개 FAIL/ERROR (`__init__() got an unexpected keyword argument 'project_file'` 등), 기존 테스트 PASS

- [ ] **Step 3: 구현 — `main_window.py`**

(a) import: QtCore 줄에 `QTimer` 추가, core import에 `from stampcut.core import project_io` 추가:

```python
from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, Signal
from stampcut.core import project_io
```

(b) 생성자 시그니처와 자동 저장 타이머 (`self._bridge.updated.connect(...)` 아래):

```python
    def __init__(self, settings: Settings | None = None, project_file: Path | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else settings_mod.load()
        self.project_file = project_file
```

```python
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1500)
        self._autosave_timer.timeout.connect(self._flush_autosave)
```

(c) `__init__` 맨 끝 (`self.setCentralWidget(central)` 다음):

```python
        if self.project_file is not None:
            restored = project_io.load(self.project_file)
            if restored is not None:
                self._adopt_project(restored, restored=True)
```

(d) `_on_analyzed`를 공통 경로로 축소하고 `_adopt_project` 신설 (`# --- 분석 ---` 섹션):

```python
    def _on_analyzed(self, project: Project) -> None:
        self._adopt_project(project)
        self._set_busy(False)
        if not project.clips:
            self.status_panel.set_idle("타임스탬프가 적힌 댓글이 없습니다")
            self._info("타임스탬프가 적힌 댓글이 없습니다.")
        elif self.ffpaths is None:
            self.status_panel.set_idle(FFMPEG_MISSING)
        if project.warnings:
            self._info("\n".join(project.warnings))
        self.analysis_done.emit()

    def _adopt_project(self, project: Project, restored: bool = False) -> None:
        """분석 결과 채택과 저장 파일 복원이 공유하는 공통 경로."""
        self.project = project
        if restored:
            self.url_panel.urls_edit.setPlainText("\n".join(project.urls))
        self.url_panel.set_title(project.title)
        self.model.set_clips(project.clips)
        self.preview.set_title(project.title)
        self.status_panel.update_summary(project, self.settings)
        if project.clips:
            self.table.selectRow(0)
            if self.ffpaths is not None:
                self.start_previews(project.clips)
        if restored:
            self.status_panel.set_idle(FFMPEG_MISSING if self.ffpaths is None else "이전 작업을 불러왔습니다 — 이어서 편집하세요")
        self._schedule_autosave()
```

(e) 자동 저장 메서드 (`_set_busy` 아래):

```python
    def _schedule_autosave(self) -> None:
        if self.project_file is not None and self.project is not None:
            self._autosave_timer.start()  # 재시작 = 디바운스

    def _flush_autosave(self) -> None:
        self._autosave_timer.stop()
        if self.project_file is None or self.project is None:
            return
        try:
            project_io.save(self.project, self.project_file)
        except OSError:
            log.exception("작업 자동 저장 실패")
```

(f) 편집 지점마다 예약 — 다음 메서드들의 **맨 끝**에 `self._schedule_autosave()` 한 줄씩 추가: `_on_clip_edited`, `_on_table_changed`, `_on_title_changed`, `_on_clip_updated`, `_on_rendered`.

(g) `closeEvent`: `self.preview.shutdown()` 바로 앞에 추가:

```python
        self._flush_autosave()
```

(h) `stampcut/app.py`: import에 `from stampcut.core import project_io` 추가(기존 settings_mod import 옆), `win = MainWindow()` 를 다음으로 교체:

```python
    win = MainWindow(project_file=project_io.project_path())
```

(i) `README.md` 사용법에 한 줄 추가:

```markdown
- 편집 내용(URL·타이틀·컷별 자막/앞뒤/줌/위치)은 자동으로 저장되며, 앱을 다시 켜면 마지막 작업이 그대로 복원됩니다. 새로 "댓글 분석"을 돌리면 새 작업으로 바뀝니다.
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `.venv/Scripts/python.exe -m pytest -q -m "not network"`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```powershell
git add stampcut/gui/main_window.py stampcut/app.py tests/test_main_window.py README.md
git commit -m "feat(gui): autosave project state and restore last session on startup"
```

---

## 완료 기준

- `pytest -q -m "not network"` 전부 통과
- 앱에서 손으로 확인: 앞/뒤 더블클릭 → 화살표 없는 입력칸; 컷 편집 후 앱 재시작 → URL·타이틀·편집값 복원, 캐시된 미리보기 즉시 재생, 없는 것만 재다운로드
