# BGM 믹싱 + 전체 미리보기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 출력 영상에 배경 음악(폴더/파일 선택, 원본·BGM 볼륨, 음원 시작점, 영상 내 구간)을 concat 뒤 한 번에 믹스하고, 540x960 전체 미리보기 위에 BGM을 실시간 동기 재생해 확인할 수 있게 한다.

**Architecture:** core(Qt 없음)에 `AudioMix` 모델, `RenderProfile`(저해상도 프로필), `build_mix_command`, `bgm_position`(미리보기·렌더 공통 규칙), `render_preview`/`preview_signature`를 추가한다. GUI는 새 `BgmPanel`과 `PreviewWidget`의 "전체" 모드(두 번째 `QMediaPlayer`로 BGM 동기)를 추가하고 `MainWindow`가 이를 연결한다. 기존 클립 렌더·concat 경로는 기본 인자로 호출하면 지금과 완전히 같은 명령을 만든다.

**Tech Stack:** Python 3.12, PySide6 (QtMultimedia `QMediaPlayer`/`QAudioOutput`), ffmpeg (`amix`, `-stream_loop`, `atrim`, `adelay`, `afade`), pytest + pytest-qt.

스펙: `docs/superpowers/specs/2026-08-28-bgm-and-full-preview-design.md`

## Global Constraints

- `stampcut/core`는 Qt를 import하지 않는다. GUI는 `stampcut/gui`에만.
- 볼륨은 선형 진폭 배율 0.0~1.0. 슬라이더 30 → `0.3` → ffmpeg `volume=0.300`, Qt `setVolume(0.3)`.
- ffmpeg 필터 값 소수는 `f"{x:.3f}"` (테스트가 문자열을 단언한다). 페이드는 BGM 시작 1초 / 끝 2초 고정.
- BGM 반복 규칙: 첫 재생은 `bgm_offset`부터 곡 끝까지, 이후 곡 처음부터 반복. `bgm_position`과 `build_mix_command`가 같은 규칙.
- `project.json` `VERSION = 2`, v1 파일도 읽는다(audio 기본값). `audio`가 깨져도 프로젝트는 살린다.
- `build_clip_command`를 기본 인자로 부르면 기존 명령과 바이트 단위로 같아야 한다 (기존 `test_renderer_commands.py` 전부 통과).
- 전체 미리보기 파일은 `data_dir()/preview/full_<uuid>.mp4`; 이전 파일 삭제 실패(`OSError`)는 무시.
- 테스트는 실제 ffmpeg/네트워크를 쓰지 않는다 (`network` 마커 제외). GUI 테스트에서 가짜 미디어 파일을 실제로 열지 않도록 `player.setSource`/`play`를 monkeypatch한다.
- 테스트 실행: `pytest -q` (전체), 네트워크 통합: `pytest -m network -v`.
- 커밋 메시지는 기존 관례(`feat(core): …`, `feat(gui): …`, `test(core): …`, `docs: …`)를 따른다.

---

## File Structure

| 파일 | 역할 | 작업 |
|---|---|---|
| `stampcut/core/models.py` | `AudioMix` 추가, `Project.audio`, `Settings.bgm_dir` | Task 1 |
| `stampcut/core/project_io.py` | v2 저장, v1/v2 로드, audio 손상 방어 | Task 1 |
| `stampcut/core/bgm_sync.py` (신규) | `bgm_position` 순수 함수 | Task 2 |
| `stampcut/core/renderer.py` | `RenderProfile`, `build_clip_command(profile, source, in_offset)`, `build_mix_command` | Task 3, 4 |
| `stampcut/core/settings.py` | `preview_dir()` | Task 6 |
| `stampcut/core/pipeline.py` | `render`에 BGM 검증·믹스 단계, `render_preview`, `preview_signature` | Task 5, 6 |
| `stampcut/gui/status_bar.py` | `STAGE_WEIGHTS`에 `mix` | Task 5 |
| `stampcut/gui/bgm_panel.py` (신규) | `BgmPanel` 위젯 + BGM 단독 듣기 | Task 7 |
| `stampcut/gui/preview_widget.py` | "클립/전체" 모드, `full_item`, `bgm_player` 동기, 최신성 표시 | Task 8 |
| `stampcut/gui/main_window.py` | 패널 배치·시그널 연결·전체 미리보기 워커·렌더 전 BGM 검증 | Task 9 |
| `README.md`, `tests/test_integration_network.py` | 사용법 문서, 실제 ffmpeg 믹스 통합 테스트 | Task 10 |

---

### Task 1: AudioMix 모델 + 설정 + 프로젝트 저장 v2

**Files:**
- Modify: `stampcut/core/models.py` (Settings 끝, Project)
- Modify: `stampcut/core/project_io.py`
- Test: `tests/test_models.py`, `tests/test_settings.py`, `tests/test_project_io.py`

**Interfaces:**
- Produces: `AudioMix(original_volume=1.0, bgm_path="", bgm_volume=0.3, bgm_offset=0.0, bgm_start=0.0, bgm_end=None)` with `has_bgm() -> bool`, `is_default() -> bool`; `Project.audio: AudioMix`; `Settings.bgm_dir: str`; `project_io.VERSION == 2`, `project_io.SUPPORTED_VERSIONS == (1, 2)`.

- [ ] **Step 1: 실패하는 테스트 작성 (models)**

`tests/test_models.py` 끝에 추가:

```python
def test_audio_mix_flags():
    from stampcut.core.models import AudioMix

    assert AudioMix().is_default() and not AudioMix().has_bgm()
    assert not AudioMix(original_volume=0.5).is_default()
    a = AudioMix(bgm_path="x.mp3")
    assert a.has_bgm() and not a.is_default()
    assert Project([], "t", [], []).audio == AudioMix()
```

- [ ] **Step 2: 실패하는 테스트 작성 (settings)**

`tests/test_settings.py` 끝에 추가:

```python
def test_bgm_dir_roundtrip_and_legacy_default(tmp_path):
    p = tmp_path / "s.json"
    sm.save(Settings(bgm_dir=r"D:\music"), p)
    assert sm.load(p).bgm_dir == r"D:\music"
    p.write_text(json.dumps({"api_key": "K"}), "utf-8")
    assert sm.load(p).bgm_dir == ""
```

- [ ] **Step 3: 실패하는 테스트 작성 (project_io)**

`tests/test_project_io.py` import를 다음으로 바꾸고:

```python
from stampcut.core.models import AudioMix, ClipStatus, Mention, Project
```

파일 끝에 추가:

```python
def test_audio_roundtrip_v2(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path)
    project.audio = AudioMix(original_volume=0.5, bgm_path=str(tmp_path / "song.mp3"), bgm_volume=0.2, bgm_offset=30.0, bgm_start=5.0, bgm_end=60.0)
    project_io.save(project, pf)
    assert json.loads(pf.read_text("utf-8"))["version"] == 2
    assert project_io.load(pf).audio == project.audio


def test_v1_file_loads_with_default_audio(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["version"] = 1
    del data["audio"]
    pf.write_text(json.dumps(data), "utf-8")
    loaded = project_io.load(pf)
    assert loaded is not None and loaded.audio == AudioMix() and len(loaded.clips) == 1


def test_broken_audio_falls_back_to_default(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["audio"] = {"bgm_volume": "loud"}
    pf.write_text(json.dumps(data), "utf-8")
    loaded = project_io.load(pf)
    assert loaded is not None and loaded.audio == AudioMix() and len(loaded.clips) == 1
    data["audio"] = "nope"
    pf.write_text(json.dumps(data), "utf-8")
    assert project_io.load(pf).audio == AudioMix()


def test_missing_bgm_file_path_is_kept(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path)
    project.audio.bgm_path = str(tmp_path / "gone.mp3")
    project_io.save(project, pf)
    assert project_io.load(pf).audio.bgm_path == str(tmp_path / "gone.mp3")
```

- [ ] **Step 4: 실패 확인**

Run: `pytest tests/test_models.py tests/test_settings.py tests/test_project_io.py -q`
Expected: FAIL — `ImportError: cannot import name 'AudioMix'`, `TypeError: Settings.__init__() got an unexpected keyword argument 'bgm_dir'`

- [ ] **Step 5: models.py 구현**

`Settings` 끝(`caption_color` 다음)에 필드 추가:

```python
    caption_color: str = "#FFFFFF"
    bgm_dir: str = ""  # 마지막으로 고른 배경 음악 폴더
```

`RawComment` 앞(VideoInfo 다음)에 클래스 추가:

```python
@dataclass
class AudioMix:
    """출력 오디오 믹스 설정. 볼륨은 선형 진폭 배율 0.0~1.0."""

    original_volume: float = 1.0
    bgm_path: str = ""  # 절대 경로. "" = BGM 없음
    bgm_volume: float = 0.3
    bgm_offset: float = 0.0  # 음원 시작점(초)
    bgm_start: float = 0.0  # 영상 내 시작(초)
    bgm_end: float | None = None  # 영상 내 끝(초). None = 영상 끝까지

    def has_bgm(self) -> bool:
        return bool(self.bgm_path)

    def is_default(self) -> bool:
        """믹스 단계를 건너뛰어도 되는 상태 (BGM 없고 원본 100%)."""
        return not self.has_bgm() and self.original_volume == 1.0
```

`Project`에 필드 추가 (`warnings` 다음):

```python
    warnings: list[str] = field(default_factory=list)
    audio: AudioMix = field(default_factory=AudioMix)
```

- [ ] **Step 6: project_io.py 구현**

import와 버전 상수를 바꾸고:

```python
from stampcut.core.models import AudioMix, Clip, ClipStatus, Mention, Project, VideoInfo

VERSION = 2
SUPPORTED_VERSIONS = (1, 2)  # v1: audio 없음 → 기본값
```

`_load_clip` 앞에 추가:

```python
def _audio_from(d) -> AudioMix:
    """audio 항목이 없거나 깨졌으면 기본값 — 프로젝트 전체를 버리지 않는다."""
    if not isinstance(d, dict):
        return AudioMix()
    try:
        end = d.get("bgm_end")
        return AudioMix(
            original_volume=float(d.get("original_volume", 1.0)),
            bgm_path=str(d.get("bgm_path") or ""),
            bgm_volume=float(d.get("bgm_volume", 0.3)),
            bgm_offset=float(d.get("bgm_offset", 0.0)),
            bgm_start=float(d.get("bgm_start", 0.0)),
            bgm_end=float(end) if end is not None else None,
        )
    except (TypeError, ValueError):
        return AudioMix()
```

`save`의 `data` dict에 항목 추가:

```python
        "clips": [_clip_dict(c) for c in project.clips],
        "audio": asdict(project.audio),
```

`load`의 버전 검사와 반환을 바꾼다:

```python
        if not isinstance(data, dict) or data.get("version") not in SUPPORTED_VERSIONS:
            return None
        ...
        return Project(urls=list(data["urls"]), title=str(data["title"]), videos=videos, clips=clips, audio=_audio_from(data.get("audio")))
```

- [ ] **Step 7: 통과 확인**

Run: `pytest tests/test_models.py tests/test_settings.py tests/test_project_io.py -q`
Expected: 모두 PASS (기존 `test_bad_files_return_none`의 `version: 999` → None 유지)

- [ ] **Step 8: 커밋**

```bash
git add stampcut/core/models.py stampcut/core/project_io.py tests/test_models.py tests/test_settings.py tests/test_project_io.py
git commit -m "feat(core): AudioMix model, Settings.bgm_dir, project.json v2 with v1 fallback"
```

---

### Task 2: bgm_position (미리보기·렌더 공통 규칙)

**Files:**
- Create: `stampcut/core/bgm_sync.py`
- Test: `tests/test_bgm_sync.py`

**Interfaces:**
- Consumes: `AudioMix` (Task 1)
- Produces: `bgm_position(t: float, audio: AudioMix, bgm_duration: float, total: float) -> float | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_bgm_sync.py`:

```python
from stampcut.core.bgm_sync import bgm_position
from stampcut.core.models import AudioMix


def mix(**kw):
    base = dict(bgm_path="song.mp3", bgm_offset=30.0, bgm_start=10.0, bgm_end=100.0)
    base.update(kw)
    return AudioMix(**base)


def test_outside_section_is_none():
    assert bgm_position(9.9, mix(), 120.0, 200.0) is None
    assert bgm_position(100.0, mix(), 120.0, 200.0) is None  # 끝은 포함하지 않음


def test_inside_first_pass_adds_offset():
    assert bgm_position(10.0, mix(), 120.0, 200.0) == 30.0
    assert bgm_position(50.0, mix(), 120.0, 200.0) == 70.0


def test_wraps_to_song_start_after_first_pass():
    # 곡 120초, offset 30 → 첫 재생은 90초 분량. 그 뒤엔 곡 처음부터.
    m = mix(bgm_end=None)
    assert bgm_position(100.0, m, 120.0, 400.0) == 0.0  # e = 90
    assert bgm_position(105.0, m, 120.0, 400.0) == 5.0
    assert bgm_position(10.0 + 90 + 120 + 7, m, 120.0, 400.0) == 7.0  # 두 번째 반복


def test_end_none_uses_total():
    assert bgm_position(150.0, mix(bgm_end=None), 120.0, 160.0) is not None
    assert bgm_position(160.0, mix(bgm_end=None), 120.0, 160.0) is None


def test_offset_beyond_duration_is_normalized():
    assert bgm_position(10.0, mix(bgm_offset=125.0), 120.0, 200.0) == 5.0


def test_no_bgm_or_zero_duration():
    assert bgm_position(20.0, AudioMix(), 120.0, 200.0) is None
    assert bgm_position(20.0, mix(), 0.0, 200.0) is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_bgm_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stampcut.core.bgm_sync'`

- [ ] **Step 3: 구현**

`stampcut/core/bgm_sync.py`:

```python
"""BGM 재생 위치 계산. 미리보기(Qt)와 렌더(ffmpeg)가 같은 규칙을 쓴다. Qt 의존 없음."""
from __future__ import annotations

from stampcut.core.models import AudioMix


def bgm_position(t: float, audio: AudioMix, bgm_duration: float, total: float) -> float | None:
    """영상 시각 t(초)에 들려야 할 음원 위치(초). 구간 밖·BGM 없음·길이 0이면 None.

    첫 재생은 bgm_offset부터 곡 끝까지, 이후엔 곡 처음부터 반복 (renderer.build_mix_command와 동일).
    """
    if not audio.has_bgm() or bgm_duration <= 0:
        return None
    end = audio.bgm_end if audio.bgm_end is not None else total
    if t < audio.bgm_start or t >= end:
        return None
    e = t - audio.bgm_start
    offset = audio.bgm_offset % bgm_duration
    first = bgm_duration - offset
    if e < first:
        return offset + e
    return (e - first) % bgm_duration
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_bgm_sync.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add stampcut/core/bgm_sync.py tests/test_bgm_sync.py
git commit -m "feat(core): bgm_position — shared BGM timeline rule for preview and render"
```

---

### Task 3: RenderProfile + build_clip_command(profile, source, in_offset)

**Files:**
- Modify: `stampcut/core/renderer.py:115-170` (`build_clip_command`)
- Test: `tests/test_renderer_commands.py`

**Interfaces:**
- Produces: `RenderProfile(scale=1.0, preset="medium", crf=18)`, `FINAL_PROFILE`, `PREVIEW_PROFILE = RenderProfile(0.5, "ultrafast", 28)`; `build_clip_command(paths, clip, settings, title, probe, workdir, index, font_path, profile=FINAL_PROFILE, source: Path | None = None, in_offset: float = 0.0)`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_renderer_commands.py` import에 `PREVIEW_PROFILE` 추가:

```python
from stampcut.core.renderer import (
    PREVIEW_PROFILE,
    build_clip_command,
    ...
)
```

파일 끝에 추가:

```python
def test_preview_profile_scales_layout_and_speeds_encode(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(), t=758, caption="원더골")
    clip.preview_path = tmp_path / "preview.mp4"
    cmd, out = build_clip_command(
        paths(tmp_path), clip, Settings(), "제목", probe(), tmp_path, 3, tmp_path / "font.otf",
        profile=PREVIEW_PROFILE, source=clip.preview_path, in_offset=7,
    )
    fc = filter_complex(cmd)
    assert cmd[cmd.index("-ss") + 1] == "7" and cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == str(clip.preview_path)
    assert out == tmp_path / "clip_003.mp4"
    assert "crop=540:540" in fc and "s=540x960" in fc and "overlay=0:210" in fc
    assert "fontsize=32" in fc and "fontsize=18" in fc and "fontsize=30" in fc
    assert "y=105-text_h/2" in fc and "y=755" in fc and "y=776" in fc and "borderw=2" in fc
    assert cmd[cmd.index("-preset") + 1] == "ultrafast" and cmd[cmd.index("-crf") + 1] == "28"
    assert (tmp_path / "clip_003_caption.txt").read_text("utf-8") == "원더골"


def test_default_profile_has_no_seek_and_uses_final_path(tmp_path, make_video, make_clip):
    cmd, _ = build(tmp_path, make_clip(make_video(), t=758))
    assert "-ss" not in cmd and cmd[cmd.index("-i") + 1] == str(tmp_path / "final.mp4")
    assert cmd[cmd.index("-preset") + 1] == "medium"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_renderer_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'PREVIEW_PROFILE'`

- [ ] **Step 3: 구현**

`renderer.py`의 `TIME_GAP = 42` 아래에 추가:

```python
@dataclass(frozen=True)
class RenderProfile:
    scale: float = 1.0  # 캔버스·정방형·폰트·Y좌표·간격 배율
    preset: str = "medium"
    crf: int = 18


FINAL_PROFILE = RenderProfile()
PREVIEW_PROFILE = RenderProfile(scale=0.5, preset="ultrafast", crf=28)  # 540x960 전체 미리보기용
```

`build_clip_command` 전체를 다음으로 교체:

```python
def build_clip_command(
    paths: FfmpegPaths,
    clip: Clip,
    settings: Settings,
    title: str,
    probe: ProbeInfo,
    workdir: Path,
    index: int,
    font_path: Path,
    profile: RenderProfile = FINAL_PROFILE,
    source: Path | None = None,
    in_offset: float = 0.0,
) -> tuple[list[str], Path]:
    """클립 하나를 profile.scale 배율의 9:16 중간 파일로 렌더하는 ffmpeg 명령과 출력 경로.

    source가 None이면 clip.final_path. in_offset > 0이면 입력을 그만큼 건너뛴다 (여유분이 있는 미리보기 구간 파일용).
    기본 인자로 부르면 최종 렌더(1080x1920, medium, crf 18) 명령과 완전히 같다.
    """
    L = LAYOUT
    k = profile.scale
    S, W, H, fps = _even(L["square"] * k), _even(L["canvas_w"] * k), _even(L["canvas_h"] * k), L["fps"]
    square_y = _even(L["square_y"] * k)
    title_font, time_font, caption_font = round(L["title_font"] * k), round(L["time_font"] * k), round(L["caption_font"] * k)
    g = compute_square_geometry(probe.width, probe.height, clip.zoom, clip.pan_x, clip.pan_y, S)
    title_y = round(_clamp(settings.title_y, 0, L["canvas_h"]) * k)
    caption_y = round(_clamp(settings.caption_y, 0, L["canvas_h"]) * k)
    time_gap = round(TIME_GAP * k)
    dur = clip.duration(settings)
    bg = ff_color(settings.background_color)
    stem = workdir / f"clip_{index:03d}"
    out = stem.with_suffix(".mp4")
    # 줄바꿈은 배율 적용 전 값으로 계산해 최종 출력과 같은 자리에서 끊는다
    title_txt = _write_text(workdir / f"{stem.name}_title.txt", wrap(title, L["title_font"], L["max_text_width"], L["max_lines"]))
    time_txt = _write_text(workdir / f"{stem.name}_time.txt", format_time(clip.t))
    caption_txt = _write_text(workdir / f"{stem.name}_caption.txt", wrap(clip.caption, L["caption_font"], L["max_text_width"], L["max_lines"]))

    filters = [
        f"[0:v]scale={g.sw}:{g.sh},pad={g.pad_w}:{g.pad_h}:{g.pad_x}:{g.pad_y}:color={bg},crop={S}:{S}:{g.crop_x}:{g.crop_y}[sq]",
        f"color=c={bg}:s={W}x{H}:r={fps}:d={dur}[bg]",
        f"[bg][sq]overlay=0:{square_y}[c0]",
    ]
    last = "c0"
    if title.strip():
        filters.append(_drawtext(last, "c1", title_txt, font_path, title_font, ff_color(settings.title_color), "(w-text_w)/2", f"{title_y}-text_h/2", line_spacing=L["line_spacing"]))
        last = "c1"
    if settings.show_time_in_caption:
        filters.append(_drawtext(last, "c2", time_txt, font_path, time_font, ff_color(L["time_color"]), "(w-text_w)/2", str(caption_y - time_gap)))
        last = "c2"
    if clip.caption.strip():
        filters.append(_drawtext(last, "c3", caption_txt, font_path, caption_font, ff_color(settings.caption_color), "(w-text_w)/2", str(caption_y), border=max(1, round(L["caption_border"] * k)), line_spacing=L["line_spacing"]))
        last = "c3"
    filters.append(f"[{last}]null[v]")
    audio_src = "0:a" if probe.has_audio else "1:a"
    filters.append(f"[{audio_src}]afade=t=in:d=0.2,afade=t=out:st={max(0.0, dur - 0.2):.2f}:d=0.2[a]")

    cmd: list = [paths.ffmpeg, "-hide_banner"]
    if in_offset > 0:
        cmd += ["-ss", f"{in_offset:g}"]
    cmd += ["-i", source if source is not None else clip.final_path]
    if not probe.has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", profile.preset, "-crf", str(profile.crf), "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out,
    ]
    return [str(c) for c in cmd], out
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_renderer_commands.py -q`
Expected: 모두 PASS — 특히 기존 `test_basic_command`(y=1510/1552, crf 18), `test_style_y_clamped_to_canvas`(y=0-text_h/2, 1920, 1878)가 그대로 통과해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add stampcut/core/renderer.py tests/test_renderer_commands.py
git commit -m "feat(core): RenderProfile and preview-source options for build_clip_command"
```

---

### Task 4: build_mix_command

**Files:**
- Modify: `stampcut/core/renderer.py` (파일 끝에 추가, import에 `AudioMix`)
- Test: `tests/test_renderer_commands.py`

**Interfaces:**
- Consumes: `AudioMix` (Task 1)
- Produces: `BGM_FADE_IN = 1.0`, `BGM_FADE_OUT = 2.0`, `BGM_MIN_SECTION = 0.5`, `build_mix_command(paths: FfmpegPaths, video: Path, audio: AudioMix, total: float, out: Path) -> list[str]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_renderer_commands.py` import 수정:

```python
from stampcut.core.models import AudioMix, Settings
from stampcut.core.renderer import (
    PREVIEW_PROFILE,
    build_clip_command,
    build_concat_command,
    build_mix_command,
    ...
)
```

파일 끝에 추가:

```python
def test_mix_command_with_bgm(tmp_path):
    song = tmp_path / "song.mp3"
    audio = AudioMix(original_volume=0.5, bgm_path=str(song), bgm_volume=0.3, bgm_offset=30.0, bgm_start=5.0, bgm_end=65.0)
    cmd = build_mix_command(paths(tmp_path), tmp_path / "concat.mp4", audio, 180.0, tmp_path / "out.mp4")
    fc = filter_complex(cmd)
    i_bgm = cmd.index(str(song))
    assert cmd[i_bgm - 1] == "-i" and cmd[i_bgm - 3:i_bgm - 1] == ["-stream_loop", "-1"]
    assert cmd[cmd.index("-i") + 1] == str(tmp_path / "concat.mp4")  # 첫 -i는 영상
    assert "[0:a]volume=0.500[a0]" in fc
    assert "[1:a]atrim=start=30.000,asetpts=PTS-STARTPTS,atrim=duration=60.000,asetpts=PTS-STARTPTS" in fc
    assert "afade=t=in:d=1,afade=t=out:st=58.000:d=2,volume=0.300,adelay=5000|5000,apad[a1]" in fc
    assert "[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]" in fc
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-t") + 1] == "180.000"
    assert cmd[cmd.index("-map") + 1] == "0:v" and "[a]" in cmd
    assert cmd[-1] == str(tmp_path / "out.mp4")


def test_mix_command_without_bgm_only_scales_original(tmp_path):
    cmd = build_mix_command(paths(tmp_path), tmp_path / "c.mp4", AudioMix(original_volume=0.25), 30.0, tmp_path / "o.mp4")
    assert filter_complex(cmd) == "[0:a]volume=0.250[a]"
    assert "-stream_loop" not in cmd and cmd.count("-i") == 1


def test_mix_command_drops_bgm_when_section_too_short(tmp_path):
    audio = AudioMix(bgm_path="s.mp3", bgm_start=100.0, bgm_end=100.2)
    cmd = build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 180.0, tmp_path / "o.mp4")
    assert filter_complex(cmd) == "[0:a]volume=1.000[a]" and "s.mp3" not in cmd
    audio = AudioMix(bgm_path="s.mp3", bgm_start=500.0)  # 구간이 영상 밖 → total로 잘려 0초
    assert "s.mp3" not in build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 180.0, tmp_path / "o.mp4")


def test_mix_command_end_none_runs_to_total(tmp_path):
    audio = AudioMix(bgm_path="s.mp3", bgm_start=10.0)
    fc = filter_complex(build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 100.0, tmp_path / "o.mp4"))
    assert "atrim=duration=90.000" in fc and "afade=t=out:st=88.000:d=2" in fc and "adelay=10000|10000" in fc
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_renderer_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_mix_command'`

- [ ] **Step 3: 구현**

`renderer.py` import 수정:

```python
from stampcut.core.models import AudioMix, Clip, Settings
```

파일 끝(`build_concat_command` 뒤)에 추가:

```python
BGM_FADE_IN = 1.0
BGM_FADE_OUT = 2.0
BGM_MIN_SECTION = 0.5  # 이보다 짧은 구간엔 BGM을 넣지 않는다


def build_mix_command(paths: FfmpegPaths, video: Path, audio: AudioMix, total: float, out: Path) -> list[str]:
    """concat 결과(video)에 원본 볼륨·BGM을 적용해 최종 파일을 만드는 명령. 비디오는 재인코딩하지 않는다.

    BGM 반복 규칙: 첫 재생은 bgm_offset부터 곡 끝까지, 이후 곡 처음부터 반복 (bgm_sync.bgm_position과 동일).
    -stream_loop -1로 무한 반복한 스트림에서 앞 offset초를 잘라내면 정확히 이 동작이 된다.
    """
    orig = f"volume={_clamp(audio.original_volume, 0.0, 1.0):.3f}"
    start = _clamp(audio.bgm_start, 0.0, total)
    end = _clamp(audio.bgm_end if audio.bgm_end is not None else total, start, total)
    section = end - start
    cmd: list = [paths.ffmpeg, "-hide_banner", "-i", video]
    if audio.has_bgm() and section >= BGM_MIN_SECTION:
        cmd += ["-stream_loop", "-1", "-i", audio.bgm_path]
        start_ms = int(round(start * 1000))
        bgm = ",".join(
            [
                f"atrim=start={max(0.0, audio.bgm_offset):.3f}",
                "asetpts=PTS-STARTPTS",
                f"atrim=duration={section:.3f}",
                "asetpts=PTS-STARTPTS",
                f"afade=t=in:d={BGM_FADE_IN:g}",
                f"afade=t=out:st={max(0.0, section - BGM_FADE_OUT):.3f}:d={BGM_FADE_OUT:g}",
                f"volume={_clamp(audio.bgm_volume, 0.0, 1.0):.3f}",
                f"adelay={start_ms}|{start_ms}",
                "apad",
            ]
        )
        filters = f"[0:a]{orig}[a0];[1:a]{bgm}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    else:
        filters = f"[0:a]{orig}[a]"
    cmd += [
        "-filter_complex", filters,
        "-map", "0:v", "-c:v", "copy",
        "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{total:.3f}",
        "-movflags", "+faststart",
        out,
    ]
    return [str(c) for c in cmd]
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_renderer_commands.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add stampcut/core/renderer.py tests/test_renderer_commands.py
git commit -m "feat(core): build_mix_command — BGM/original volume mix after concat"
```

---

### Task 5: pipeline.render 믹스 단계 + BGM 검증 + 진행률 가중치

**Files:**
- Modify: `stampcut/core/pipeline.py:120-185` (`render`)
- Modify: `stampcut/gui/status_bar.py:13` (`STAGE_WEIGHTS`)
- Test: `tests/test_pipeline.py`, `tests/test_status_bar.py`

**Interfaces:**
- Consumes: `build_mix_command` (Task 4), `AudioMix.has_bgm/is_default` (Task 1)
- Produces: `render()`가 `project.audio`를 읽어 concat 뒤 `"mix"` 단계를 실행. 진행률 stage `"mix"` 추가.

- [ ] **Step 1: 실패하는 테스트 작성 (status_bar)**

`tests/test_status_bar.py`의 `test_overall_percent`를 다음으로 교체:

```python
def test_overall_percent():
    assert overall_percent("download", 1, 4) == 10
    assert overall_percent("render", 150, 300) == 65
    assert overall_percent("concat", 1, 1) == 95
    assert overall_percent("mix", 1, 1) == 100
    assert overall_percent("preview", 2, 4) == 50
    assert overall_percent("analyze", 0, 0) == 0
```

- [ ] **Step 2: 실패하는 테스트 작성 (pipeline)**

`tests/test_pipeline.py` import 수정:

```python
from stampcut.core.models import AudioMix, ClipStatus, Project, RawComment, Settings
```

파일 끝에 추가:

```python
def test_render_mixes_bgm_after_concat(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    p.audio = AudioMix(bgm_path=str(song), bgm_volume=0.2)
    out = tmp_path / "out" / "제목.mp4"
    prog = []
    pipeline.render(p, Settings(), out, FakeDownloader(tmp_path), _paths(tmp_path), progress=lambda *x: prog.append(x))
    assert out.exists() and len(fake_ffmpeg) == 3  # 클립 렌더 + concat + 믹스
    mix = fake_ffmpeg[-1]
    assert str(song) in mix and "-stream_loop" in mix and mix[mix.index("-c:v") + 1] == "copy"
    assert mix[mix.index("-t") + 1] == "18.000"  # probe 길이
    assert "concat" in " ".join(fake_ffmpeg[-2])
    assert [x[0] for x in prog if x[0] == "mix"] == ["mix", "mix"] and prog[-1][:2] == ("mix", 1)
    assert not any((tmp_path / "render").iterdir())


def test_render_skips_mix_for_default_audio(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    pipeline.render(p, Settings(), tmp_path / "o.mp4", FakeDownloader(tmp_path), _paths(tmp_path))
    assert len(fake_ffmpeg) == 2 and not any("-stream_loop" in c for c in fake_ffmpeg)


def test_render_original_volume_only_still_mixes(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    p.audio = AudioMix(original_volume=0.5)
    pipeline.render(p, Settings(), tmp_path / "o.mp4", FakeDownloader(tmp_path), _paths(tmp_path))
    mix = fake_ffmpeg[-1]
    assert len(fake_ffmpeg) == 3 and "volume=0.500" in mix[mix.index("-filter_complex") + 1]


def test_render_missing_bgm_fails_before_download(fake_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    p = Project([v.url], "제목", [v], [make_clip(v, 100)])
    p.audio = AudioMix(bgm_path=str(tmp_path / "gone.mp3"))
    dl = FakeDownloader(tmp_path)
    with pytest.raises(ValueError, match="배경 음악"):
        pipeline.render(p, Settings(), tmp_path / "o.mp4", dl, _paths(tmp_path))
    assert dl.calls == [] and fake_ffmpeg == []
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_pipeline.py tests/test_status_bar.py -q`
Expected: 새 테스트 5개 FAIL (`len(fake_ffmpeg) == 2`, `overall_percent("concat", 1, 1) == 100` 등)

- [ ] **Step 4: status_bar.py 구현**

```python
STAGE_WEIGHTS = {"download": (0, 40), "render": (40, 50), "concat": (90, 5), "mix": (95, 5)}
```

- [ ] **Step 5: pipeline.py 구현**

import 수정:

```python
from stampcut.core.renderer import build_clip_command, build_concat_command, build_mix_command, write_concat_list
```

`render`의 앞부분(클립 검사 뒤)에 BGM 검증 추가:

```python
    clips = project.enabled_clips()
    if not clips:
        raise ValueError("켜진 클립이 없습니다")
    audio = project.audio
    if audio.has_bgm() and not Path(audio.bgm_path).is_file():
        raise ValueError(f"배경 음악 파일을 찾을 수 없습니다: {audio.bgm_path}")
    _check(cancel)
```

`render`의 concat 이후 부분(`progress("concat", 0, 1, "합치는 중")`부터 `return output_path`까지)을 교체:

```python
    progress("concat", 0, 1, "합치는 중")
    concat_out = job / "concat.mp4"
    ff.run(build_concat_command(paths, write_concat_list(outputs, job), concat_out), cancel=cancel)
    final = concat_out
    if not audio.is_default():
        _check(cancel)
        progress("mix", 0, 1, "배경 음악 섞는 중")
        total = ff.probe(paths, concat_out).duration
        final = job / "output.mp4"
        ff.run(build_mix_command(paths, concat_out, audio, total, final), cancel=cancel, total_seconds=total)
    # 성공했을 때만 결과 파일이 생기도록 임시 파일을 옮긴다 (취소/실패는 output_path를 남기지 않는다).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(final, output_path)
    for d in render_dir().iterdir():  # 이번 작업 폴더와 이전에 남은 찌꺼기를 함께 지운다
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    progress("concat" if audio.is_default() else "mix", 1, 1, "완료")
    return output_path
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_pipeline.py tests/test_status_bar.py -q`
Expected: 모두 PASS (기존 `test_render_flow`의 `prog[-1][:2] == ("concat", 1)`도 유지)

- [ ] **Step 7: 커밋**

```bash
git add stampcut/core/pipeline.py stampcut/gui/status_bar.py tests/test_pipeline.py tests/test_status_bar.py
git commit -m "feat(core): mix BGM after concat in render; validate BGM file before download"
```

---

### Task 6: render_preview + preview_signature + preview_dir

**Files:**
- Modify: `stampcut/core/settings.py` (`render_dir` 아래)
- Modify: `stampcut/core/pipeline.py` (import, 파일 끝)
- Test: `tests/test_settings.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `PREVIEW_PROFILE`, `build_clip_command(profile, source, in_offset)` (Task 3), `preview_covers` (기존)
- Produces: `settings.preview_dir() -> Path`; `pipeline.render_preview(project, s, paths, progress=_noop, cancel=None) -> Path`; `pipeline.preview_signature(project, s) -> str`

- [ ] **Step 1: 실패하는 테스트 작성 (settings)**

`tests/test_settings.py`의 `test_dirs_follow_env`에 한 줄 추가:

```python
    assert sm.preview_dir() == tmp_path / "local" / "StampCut" / "preview"
```

- [ ] **Step 2: 실패하는 테스트 작성 (pipeline)**

`tests/test_pipeline.py` 끝에 추가:

```python
@pytest.fixture
def fake_preview_ffmpeg(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(pipeline, "preview_dir", lambda: tmp_path / "preview")
    monkeypatch.setattr(pipeline.ff, "probe", lambda paths, f: ProbeInfo(640, 360, 90.0, True))

    def fake_run(cmd, on_progress=None, cancel=None, total_seconds=None):
        runs.append(cmd)
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")
        if on_progress:
            on_progress(1.0)

    monkeypatch.setattr(pipeline.ff, "run", fake_run)
    return runs


def _ready(clip, s, root):
    """여유분(앞 30초/뒤 60초)이 있는 미리보기 구간 파일이 받아진 상태."""
    clip.preview_start, clip.preview_end = max(0, clip.t - 30), clip.t + 60
    clip.preview_path = root / f"p{clip.t}.mp4"
    clip.preview_path.write_bytes(b"x")
    clip.status = ClipStatus.READY
    return clip


def test_render_preview_uses_preview_files_with_offsets(fake_preview_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    s = Settings()
    a, off, c = _ready(make_clip(v, 100), s, tmp_path), make_clip(v, 500, enabled=False), _ready(make_clip(v, 900), s, tmp_path)
    p = Project([v.url], "제목", [v], [a, off, c])
    prog = []
    result = pipeline.render_preview(p, s, _paths(tmp_path), progress=lambda *x: prog.append(x))
    assert result.parent == tmp_path / "preview" and result.name.startswith("full_") and result.exists()
    assert len(fake_preview_ffmpeg) == 3  # 켜진 클립 2개 + concat
    first = fake_preview_ffmpeg[0]
    assert first[first.index("-ss") + 1] == "27"  # start(97) - preview_start(70)
    assert first[first.index("-i") + 1] == str(a.preview_path)
    assert first[first.index("-preset") + 1] == "ultrafast" and "s=540x960" in first[first.index("-filter_complex") + 1]
    assert "concat" in fake_preview_ffmpeg[-1]
    assert not any(d.is_dir() for d in (tmp_path / "preview").iterdir())  # 작업 폴더 정리
    assert prog[0][0] == "preview_render" and prog[-1][3] == "전체 미리보기 준비됨"


def test_render_preview_replaces_previous_file(fake_preview_ffmpeg, tmp_path, make_video, make_clip):
    v = make_video()
    s = Settings()
    p = Project([v.url], "제목", [v], [_ready(make_clip(v, 100), s, tmp_path)])
    old = tmp_path / "preview" / "full_old.mp4"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    result = pipeline.render_preview(p, s, _paths(tmp_path))
    assert not old.exists() and result.exists()


def test_render_preview_requires_ready_previews(tmp_path, make_video, make_clip, monkeypatch):
    monkeypatch.setattr(pipeline, "preview_dir", lambda: tmp_path / "preview")
    v = make_video()
    s = Settings()
    a, b = _ready(make_clip(v, 100), s, tmp_path), make_clip(v, 758)
    with pytest.raises(ValueError, match="3게임 12:38"):
        pipeline.render_preview(Project([v.url], "제목", [v], [a, b]), s, _paths(tmp_path))
    assert not (tmp_path / "preview").exists()
    with pytest.raises(ValueError, match="켜진 클립"):
        pipeline.render_preview(Project([v.url], "제목", [v], [make_clip(v, 1, enabled=False)]), s, _paths(tmp_path))


def test_render_preview_cancel_cleans_up(tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(pipeline, "preview_dir", lambda: tmp_path / "preview")
    monkeypatch.setattr(pipeline.ff, "probe", lambda paths, f: ProbeInfo(640, 360, 90.0, True))

    def fake_run(cmd, on_progress=None, cancel=None, total_seconds=None):
        if "concat" in cmd:
            raise Cancelled()
        Path(cmd[-1]).write_bytes(b"x")

    monkeypatch.setattr(pipeline.ff, "run", fake_run)
    v = make_video()
    s = Settings()
    p = Project([v.url], "제목", [v], [_ready(make_clip(v, 100), s, tmp_path)])
    with pytest.raises(Cancelled):
        pipeline.render_preview(p, s, _paths(tmp_path))
    assert list((tmp_path / "preview").iterdir()) == []


def test_preview_signature_tracks_visible_edits_only(make_video, make_clip):
    v = make_video()
    s = Settings()
    a, b = make_clip(v, 100), make_clip(v, 500, enabled=False)
    p = Project([v.url], "제목", [v], [a, b])
    base = pipeline.preview_signature(p, s)
    assert base == pipeline.preview_signature(p, s)
    b.caption = "끈 클립 자막"
    p.audio = AudioMix(bgm_path="x.mp3")
    assert pipeline.preview_signature(p, s) == base  # 끈 클립·BGM은 화면에 안 보임
    a.caption = "바뀐 자막"
    changed = pipeline.preview_signature(p, s)
    assert changed != base
    b.enabled = True
    assert pipeline.preview_signature(p, s) != changed
    p.title = "다른 제목"
    t = pipeline.preview_signature(p, s)
    assert t != changed
    assert pipeline.preview_signature(p, Settings(title_y=100)) != t
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_settings.py tests/test_pipeline.py -q`
Expected: FAIL — `AttributeError: module 'stampcut.core.settings' has no attribute 'preview_dir'`, `AttributeError: ... 'preview_dir'`/`'render_preview'`

- [ ] **Step 4: settings.py 구현**

`render_dir` 아래에 추가:

```python
def preview_dir() -> Path:
    return data_dir() / "preview"
```

- [ ] **Step 5: pipeline.py 구현**

import 수정/추가:

```python
import hashlib
import json
...
from stampcut.core.renderer import PREVIEW_PROFILE, build_clip_command, build_concat_command, build_mix_command, write_concat_list
from stampcut.core.settings import preview_dir, render_dir, resolve_font
```

파일 끝에 추가:

```python
def preview_signature(project: Project, s: Settings) -> str:
    """전체 미리보기 파일이 지금 편집 상태와 맞는지 비교하기 위한 해시. 화면에 보이는 것만 (BGM·끈 클립 제외)."""
    payload = {
        "title": project.title,
        "clips": [
            [c.video.video_id, c.t, c.effective_pre(s), c.effective_post(s), c.zoom, c.pan_x, c.pan_y, c.caption]
            for c in project.enabled_clips()
        ],
        "style": [s.title_y, s.title_color, s.caption_y, s.caption_color, s.background_color, s.show_time_in_caption, s.font_path],
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def render_preview(
    project: Project,
    s: Settings,
    paths: ff.FfmpegPaths,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
) -> Path:
    """켜진 클립의 360p 미리보기 구간으로 540x960 전체 미리보기 mp4를 만든다. 고화질 다운로드·BGM 없음."""
    clips = project.enabled_clips()
    if not clips:
        raise ValueError("켜진 클립이 없습니다")
    missing = [c for c in clips if not preview_covers(c, s)]
    if missing:
        raise ValueError("미리보기가 준비되지 않은 클립: " + ", ".join(_label(c) for c in missing))
    _check(cancel)
    root = preview_dir()
    job = root / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    font = resolve_font(s)
    n = len(clips)
    try:
        outputs: list[Path] = []
        for i, c in enumerate(clips):
            _check(cancel)
            label = _label(c)
            progress("preview_render", i * 100, n * 100, f"{label} 미리보기 렌더 중")
            info = ff.probe(paths, c.preview_path)
            cmd, out = build_clip_command(
                paths, c, s, project.title, info, job, i, font,
                profile=PREVIEW_PROFILE, source=c.preview_path, in_offset=c.start(s) - c.preview_start,
            )

            def _on_fraction(f: float, i: int = i, label: str = label) -> None:
                progress("preview_render", i * 100 + int(f * 100), n * 100, f"{label} 미리보기 렌더 중")

            ff.run(cmd, on_progress=_on_fraction, cancel=cancel, total_seconds=c.duration(s))
            outputs.append(out)
        _check(cancel)
        progress("preview_render", n * 100, n * 100, "합치는 중")
        tmp = job / "full.mp4"
        ff.run(build_concat_command(paths, write_concat_list(outputs, job), tmp), cancel=cancel)
        result = root / f"full_{job.name}.mp4"
        os.replace(tmp, result)
    finally:
        shutil.rmtree(job, ignore_errors=True)
    for old in root.glob("full_*.mp4"):
        if old != result:
            try:
                old.unlink()
            except OSError:  # 플레이어가 잡고 있는 파일 — 다음에 다시 시도
                pass
    progress("preview_render", n * 100, n * 100, "전체 미리보기 준비됨")
    return result
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_settings.py tests/test_pipeline.py -q`
Expected: 모두 PASS

- [ ] **Step 7: 커밋**

```bash
git add stampcut/core/settings.py stampcut/core/pipeline.py tests/test_settings.py tests/test_pipeline.py
git commit -m "feat(core): render_preview (540x960 from preview segments) and preview_signature"
```

---

### Task 7: BgmPanel 위젯

**Files:**
- Create: `stampcut/gui/bgm_panel.py`
- Test: `tests/test_bgm_panel.py`

**Interfaces:**
- Consumes: `AudioMix` (Task 1)
- Produces: `list_audio_files(folder) -> list[Path]`; `BgmPanel(QGroupBox)` with signals `changed()`, `bgm_dir_changed(str)`; methods `set_mix(mix: AudioMix | None)`, `set_bgm_dir(str)`, `bgm_dir() -> str`, `select_file(path: str)`, `set_busy(bool)`, `stop()`; attributes `mix`, `file_combo`, `original_slider`, `bgm_slider`, `original_label`, `bgm_label`, `offset_spin`, `start_spin`, `end_spin`, `listen_btn`, `player`, `audio_out`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_bgm_panel.py`:

```python
from stampcut.core.models import AudioMix
from stampcut.gui.bgm_panel import BgmPanel, list_audio_files


def _folder(tmp_path):
    d = tmp_path / "music"
    d.mkdir()
    for name in ("b.MP3", "a.wav", "notes.txt", "c.flac"):
        (d / name).write_bytes(b"x")
    return d


def _panel(qtbot, monkeypatch):
    p = BgmPanel()
    qtbot.addWidget(p)
    # 가짜 파일을 실제로 열지 않는다
    monkeypatch.setattr(p.player, "setSource", lambda url: None)
    monkeypatch.setattr(p.player, "play", lambda: None)
    return p


def test_list_audio_files_filters_and_sorts(tmp_path):
    d = _folder(tmp_path)
    assert [p.name for p in list_audio_files(d)] == ["a.wav", "b.MP3", "c.flac"]
    assert list_audio_files("") == [] and list_audio_files(tmp_path / "nope") == []


def test_panel_lists_folder_and_none_first(qtbot, monkeypatch, tmp_path):
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(_folder(tmp_path)))
    p.set_mix(AudioMix())
    assert [p.file_combo.itemText(i) for i in range(p.file_combo.count())] == ["없음", "a.wav", "b.MP3", "c.flac"]
    assert p.file_combo.currentIndex() == 0 and p.file_combo.itemData(0) == ""
    assert not p.bgm_slider.isEnabled() and not p.listen_btn.isEnabled() and p.original_slider.isEnabled()


def test_selecting_file_updates_mix_and_enables_controls(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    mix = AudioMix()
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(d))
    p.set_mix(mix)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.file_combo.setCurrentIndex(1)
    assert mix.bgm_path == str(d / "a.wav") and p.bgm_slider.isEnabled() and p.listen_btn.isEnabled()
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.file_combo.setCurrentIndex(0)
    assert mix.bgm_path == "" and not p.bgm_slider.isEnabled()


def test_sliders_and_spins_write_mix(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    mix = AudioMix(bgm_path=str(d / "a.wav"))
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(d))
    p.set_mix(mix)
    assert p.file_combo.currentText() == "a.wav"
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.original_slider.setValue(40)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.bgm_slider.setValue(55)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.offset_spin.setValue(12.5)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.start_spin.setValue(3.0)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.end_spin.setValue(45.0)
    assert (mix.original_volume, mix.bgm_volume, mix.bgm_offset, mix.bgm_start, mix.bgm_end) == (0.4, 0.55, 12.5, 3.0, 45.0)
    assert p.original_label.text() == "40%" and p.bgm_label.text() == "55%"
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.end_spin.setValue(0.0)
    assert mix.bgm_end is None and p.end_spin.text() == "끝까지"


def test_browse_adds_outside_file_and_missing_file_is_marked(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(b"x")
    mix = AudioMix()
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(d))
    p.set_mix(mix)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.select_file(str(outside))
    assert mix.bgm_path == str(outside) and p.file_combo.currentText() == "elsewhere.mp3"
    p.set_mix(AudioMix(bgm_path=str(tmp_path / "gone.mp3")))
    assert p.file_combo.currentText() == "gone.mp3 (파일 없음)"


def test_none_mix_disables_and_busy_stops_listening(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    p = _panel(qtbot, monkeypatch)
    p.set_mix(None)
    assert not p.isEnabled()
    p.set_bgm_dir(str(d))
    p.set_mix(AudioMix(bgm_path=str(d / "a.wav")))
    assert p.isEnabled()
    p.listen_btn.setChecked(True)
    assert p.listen_btn.text() == "⏹ 정지"
    p.set_busy(True)
    assert not p.isEnabled() and not p.listen_btn.isChecked() and p.listen_btn.text() == "▶ BGM만 듣기"
    p.set_busy(False)
    assert p.isEnabled()
    p.listen_btn.setChecked(True)
    p.stop()
    assert not p.listen_btn.isChecked()
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_bgm_panel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stampcut.gui.bgm_panel'`

- [ ] **Step 3: 구현**

`stampcut/gui/bgm_panel.py`:

```python
"""배경 음악 패널: 음원 선택·볼륨·위치 + BGM 단독 듣기."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from stampcut.core.models import AudioMix

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
NONE_LABEL = "없음"
MISSING_SUFFIX = " (파일 없음)"
LISTEN_LABEL = "▶ BGM만 듣기"
STOP_LABEL = "⏹ 정지"


def list_audio_files(folder: str | Path) -> list[Path]:
    """폴더 안의 오디오 파일을 이름순으로. 폴더가 없으면 []."""
    if not folder:
        return []
    d = Path(folder)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS), key=lambda p: p.name.lower())


class BgmPanel(QGroupBox):
    changed = Signal()  # mix를 제자리 수정한 뒤 발생
    bgm_dir_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("배경 음악", parent)
        self.mix: AudioMix | None = None
        self._bgm_dir = ""

        self.folder_btn = QPushButton("폴더…")
        self.folder_btn.clicked.connect(self._choose_folder)
        self.file_combo = QComboBox()
        self.file_combo.currentIndexChanged.connect(self._on_file_changed)
        self.browse_btn = QPushButton("찾아보기…")
        self.browse_btn.clicked.connect(self._browse_file)

        self.original_slider, self.original_label = self._volume_slider(100)
        self.original_slider.valueChanged.connect(self._on_original_volume)
        self.bgm_slider, self.bgm_label = self._volume_slider(30)
        self.bgm_slider.valueChanged.connect(self._on_bgm_volume)

        self.offset_spin = self._seconds_spin()
        self.offset_spin.valueChanged.connect(self._on_offset)
        self.start_spin = self._seconds_spin()
        self.start_spin.valueChanged.connect(self._on_start)
        self.end_spin = self._seconds_spin()
        self.end_spin.setSpecialValueText("끝까지")  # 0 = None
        self.end_spin.valueChanged.connect(self._on_end)

        self.listen_btn = QPushButton(LISTEN_LABEL)
        self.listen_btn.setCheckable(True)
        self.listen_btn.toggled.connect(self._on_listen_toggled)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        layout = QVBoxLayout(self)
        row1 = QHBoxLayout()
        row1.addWidget(self.folder_btn)
        row1.addWidget(self.file_combo, 1)
        row1.addWidget(self.browse_btn)
        layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("원본 볼륨"))
        row2.addWidget(self.original_slider, 1)
        row2.addWidget(self.original_label)
        row2.addSpacing(12)
        row2.addWidget(QLabel("BGM 볼륨"))
        row2.addWidget(self.bgm_slider, 1)
        row2.addWidget(self.bgm_label)
        layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("음원 시작"))
        row3.addWidget(self.offset_spin)
        row3.addSpacing(12)
        row3.addWidget(QLabel("영상 구간"))
        row3.addWidget(self.start_spin)
        row3.addWidget(QLabel("~"))
        row3.addWidget(self.end_spin)
        row3.addStretch(1)
        row3.addWidget(self.listen_btn)
        layout.addLayout(row3)
        self.set_mix(None)

    @staticmethod
    def _volume_slider(value: int) -> tuple[QSlider, QLabel]:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(5)
        slider.setPageStep(10)
        slider.setValue(value)
        label = QLabel(f"{value}%")
        label.setMinimumWidth(36)
        return slider, label

    @staticmethod
    def _seconds_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 3600.0)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix("초")
        spin.setKeyboardTracking(False)
        return spin

    # --- 외부 API ---
    def set_bgm_dir(self, folder: str) -> None:
        self._bgm_dir = folder
        self._rebuild_files()

    def bgm_dir(self) -> str:
        return self._bgm_dir

    def set_mix(self, mix: AudioMix | None) -> None:
        """패널이 mix를 직접 참조해 제자리 수정한다. None이면 전체 비활성."""
        self.stop()
        self.mix = mix
        self._rebuild_files()
        self._sync_controls()

    def set_busy(self, busy: bool) -> None:
        self.stop()
        self.setEnabled(not busy and self.mix is not None)

    def stop(self) -> None:
        if self.listen_btn.isChecked():
            self.listen_btn.setChecked(False)  # → _on_listen_toggled(False)
        else:
            self.player.stop()

    def select_file(self, path: str) -> None:
        """드롭다운에 없으면 항목을 추가하고 선택한다 (찾아보기·복원·테스트용)."""
        if self.file_combo.findData(path) < 0:
            self.file_combo.addItem(self._display_name(path), path)
        self.file_combo.setCurrentIndex(self.file_combo.findData(path))  # → _on_file_changed

    # --- 내부 ---
    @staticmethod
    def _display_name(path: str) -> str:
        p = Path(path)
        return p.name + ("" if p.is_file() else MISSING_SUFFIX)

    def _rebuild_files(self) -> None:
        """드롭다운 = 없음 + 폴더 내 오디오 + (목록에 없는) 현재 선택 파일."""
        current = self.mix.bgm_path if self.mix is not None else ""
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        self.file_combo.addItem(NONE_LABEL, "")
        paths = [str(p) for p in list_audio_files(self._bgm_dir)]
        if current and current not in paths:
            paths.append(current)
        for p in paths:
            self.file_combo.addItem(self._display_name(p), p)
        self.file_combo.setCurrentIndex(max(0, self.file_combo.findData(current)) if current else 0)
        self.file_combo.blockSignals(False)

    def _sync_controls(self) -> None:
        mix = self.mix
        self.setEnabled(mix is not None)
        if mix is None:
            return
        widgets = (self.original_slider, self.bgm_slider, self.offset_spin, self.start_spin, self.end_spin)
        for w in widgets:
            w.blockSignals(True)
        self.original_slider.setValue(int(round(mix.original_volume * 100)))
        self.bgm_slider.setValue(int(round(mix.bgm_volume * 100)))
        self.offset_spin.setValue(mix.bgm_offset)
        self.start_spin.setValue(mix.bgm_start)
        self.end_spin.setValue(mix.bgm_end if mix.bgm_end is not None else 0.0)
        for w in widgets:
            w.blockSignals(False)
        self.original_label.setText(f"{self.original_slider.value()}%")
        self.bgm_label.setText(f"{self.bgm_slider.value()}%")
        has = mix.has_bgm()
        for w in (self.bgm_slider, self.bgm_label, self.offset_spin, self.start_spin, self.end_spin, self.listen_btn):
            w.setEnabled(has)

    def _emit(self) -> None:
        if self.mix is not None:
            self.changed.emit()

    def _choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "BGM 폴더", self._bgm_dir or str(Path.home()))
        if d:
            self.set_bgm_dir(d)
            self.bgm_dir_changed.emit(d)

    def _browse_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        f, _ = QFileDialog.getOpenFileName(self, "배경 음악 파일", self._bgm_dir or str(Path.home()), f"오디오 파일 ({exts})")
        if f:
            self.select_file(f)

    def _on_file_changed(self, index: int) -> None:
        if self.mix is None:
            return
        self.stop()
        self.mix.bgm_path = self.file_combo.itemData(index) or ""
        self._sync_controls()
        self._emit()

    def _on_original_volume(self, value: int) -> None:
        self.original_label.setText(f"{value}%")
        if self.mix is not None:
            self.mix.original_volume = value / 100
            self._emit()

    def _on_bgm_volume(self, value: int) -> None:
        self.bgm_label.setText(f"{value}%")
        if self.mix is not None:
            self.mix.bgm_volume = value / 100
            self.audio_out.setVolume(self.mix.bgm_volume)
            self._emit()

    def _on_offset(self, value: float) -> None:
        if self.mix is not None:
            self.mix.bgm_offset = value
            if self.listen_btn.isChecked():
                self.player.setPosition(int(value * 1000))
            self._emit()

    def _on_start(self, value: float) -> None:
        if self.mix is not None:
            self.mix.bgm_start = value
            self._emit()

    def _on_end(self, value: float) -> None:
        if self.mix is not None:
            self.mix.bgm_end = value if value > 0 else None
            self._emit()

    # --- BGM만 듣기 ---
    def _on_listen_toggled(self, on: bool) -> None:
        if not on:
            self.player.stop()
            self.listen_btn.setText(LISTEN_LABEL)
            return
        if self.mix is None or not self.mix.has_bgm() or not Path(self.mix.bgm_path).is_file():
            self.listen_btn.setChecked(False)  # → 위의 not on 경로
            return
        self.player.setSource(QUrl.fromLocalFile(self.mix.bgm_path))
        self.audio_out.setVolume(self.mix.bgm_volume)
        self.player.play()
        self.player.setPosition(int(self.mix.bgm_offset * 1000))
        self.listen_btn.setText(STOP_LABEL)

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.listen_btn.isChecked():
            self.listen_btn.setChecked(False)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_bgm_panel.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add stampcut/gui/bgm_panel.py tests/test_bgm_panel.py
git commit -m "feat(gui): BgmPanel — folder/file pick, volumes, positions, BGM-only listen"
```

---

### Task 8: PreviewWidget "전체" 모드 + BGM 동기 재생

**Files:**
- Modify: `stampcut/gui/preview_widget.py`
- Test: `tests/test_preview_widget.py`

**Interfaces:**
- Consumes: `bgm_position` (Task 2), `AudioMix` (Task 1)
- Produces: signals `full_preview_requested()`, `bgm_error(str)`; methods `set_full_preview(path: Path, signature: str)`, `mark_full_preview_stale()`, `clear_full_preview()`, `full_signature() -> str | None`, `mode() -> "clip" | "full"`, `set_mode(mode: str)`, `set_audio_mix(mix: AudioMix | None)`; attributes `full_item`, `bgm_player`, `bgm_audio`, `audio_mix`, `clip_mode_btn`, `full_mode_btn`, `make_full_btn`, `full_status`; `_on_duration(ms)` (테스트가 직접 호출).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_preview_widget.py` import 수정:

```python
from stampcut.core.models import AudioMix, Settings
```

파일 끝에 추가:

```python
def _full_file(tmp_path):
    p = tmp_path / "full_x.mp4"
    p.write_bytes(b"x")
    return p


def _widget(qtbot, monkeypatch):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    # 가짜 파일을 실제로 열지 않는다
    monkeypatch.setattr(w.player, "setSource", lambda url: None)
    monkeypatch.setattr(w.player, "play", lambda: None)
    monkeypatch.setattr(w.bgm_player, "setSource", lambda url: None)
    monkeypatch.setattr(w.bgm_player, "play", lambda: None)
    return w


def test_full_mode_switches_output_and_hides_overlays(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    clip = make_clip(make_video(), t=758, caption="원더골")
    w.set_title("제목")
    w.set_clip(clip)
    assert not w.full_mode_btn.isEnabled() and w.mode() == "clip" and w.clip_mode_btn.isChecked()
    w.set_full_preview(_full_file(tmp_path), "sig1")
    assert w.mode() == "full" and w.full_mode_btn.isChecked() and w.full_mode_btn.isEnabled()
    assert w.player.videoOutput() is w.full_item and w.full_item.isVisible() and not w.square.isVisible()
    assert not w.title_item.isVisible() and not w.caption_item.isVisible() and not w.time_item.isVisible()
    assert not w.controls_panel.isEnabled() and w.play_btn.isEnabled() and w.seek_slider.isEnabled()
    assert w.full_signature() == "sig1" and w.full_status.text() == "전체 미리보기 최신"
    w.set_title("다른 제목")  # relayout이 오버레이를 다시 켜면 안 된다
    assert not w.title_item.isVisible()
    w.set_clip(clip)  # 전체 모드에선 행 선택이 재생을 바꾸지 않는다
    assert w.mode() == "full" and w.clip is clip
    w.set_mode("clip")
    assert w.mode() == "clip" and w.player.videoOutput() is w.video_item and w.clip_mode_btn.isChecked()
    assert w.title_item.isVisible() and w.caption_item.isVisible() and w.square.isVisible() and not w.full_item.isVisible()
    assert w.controls_panel.isEnabled()


def test_full_mode_seek_range_follows_duration(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    w.set_clip(make_clip(make_video(), t=758))
    w.set_full_preview(_full_file(tmp_path), "sig")
    w._on_duration(125_000)
    assert w.seek_slider.maximum() == 125_000
    w._on_position(61_000)
    assert w.seek_slider.value() == 61_000 and w.pos_label.text() == "1:01 / 2:05"


def test_stale_and_clear(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    w.set_clip(make_clip(make_video(), t=758))
    w.mark_full_preview_stale()  # 파일 없을 땐 아무 일도 없다
    assert w.full_status.text() == ""
    w.set_full_preview(_full_file(tmp_path), "sig")
    w.mark_full_preview_stale()
    assert "다시 만들기" in w.full_status.text() and w.mode() == "full"
    w.set_full_preview(_full_file(tmp_path), "sig2")  # 다시 만들면 최신
    assert w.full_status.text() == "전체 미리보기 최신" and w.full_signature() == "sig2"
    w.clear_full_preview()
    assert w.mode() == "clip" and not w.full_mode_btn.isEnabled() and w.full_signature() is None and w.full_status.text() == ""


def test_set_audio_mix_applies_volumes_in_full_mode(qtbot, tmp_path, make_video, make_clip, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    mix = AudioMix(original_volume=0.5, bgm_path=str(song), bgm_volume=0.2)
    w.set_clip(make_clip(make_video(), t=758))
    w.set_audio_mix(mix)
    assert abs(w.audio.volume() - 1.0) < 1e-6  # 클립 모드에선 원본 100%
    w.set_full_preview(_full_file(tmp_path), "sig")
    assert abs(w.audio.volume() - 0.5) < 1e-6 and abs(w.bgm_audio.volume() - 0.2) < 1e-6
    mix.original_volume = 0.8
    mix.bgm_volume = 0.6
    w.set_audio_mix(mix)
    assert abs(w.audio.volume() - 0.8) < 1e-6 and abs(w.bgm_audio.volume() - 0.6) < 1e-6
    w.set_mode("clip")
    assert abs(w.audio.volume() - 1.0) < 1e-6


def test_full_preview_button_emits_request(qtbot, monkeypatch):
    w = _widget(qtbot, monkeypatch)
    with qtbot.waitSignal(w.full_preview_requested, timeout=1000):
        w.make_full_btn.click()


def test_shutdown_stops_bgm_player(qtbot):
    w = PreviewWidget(Settings(), "Malgun Gothic")
    qtbot.addWidget(w)
    w.shutdown()
    assert w.bgm_player.source().isEmpty()
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_preview_widget.py -q`
Expected: FAIL — `AttributeError: 'PreviewWidget' object has no attribute 'bgm_player'`

- [ ] **Step 3: 구현 — import·상수**

`preview_widget.py` import 수정:

```python
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    ...
)

from stampcut.core.bgm_sync import bgm_position
from stampcut.core.models import AudioMix, Clip, Settings
```

`_S = LAYOUT["square"]` 아래에:

```python
BGM_RESYNC_MS = 250  # 전체 미리보기와 BGM 위치 차이가 이보다 크면 다시 맞춘다
```

- [ ] **Step 4: 구현 — 생성자**

클래스 시그널에 추가:

```python
    clip_changed = Signal(object)
    style_changed = Signal()
    full_preview_requested = Signal()
    bgm_error = Signal(str)
```

`__init__`에서 `self._loaded_path: Path | None = None` 아래에 상태 추가:

```python
        self.audio_mix: AudioMix | None = None
        self._mode = "clip"
        self._full_path: Path | None = None
        self._full_signature: str | None = None
        self._full_stale = False
        self._full_duration_ms = 0
        self._bgm_loaded = ""
        self._bgm_duration_ms = 0
```

`self.video_item.nativeSizeChanged.connect(self._on_native_size)` 아래에 전체 모드용 비디오 아이템:

```python
        self.full_item = QGraphicsVideoItem()  # 전체 미리보기: 캔버스 전체 (이미 9:16으로 구워진 파일)
        self.full_item.setSize(QSizeF(L["canvas_w"], L["canvas_h"]))
        self.full_item.setPos(0, 0)
        self.full_item.setVisible(False)
        self.scene.addItem(self.full_item)
```

`self.player.setLoops(QMediaPlayer.Loops.Infinite)` 아래에:

```python
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.bgm_player = QMediaPlayer(self)
        self.bgm_audio = QAudioOutput(self)
        self.bgm_player.setAudioOutput(self.bgm_audio)
        self.bgm_player.durationChanged.connect(self._on_bgm_duration)
        self.bgm_player.errorOccurred.connect(lambda _err, msg: self.bgm_error.emit(msg))
```

`self.transport = QHBoxLayout()` 앞에 모드 줄 위젯:

```python
        self.clip_mode_btn = QPushButton("클립")
        self.clip_mode_btn.setCheckable(True)
        self.clip_mode_btn.setChecked(True)
        self.full_mode_btn = QPushButton("전체")
        self.full_mode_btn.setCheckable(True)
        self.full_mode_btn.setEnabled(False)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.clip_mode_btn)
        self.mode_group.addButton(self.full_mode_btn)
        self.clip_mode_btn.clicked.connect(self._on_clip_mode_clicked)
        self.full_mode_btn.clicked.connect(self._on_full_mode_clicked)
        self.make_full_btn = QPushButton("전체 미리보기 만들기")
        self.make_full_btn.clicked.connect(self.full_preview_requested)
        self.full_status = QLabel("")
        self.mode_row = QHBoxLayout()
        self.mode_row.addWidget(self.clip_mode_btn)
        self.mode_row.addWidget(self.full_mode_btn)
        self.mode_row.addWidget(self.make_full_btn)
        self.mode_row.addWidget(self.full_status, 1)
```

`layout` 조립을 다음으로:

```python
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addLayout(self.mode_row)
        layout.addLayout(self.transport)
        self._set_controls_enabled(False)
```

- [ ] **Step 5: 구현 — 종료·외부 API**

`shutdown`에 BGM 플레이어 정리 추가:

```python
        self.player.stop()
        self.player.setVideoOutput(None)
        self.player.setSource(QUrl())
        self.bgm_player.stop()
        self.bgm_player.setSource(QUrl())
```

`set_clip`를 다음으로 교체 (전체 모드에선 선택만 기억):

```python
    def set_clip(self, clip: Clip | None) -> None:
        self.clip = clip
        if self._mode == "full":
            return  # 전체 모드에선 선택만 기억하고, 클립 모드로 돌아올 때 반영한다
        self.player.stop()
        self._set_controls_enabled(clip is not None)
        if clip is None:
            self.video_item.setVisible(False)
            self._loaded_path = None
            self.player.setSource(QUrl())
            self.relayout()
            self._update_seek_range()
            return
        self.video_item.setVisible(True)
        self.sync_from_clip()
        self.refresh_media()
```

`refresh_media` 첫 줄에 가드 추가:

```python
    def refresh_media(self) -> None:
        if self._shut_down or self._mode == "full":
            return
```

`set_settings` 아래(외부 API 구역)에 추가:

```python
    # --- 전체 미리보기 ---
    def mode(self) -> str:
        return self._mode

    def full_signature(self) -> str | None:
        return self._full_signature

    def set_full_preview(self, path: Path, signature: str) -> None:
        self._full_path, self._full_signature, self._full_stale = path, signature, False
        self._full_duration_ms = 0
        self.full_mode_btn.setEnabled(True)
        self.full_status.setText("전체 미리보기 최신")
        self.set_mode("full")

    def mark_full_preview_stale(self) -> None:
        if self._full_path is not None and not self._full_stale:
            self._full_stale = True
            self.full_status.setText("클립이 바뀌었습니다 — 다시 만들기")

    def clear_full_preview(self) -> None:
        was_full = self._mode == "full"
        self._full_path = self._full_signature = None
        self._full_stale = False
        self._full_duration_ms = 0
        self.full_mode_btn.setEnabled(False)
        self.full_status.setText("")
        if was_full:
            self.set_mode("clip")

    def set_mode(self, mode: str) -> None:
        """"clip" 또는 "full". 전체 파일이 없으면 clip. 같은 모드로 다시 부르면 미디어를 다시 연다."""
        if mode == "full" and self._full_path is None:
            mode = "clip"
        self.player.stop()
        self.bgm_player.pause()
        self._mode = mode
        self.clip_mode_btn.setChecked(mode == "clip")
        self.full_mode_btn.setChecked(mode == "full")
        self.controls_panel.setEnabled(mode == "clip")
        if mode == "full":
            self.player.setLoops(1)
            self.player.setVideoOutput(self.full_item)
            self._loaded_path = self._full_path
            self.player.setSource(QUrl.fromLocalFile(str(self._full_path)))
            self.audio.setVolume(self.audio_mix.original_volume if self.audio_mix is not None else 1.0)
            self.relayout()
            self._set_controls_enabled(True)
            self._update_seek_range()
            self.player.play()
            self.play_btn.setText("⏸ 일시정지")
        else:
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            self.player.setVideoOutput(self.video_item)
            self.audio.setVolume(1.0)
            self._loaded_path = None
            self.set_clip(self.clip)

    def _on_clip_mode_clicked(self) -> None:
        if self._mode != "clip":
            self.set_mode("clip")
        else:
            self.clip_mode_btn.setChecked(True)

    def _on_full_mode_clicked(self) -> None:
        if self._mode != "full":
            self.set_mode("full")
        else:
            self.full_mode_btn.setChecked(True)

    # --- BGM 동기 재생 ---
    def set_audio_mix(self, mix: AudioMix | None) -> None:
        self.audio_mix = mix
        path = mix.bgm_path if mix is not None and mix.has_bgm() and Path(mix.bgm_path).is_file() else ""
        if path != self._bgm_loaded:
            self._bgm_loaded = path
            self._bgm_duration_ms = 0
            self.bgm_player.stop()
            self.bgm_player.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        self.bgm_audio.setVolume(mix.bgm_volume if mix is not None else 0.0)
        if self._mode == "full":
            self.audio.setVolume(mix.original_volume if mix is not None else 1.0)
            self._sync_bgm(self.player.position())

    def _on_bgm_duration(self, ms: int) -> None:
        self._bgm_duration_ms = max(0, ms)
        if self._mode == "full":
            self._sync_bgm(self.player.position())

    def _sync_bgm(self, t_ms: int) -> None:
        """영상 위치 t_ms에 맞춰 BGM을 재생/정지/재동기한다. 규칙은 bgm_position (렌더와 동일)."""
        playing = self._mode == "full" and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        mix = self.audio_mix
        if not playing or mix is None or not self._bgm_loaded:
            self.bgm_player.pause()
            return
        pos = bgm_position(t_ms / 1000, mix, self._bgm_duration_ms / 1000, self._full_duration_ms / 1000)
        if pos is None:
            self.bgm_player.pause()
            return
        target = int(pos * 1000)
        if self.bgm_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.bgm_player.setPosition(target)
            self.bgm_player.play()
        elif abs(self.bgm_player.position() - target) > BGM_RESYNC_MS:
            self.bgm_player.setPosition(target)
```

- [ ] **Step 6: 구현 — 배치·재생 핸들러**

`relayout` 끝부분의 가시성 처리를 전체 모드를 반영하도록 교체:

```python
        show_time = bool(clip) and s.show_time_in_caption
        if clip:
            self._place_text(self.time_item, format_time(clip.t), top=caption_y - TIME_GAP)
            self._place_text(self.caption_item, wrap(clip.caption, L["caption_font"], L["max_text_width"], L["max_lines"]), top=caption_y)
        full = self._mode == "full"
        self.title_item.setVisible(not full)
        self.time_item.setVisible(show_time and not full)
        self.caption_item.setVisible(bool(clip) and not full)
        self.square.setVisible(not full)
        self.full_item.setVisible(full)
```

`_window_ms`·`_on_position`·`_toggle_play`를 교체하고 `_on_duration`/`_on_media_status`를 추가:

```python
    def _window_ms(self) -> tuple[int, int]:
        if self._mode == "full":
            return 0, self._full_duration_ms
        clip = self.clip
        if clip is None or clip.preview_start is None:
            return 0, 0
        s = self.settings
        return (clip.start(s) - clip.preview_start) * 1000, (clip.end(s) - clip.preview_start) * 1000

    def _on_duration(self, ms: int) -> None:
        if self._mode == "full":
            self._full_duration_ms = max(0, ms)
            self._update_seek_range()

    def _on_media_status(self, status) -> None:
        if self._mode == "full" and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_btn.setText("▶ 재생")
            self.bgm_player.pause()

    def _on_seek(self, value: int) -> None:
        start, end = self._window_ms()
        if end:
            self.player.setPosition(start + value)
            self._sync_bgm(start + value)

    def _seek_by(self, delta_s: int) -> None:
        start, end = self._window_ms()
        if end:
            target = seek_target(self.player.position(), delta_s, start, end)
            self.player.setPosition(target)
            self._sync_bgm(target)

    def _on_position(self, ms: int) -> None:
        start, end = self._window_ms()
        if self._mode == "clip" and end and (ms >= end or ms < start - 500):
            self.player.setPosition(start)
            return
        if not self.seek_slider.isSliderDown():
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(min(max(0, ms - start), max(0, end - start)))
            self.seek_slider.blockSignals(False)
        self.pos_label.setText(f"{format_time(max(0, ms - start) // 1000)} / {format_time(max(0, end - start) // 1000)}")
        if self._mode == "full":
            self._sync_bgm(ms)

    def _toggle_play(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶ 재생")
            self.bgm_player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState and self._loaded_path is not None:
            self.player.play()
            self.play_btn.setText("⏸ 일시정지")
            self._sync_bgm(self.player.position())
        elif self._mode == "full":
            self.player.setPosition(0)
            self.player.play()
            self.play_btn.setText("⏸ 일시정지")
        else:
            self.refresh_media()
```

`_on_drag` 첫 줄에 가드:

```python
    def _on_drag(self, dx: float, dy: float) -> None:
        clip = self.clip
        if clip is None or self._mode == "full":
            return
```

- [ ] **Step 7: 통과 확인**

Run: `pytest tests/test_preview_widget.py -q`
Expected: 모두 PASS (기존 테스트 포함)

- [ ] **Step 8: 커밋**

```bash
git add stampcut/gui/preview_widget.py tests/test_preview_widget.py
git commit -m "feat(gui): full-preview mode in PreviewWidget with live BGM sync"
```

---

### Task 9: MainWindow 연결

**Files:**
- Modify: `stampcut/gui/main_window.py`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `BgmPanel` (Task 7), `PreviewWidget` 전체 모드 API (Task 8), `pipeline.render_preview`/`preview_signature` (Task 6)
- Produces: `MainWindow.bgm_panel`, `start_full_preview()`, signal `full_preview_done(object)`; `_set_busy`가 모드에 따라 `controls_panel`을 복원.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_main_window.py` import 수정:

```python
from stampcut.core.models import AudioMix, Project, Settings
```

파일 끝에 추가:

```python
def test_bgm_panel_wired_to_project_and_autosave(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    project, clip = _project_with_clip(make_video, make_clip)
    w = MainWindow(Settings(api_key="TEST"), project_file=tmp_path / "project.json")
    qtbot.addWidget(w)
    assert not w.bgm_panel.isEnabled()
    assert w.bgm_panel.parentWidget() is w.url_panel.parentWidget()  # 왼쪽 열
    w._adopt_project(project)
    assert w.bgm_panel.isEnabled() and w.bgm_panel.mix is project.audio
    w._autosave_timer.stop()
    with qtbot.waitSignal(w.bgm_panel.changed, timeout=1000):
        w.bgm_panel.original_slider.setValue(50)
    assert project.audio.original_volume == 0.5 and w._autosave_timer.isActive()
    assert w.preview.audio_mix is project.audio


def test_bgm_dir_change_saves_settings(qtbot, tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(main_window.settings_mod, "save", lambda s, path=None: saved.append(s.bgm_dir))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.bgm_panel.bgm_dir_changed.emit(str(tmp_path))
    assert w.settings.bgm_dir == str(tmp_path) and saved == [str(tmp_path)]


def test_full_preview_flow_and_stale_marking(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    from stampcut.core.ffmpeg import FfmpegPaths

    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    project, clip = _project_with_clip(make_video, make_clip)
    full = tmp_path / "full_1.mp4"

    def fake_render_preview(proj, s, paths, progress, cancel):
        progress("preview_render", 100, 100, "합치는 중")
        full.write_bytes(b"x")
        return full

    monkeypatch.setattr(main_window.pipeline, "render_preview", fake_render_preview)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    monkeypatch.setattr(w.preview.player, "setSource", lambda url: None)
    monkeypatch.setattr(w.preview.player, "play", lambda: None)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    w._adopt_project(project)
    with qtbot.waitSignal(w.full_preview_done, timeout=5000):
        w.preview.make_full_btn.click()
    assert w.preview.mode() == "full"
    assert w.preview.full_signature() == main_window.pipeline.preview_signature(project, w.settings)
    assert w.bgm_panel.isEnabled() and not w.preview.controls_panel.isEnabled()  # busy 해제 후에도 전체 모드에선 편집 잠금
    assert "준비됨" in w.status_panel.message.text()
    clip.caption = "바뀐 자막"
    w._on_clip_edited(clip)
    assert "다시 만들기" in w.preview.full_status.text()
    w._adopt_project(_project_with_clip(make_video, make_clip)[0])  # 새 분석 → 전체 미리보기 해제
    assert w.preview.mode() == "clip" and w.preview.full_signature() is None


def test_full_preview_failure_warns_with_hint(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    from stampcut.core.ffmpeg import FfmpegPaths

    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    def failing(proj, s, paths, progress, cancel):
        raise ValueError("미리보기가 준비되지 않은 클립: 3게임 12:38")

    monkeypatch.setattr(main_window.pipeline, "render_preview", failing)
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    w._adopt_project(_project_with_clip(make_video, make_clip)[0])
    w.start_full_preview()
    qtbot.waitUntil(lambda: bool(warned), timeout=5000)
    assert "12:38" in warned[0] and "다시 받기" in warned[0]
    assert w.bgm_panel.isEnabled()  # busy 해제됨


def test_full_preview_refused_without_clips_or_ffmpeg(qtbot, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    started = []
    monkeypatch.setattr(main_window.pipeline, "render_preview", lambda *a, **k: started.append(1))
    w = MainWindow(Settings(api_key="TEST"))
    qtbot.addWidget(w)
    w.start_full_preview()
    assert len(warned) == 1 and "클립" in warned[0]
    w.ffpaths = None
    _load_project(w, _project_with_clip(make_video, make_clip)[0])
    w.start_full_preview()
    assert len(warned) == 2 and "ffmpeg" in warned[1] and started == []


def test_render_refuses_missing_bgm_file(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    from PySide6.QtWidgets import QMessageBox

    from stampcut.core.ffmpeg import FfmpegPaths

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    rendered = []
    monkeypatch.setattr(main_window.pipeline, "render", lambda *a, **k: rendered.append(1))
    project, _ = _project_with_clip(make_video, make_clip)
    project.audio = AudioMix(bgm_path=str(tmp_path / "gone.mp3"))
    w = MainWindow(Settings(api_key="TEST", output_dir=str(tmp_path / "out")))
    qtbot.addWidget(w)
    w.ffpaths = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    _load_project(w, project)
    w.start_render()
    assert warned and "배경 음악" in warned[0] and rendered == []


def test_restore_fills_bgm_panel_and_busy_locks_it(qtbot, tmp_path, monkeypatch, make_video, make_clip):
    monkeypatch.setattr(MainWindow, "start_previews", lambda self, clips: None)
    pf = tmp_path / "project.json"
    project, _ = _project_with_clip(make_video, make_clip)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    project.audio = AudioMix(bgm_path=str(song), bgm_volume=0.4, bgm_offset=12.0)
    w = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w)
    w._adopt_project(project)
    w._flush_autosave()
    w2 = MainWindow(Settings(api_key="TEST"), project_file=pf)
    qtbot.addWidget(w2)
    assert w2.project.audio == project.audio
    assert w2.bgm_panel.file_combo.currentText() == "song.mp3" and w2.bgm_panel.bgm_slider.value() == 40
    assert w2.bgm_panel.offset_spin.value() == 12.0
    w2._set_busy(True)
    assert not w2.bgm_panel.isEnabled()
    w2._set_busy(False)
    assert w2.bgm_panel.isEnabled()
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_main_window.py -q`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'bgm_panel'`

- [ ] **Step 3: 구현 — import·시그널·생성자**

import 추가:

```python
from stampcut.gui.bgm_panel import BgmPanel
```

클래스 시그널:

```python
    analysis_done = Signal()
    render_done = Signal(object)
    full_preview_done = Signal(object)
```

`__init__`에서 `self.preview.style_changed.connect(self._on_style_changed)` 아래에:

```python
        self.preview.full_preview_requested.connect(self.start_full_preview)
        self.preview.bgm_error.connect(lambda msg: self.status_panel.set_idle(f"BGM 재생 불가: {msg}"))

        self.bgm_panel = BgmPanel()
        self.bgm_panel.set_bgm_dir(self.settings.bgm_dir)
        self.bgm_panel.changed.connect(self._on_audio_changed)
        self.bgm_panel.bgm_dir_changed.connect(self._on_bgm_dir_changed)
```

왼쪽 열 조립을 다음으로:

```python
        left_layout.addWidget(self.url_panel)
        style_box = QGroupBox("세부 설정")
        QVBoxLayout(style_box).addWidget(self.preview.controls_panel)
        left_layout.addWidget(style_box)
        left_layout.addWidget(self.bgm_panel)
        left_layout.addWidget(self.table, 1)
```

- [ ] **Step 4: 구현 — 설정·채택·편집 훅**

`apply_settings` 끝에 추가:

```python
        self.status_panel.update_summary(self.project, s)
        self.bgm_panel.set_bgm_dir(s.bgm_dir)
        self._check_full_preview()
```

`_on_style_changed`:

```python
    def _on_style_changed(self) -> None:
        # PreviewWidget이 공유 Settings 객체를 직접 고쳤으므로 저장만 하면 된다
        settings_mod.save(self.settings)
        self._check_full_preview()
```

`_adopt_project`에서 `self.preview.set_title(project.title)` 아래에:

```python
        self.preview.set_title(project.title)
        self.bgm_panel.set_mix(project.audio)
        self.preview.set_audio_mix(project.audio)
        self.preview.clear_full_preview()
```

`_on_clip_edited`·`_on_table_changed`·`_on_title_changed` 각각의 `self._schedule_autosave()` 앞에 `self._check_full_preview()` 한 줄 추가. `_on_output_dir_changed` 아래에 핸들러 추가:

```python
    def _on_audio_changed(self) -> None:
        if self.project:
            self.preview.set_audio_mix(self.project.audio)
            self._schedule_autosave()

    def _on_bgm_dir_changed(self, d: str) -> None:
        self.settings.bgm_dir = d
        settings_mod.save(self.settings)

    def _check_full_preview(self) -> None:
        sig = self.preview.full_signature()
        if sig is not None and self.project and sig != pipeline.preview_signature(self.project, self.settings):
            self.preview.mark_full_preview_stale()

    # --- 전체 미리보기 ---
    def start_full_preview(self) -> None:
        if not self.project or not self.project.enabled_clips():
            self._warn("켜진 클립이 없습니다.")
            return
        if self.ffpaths is None:
            self._warn(FFMPEG_MISSING)
            return
        self._set_busy(True)
        self.status_panel.set_idle("전체 미리보기 만드는 중")
        w = Worker(pipeline.render_preview, self.project, self.settings, self.ffpaths)
        w.signals.finished.connect(self._on_full_preview_done)
        w.signals.failed.connect(self._on_full_preview_failed)
        w.signals.cancelled.connect(lambda: self._set_busy(False))
        self._start(w)

    def _on_full_preview_done(self, path: Path) -> None:
        self._set_busy(False)
        assert self.project is not None
        self.preview.set_full_preview(path, pipeline.preview_signature(self.project, self.settings))
        self.status_panel.set_idle("전체 미리보기 준비됨 — BGM을 조절해 보세요")
        self.full_preview_done.emit(path)

    def _on_full_preview_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.status_panel.set_idle("전체 미리보기 실패")
        hint = "\n\n표에서 해당 행을 우클릭 → 미리보기 다시 받기 후 다시 시도하세요." if "준비되지 않은" in msg else ""
        self._warn(f"전체 미리보기를 만들지 못했습니다.\n{msg}{hint}")
```

- [ ] **Step 5: 구현 — 렌더 검증·busy·종료**

`start_render`에서 0초 클립 검사 다음에 추가:

```python
        audio = self.project.audio
        if audio.has_bgm() and not Path(audio.bgm_path).is_file():
            self._warn(f"배경 음악 파일을 찾을 수 없습니다:\n{audio.bgm_path}")
            return
```

`_set_busy`:

```python
    def _set_busy(self, busy: bool) -> None:
        self.url_panel.set_busy(busy)
        self.status_panel.set_busy(busy)
        self.table.setEnabled(not busy)
        self.preview.setEnabled(not busy)
        self.preview.controls_panel.setEnabled(not busy and self.preview.mode() == "clip")
        self.settings_action.setEnabled(not busy)
        self.bgm_panel.set_busy(busy)
```

`closeEvent`의 `self.preview.shutdown()` 앞에:

```python
        self._flush_autosave()
        self.bgm_panel.stop()
        self.preview.shutdown()
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_main_window.py -q`
Expected: 모두 PASS. 기존 `test_busy_locks_detached_controls_panel`(클립 모드에서 busy 해제 → 활성)도 유지.

- [ ] **Step 7: 전체 테스트**

Run: `pytest -q`
Expected: 전부 PASS, 실패 0

- [ ] **Step 8: 커밋**

```bash
git add stampcut/gui/main_window.py tests/test_main_window.py
git commit -m "feat(gui): wire BgmPanel, full preview worker and stale check into MainWindow"
```

---

### Task 10: 문서 + 실제 ffmpeg 믹스 통합 테스트

**Files:**
- Modify: `README.md` (사용법 3~4번 사이)
- Modify: `tests/test_integration_network.py` (파일 끝)

**Interfaces:**
- Consumes: `build_mix_command` (Task 4)

- [ ] **Step 1: 통합 테스트 작성**

`tests/test_integration_network.py` import에 추가:

```python
from stampcut.core.models import AudioMix, Clip, Project, Settings, VideoInfo
from stampcut.core.renderer import build_clip_command, build_mix_command
```

파일 끝에 추가 (유튜브 불필요, ffmpeg만 필요):

```python
def test_mix_bgm_over_generated_video(tmp_path, paths):
    """생성한 사인파 BGM을 짧은 테스트 영상에 섞는다. 곡(3초)이 구간(5.5초)보다 짧아 반복 경로도 탄다."""
    video = tmp_path / "video.mp4"
    ff.run([
        paths.ffmpeg, "-hide_banner",
        "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", video,
    ])
    song = tmp_path / "song.wav"
    ff.run([paths.ffmpeg, "-hide_banner", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3", song])
    audio = AudioMix(original_volume=0.5, bgm_path=str(song), bgm_volume=0.3, bgm_offset=1.0, bgm_start=0.5, bgm_end=None)
    out = tmp_path / "mixed.mp4"
    ff.run(build_mix_command(paths, video, audio, 6.0, out), total_seconds=6.0)
    info = ff.probe(paths, out)
    assert info.has_audio and abs(info.duration - 6.0) < 0.3 and (info.width, info.height) == (1080, 1920)
```

- [ ] **Step 2: 통합 테스트 실행**

Run: `pytest -m network tests/test_integration_network.py::test_mix_bgm_over_generated_video -v`
Expected: PASS (ffmpeg가 PATH에 있을 때; 없으면 skip)

- [ ] **Step 3: README 사용법 갱신**

`README.md`의 사용법 3번 항목 뒤(4번 "하이라이트 만들기" 앞)에 추가:

```markdown
   - **배경 음악**: 세부 설정 아래 "배경 음악"에서 **폴더…**로 음원 폴더를 고르면 드롭다운에 파일이 나열되고(폴더 밖 파일은 **찾아보기…**), 원본/BGM 볼륨, 음원 시작점(곡의 몇 초부터), 영상 구간(몇 초~몇 초, 끝 0 = 끝까지)을 정합니다. **▶ BGM만 듣기**로 곡만 확인할 수 있습니다. 곡이 구간보다 짧으면 처음부터 반복됩니다.
   - **전체 미리보기**: 미리보기 창의 **전체 미리보기 만들기**를 누르면 수십 초 안에 최종과 같은 화면의 저해상도 영상이 만들어지고 **전체** 모드로 재생됩니다. 이때 BGM이 설정한 위치·볼륨으로 함께 들리며, 슬라이더를 움직이면 즉시 반영됩니다(재생성 불필요). 클립을 고치면 "다시 만들기" 표시가 뜹니다. **클립** 모드로 돌아가면 컷별 편집을 계속할 수 있습니다.
```

4번 항목의 문장을 다음으로 바꾼다:

```markdown
4. **하이라이트 만들기** → 출력 폴더에 `타이틀.mp4`가 생깁니다(배경 음악은 마지막 단계에서 섞입니다). **폴더 열기 / 재생**으로 확인합니다.
```

"편집 내용(…)은 자동으로 저장되며" 문장에 `배경 음악 설정`을 포함시킨다:

```markdown
- 편집 내용(URL·타이틀·컷별 자막/앞뒤/줌/위치·배경 음악 설정)은 자동으로 저장되며, 앱을 다시 켜면 마지막 작업이 그대로 복원됩니다. 새로 "댓글 분석"을 돌리면 새 작업으로 바뀝니다.
```

- [ ] **Step 4: 전체 테스트 재확인**

Run: `pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add README.md tests/test_integration_network.py
git commit -m "docs: BGM and full-preview usage; test(network): real ffmpeg BGM mix"
```

---

## 실행 후 확인 (수동)

1. `python -m stampcut` → 이전 작업이 복원되고 "배경 음악" 패널이 세부 설정 아래에 보인다.
2. 폴더… → mp3 폴더 선택 → 드롭다운에 파일 → 하나 선택 → ▶ BGM만 듣기 → 볼륨 슬라이더로 즉시 변함.
3. 전체 미리보기 만들기 → 상태줄 진행 → "전체" 모드로 재생, 타이틀/자막이 구워져 보이고 BGM이 영상 구간에서만 들림. 음원 시작 값을 바꾸면 곧바로 다른 지점이 들림.
4. 자막 하나 수정 → "클립이 바뀌었습니다 — 다시 만들기".
5. 하이라이트 만들기 → 상태줄에 "배경 음악 섞는 중" → 결과 mp4에 BGM이 섞여 있고 첫 1초 페이드인, 끝 2초 페이드아웃.
6. BGM 파일을 지운 뒤 하이라이트 만들기 → 경고창, 다운로드 시작 안 함.
