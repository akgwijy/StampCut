"""댓글 텍스트에서 타임스탬프와 자막 문구를 뽑는다."""
from __future__ import annotations

import re

from stampcut.core.models import Mention, RawComment, VideoInfo

# 12:38 / 1:02:33  (앞뒤에 숫자가 붙어 있으면 제외)
_COLON_RE = re.compile(r"(?<!\d)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?!\d)")
# 1시간 2분 3초 / 12분 38초 / 12분 / 2시간
_HANGUL_RE = re.compile(
    r"(?<!\d)(?:(\d{1,2})\s*시간\s*)?(\d{1,3})\s*분(?:\s*(\d{1,2})\s*초)?"
    r"|(?<!\d)(\d{1,2})\s*시간(?!\s*\d{1,3}\s*분)"
)
_TRIM_CHARS = " \t-:,~·|()[]"


def _hms(h: int, m: int, s: int, *, hour_given: bool) -> int | None:
    if s >= 60:
        return None
    if hour_given and m >= 60:
        return None
    return h * 3600 + m * 60 + s


def find_timestamps(line: str, max_seconds: int) -> list[tuple[int, tuple[int, int]]]:
    """줄에서 (초, (시작, 끝) 위치) 목록을 등장 순서로 돌려준다."""
    found: list[tuple[int, tuple[int, int]]] = []
    for m in _COLON_RE.finditer(line):
        h = int(m.group(1)) if m.group(1) else 0
        secs = _hms(h, int(m.group(2)), int(m.group(3)), hour_given=bool(m.group(1)))
        if secs is not None and secs <= max_seconds:
            found.append((secs, m.span()))
    for m in _HANGUL_RE.finditer(line):
        if m.group(4):
            secs = int(m.group(4)) * 3600
        else:
            h = int(m.group(1)) if m.group(1) else 0
            s = int(m.group(3)) if m.group(3) else 0
            secs = _hms(h, int(m.group(2)), s, hour_given=bool(m.group(1)))
        if secs is not None and secs <= max_seconds:
            found.append((secs, m.span()))
    found.sort(key=lambda x: x[1][0])
    return found


def _caption_without(line: str, spans: list[tuple[int, int]]) -> str:
    chars = list(line)
    for a, b in sorted(spans, reverse=True):
        del chars[a:b]
    text = " ".join("".join(chars).split())
    return text.strip(_TRIM_CHARS).strip()


def extract_mentions(video: VideoInfo, comment: RawComment) -> list[Mention]:
    out: list[Mention] = []
    seen: set[int] = set()
    for line in comment.text.splitlines():
        found = find_timestamps(line, video.duration)
        if not found:
            continue
        caption = _caption_without(line, [span for _, span in found])
        for secs, _ in found:
            if secs in seen:
                continue
            seen.add(secs)
            out.append(
                Mention(
                    video_id=video.video_id,
                    seconds=secs,
                    caption=caption,
                    comment_id=comment.id,
                    author=comment.author,
                    like_count=comment.like_count,
                    is_reply=comment.is_reply,
                )
            )
    return out


def format_time(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
