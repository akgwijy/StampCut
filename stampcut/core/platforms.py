"""OS 판별과 OS별 기본값. 다른 core 모듈·Qt에 의존하지 않는다."""
from __future__ import annotations

import sys
from typing import Literal

Platform = Literal["windows", "macos", "linux"]


def current() -> Platform:
    """호출 시점의 sys.platform으로 판별한다 (테스트에서 monkeypatch 가능)."""
    p = sys.platform
    if p.startswith(("win", "cygwin")):
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def default_output_dir() -> str:
    return "~/Movies/StampCut" if current() == "macos" else "~/Videos/StampCut"
