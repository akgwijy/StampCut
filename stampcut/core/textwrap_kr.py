"""한글 폭 추정과 줄바꿈. ffmpeg drawtext는 자동 줄바꿈이 없어서 미리 넣는다."""
from __future__ import annotations

WIDE_RATIO = 1.0
NARROW_RATIO = 0.55
ELLIPSIS = "…"


def is_wide(ch: str) -> bool:
    o = ord(ch)
    return (
        0x1100 <= o <= 0x11FF  # 한글 자모
        or 0x2E80 <= o <= 0xA4CF  # 한자·가나·호환 자모 등
        or 0xAC00 <= o <= 0xD7A3  # 한글 음절
        or 0xF900 <= o <= 0xFAFF
        or 0xFE30 <= o <= 0xFE4F
        or 0xFF00 <= o <= 0xFF60  # 전각
    )


def text_width(text: str, font_size: float) -> float:
    return sum((WIDE_RATIO if is_wide(c) else NARROW_RATIO) * font_size for c in text)


def _fit_prefix(word: str, font_size: float, max_width: float) -> tuple[str, str]:
    acc = ""
    for i, ch in enumerate(word):
        if text_width(acc + ch, font_size) > max_width:
            return acc, word[i:]
        acc += ch
    return acc, ""


def wrap(text: str, font_size: float, max_width: float = 960.0, max_lines: int = 2) -> str:
    words = text.split()
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(words):
        w = words[i]
        candidate = f"{cur} {w}" if cur else w
        if text_width(candidate, font_size) <= max_width:
            cur = candidate
            i += 1
            continue
        if cur:
            lines.append(cur)
            cur = ""
            continue
        head, tail = _fit_prefix(w, font_size, max_width)
        if not head:
            break
        lines.append(head)
        words[i] = tail
        if not tail:
            i += 1
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and text_width(last + ELLIPSIS, font_size) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + ELLIPSIS
    return "\n".join(lines)
