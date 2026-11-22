# 플랫폼별 동작 (Windows / macOS / Linux) 설계

2026-08-30

## 배경

StampCut은 Windows에서 개발됐지만 코드 대부분이 크로스플랫폼이다(PySide6, yt-dlp, `subprocess`).
macOS에서 소스로 실행해 보면 GUI·테스트는 정상이지만 다음이 Windows 전제로 굳어 있다.

| 항목 | 현재 | 문제 |
|---|---|---|
| 설정·캐시·로그 위치 | `%APPDATA%`, `%LOCALAPPDATA%` 없으면 홈 폴더 | macOS/Linux에서 `~/StampCut/`에 생김 |
| 기본 출력 폴더 | `~/Videos/StampCut` | macOS는 `~/Movies` |
| ffmpeg 탐색 | 앱 폴더 `bin/ffmpeg.exe`, 설정 폴더 + `ffmpeg.exe`, PATH | 맥 번들은 `bin/ffmpeg`; Finder로 실행하면 PATH에 Homebrew가 없어 못 찾음 |
| 안내 문구 | `winget install Gyan.FFmpeg`, `ffmpeg.exe` 파일 필터 | 맥에서는 틀린 안내, 파일 선택 불가 |
| 폰트 폴백 | `C:\Windows\Fonts\malgunbd.ttf` | 동봉 폰트가 있어 실제로는 안 타지만 OS별로 맞춤 |

실행파일은 어차피 OS별로 따로 빌드하지만, **같은 소스가 각 OS에서 올바르게 동작**해야
빌드 결과물도 올바르다. 패키징만 OS별이고 코드는 한 벌을 유지한다.

## 범위

- `stampcut/core/platforms.py` (신규): OS 판별과 OS별 기본값. Qt·다른 core 모듈에 의존하지 않는다.
  - `current() -> Literal["windows", "macos", "linux"]` — `sys.platform`을 호출 시점에 읽는다(테스트에서 monkeypatch).
  - `default_output_dir() -> str` — Windows/Linux `~/Videos/StampCut`, macOS `~/Movies/StampCut`.
- `stampcut/core/settings.py`
  - `config_dir()`: Windows `%APPDATA%\StampCut`; macOS `~/Library/Application Support/StampCut`; Linux `$XDG_CONFIG_HOME/StampCut` (기본 `~/.config`).
  - `data_dir()`: Windows `%LOCALAPPDATA%\StampCut`; macOS `~/Library/Application Support/StampCut` (config와 같은 폴더); Linux `$XDG_DATA_HOME/StampCut` (기본 `~/.local/share`).
  - Windows에서 환경변수가 없으면 지금처럼 홈 폴더로 폴백.
  - `bundled_font_path()` 폴백: Windows `malgunbd.ttf`, macOS `/System/Library/Fonts/AppleSDGothicNeo.ttc`, Linux `/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc`.
- `stampcut/core/models.py`: `Settings.output_dir` 기본값을 `platforms.default_output_dir()`로.
- `stampcut/core/ffmpeg.py`
  - `exe_name(base)`: Windows만 `.exe`를 붙인다.
  - `find_ffmpeg()` 후보 순서: ① 앱 폴더 `bin/<exe>` ② 설정 경로(파일 또는 폴더/`<exe>`) ③ PATH ④ **OS별 기본 설치 폴더** — macOS `/opt/homebrew/opt/ffmpeg-full/bin`, `/usr/local/opt/ffmpeg-full/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `/opt/local/bin`; Linux `/usr/bin`, `/usr/local/bin`; Windows 없음.
  - 후보 중 **필수 필터(`drawtext`)를 갖춘 첫 번째**를 고른다. 갖춘 게 없으면 첫 후보를 돌려주고 호출자가 `missing_filters()`로 안내한다 (다운로드·클립 미리보기는 drawtext 없이도 되므로 `None`으로 만들지 않는다).
  - `missing_filters(ffmpeg)`: `ffmpeg -filters` 출력에서 빠진 필수 필터. 경로별 `lru_cache`라 설정 창에서 글자마다 다시 실행되지 않는다. 실행 실패(빈 파일 등)는 전부 빠진 것으로 본다.
  - `install_hint()`: Windows `winget install Gyan.FFmpeg`, macOS `brew install ffmpeg-full`, Linux `sudo apt install ffmpeg`.
  - 배경: Homebrew는 2026-01에 `ffmpeg` 포뮬러를 슬림화해 libfreetype(drawtext)을 뺐고, 전체 빌드는 keg-only인 `ffmpeg-full`로 옮겼다 ([homebrew-core#261303](https://github.com/Homebrew/homebrew-core/pull/261303)).
- GUI
  - `main_window.py`: `_ffmpeg_problem()` — 없으면 `FFMPEG_MISSING`, 필터가 빠졌으면 `FFMPEG_NO_DRAWTEXT`, 정상이면 `None`. 시작 상태줄·분석 후·복원 후·전체 미리보기·렌더에서 공통으로 쓴다.
  - `settings_dialog.py`: 상태 라벨이 빨강(없음)/주황(있지만 drawtext 없음)/초록(정상). 문구·파일 필터·플레이스홀더를 위 함수로 조합. "PowerShell" 대신 "터미널".
- README: 설치 절을 Windows/macOS로 나누고 문제 해결 표·로그 경로를 OS별로.

## 범위 밖

- 기존 `~/StampCut/` (맥에서 폴백으로 생긴 폴더) 마이그레이션 — 아직 맥 사용자가 없다.
- PyInstaller 스펙·빌드 스크립트.
- `yt-dlp 업데이트` 버튼(`sys.executable -m pip`)은 이미 크로스플랫폼.

## 테스트

- `tests/test_platforms.py`: `current()` 매핑, `default_output_dir()`.
- `tests/test_settings.py`: OS별 디렉터리(환경변수·`Path.home` monkeypatch), 폰트 폴백, `app_dir()` 테스트를 플랫폼 중립 경로로.
- `tests/test_ffmpeg.py`: 기존 탐색 테스트는 Windows 고정; macOS에서 `.exe` 없는 이름·기본 설치 폴더 폴백; `install_hint()`.
- 기존 `APPDATA`/`LOCALAPPDATA`에 기대는 테스트(`test_project_io`, `test_app`)는 Windows로 고정.
