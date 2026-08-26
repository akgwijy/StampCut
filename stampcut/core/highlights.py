"""타임스탬프 언급을 묶어 하이라이트 후보 클립을 만든다."""
from __future__ import annotations

import math
from collections import defaultdict

from stampcut.core.models import Clip, Mention, Settings, VideoInfo


def clip_likes(clip: Clip) -> int:
    return sum(m.like_count for m in clip.mentions)


def _score(mentions: list[Mention]) -> float:
    authors = {m.author for m in mentions}
    likes = sum(m.like_count for m in mentions)
    return len(authors) + 0.1 * math.log1p(likes)


def _pick_caption(mentions: list[Mention]) -> str:
    cands = [m for m in mentions if m.caption]
    if not cands:
        return ""
    return max(cands, key=lambda m: (m.like_count, len(m.caption))).caption


def _cluster(mentions: list[Mention], window: float) -> list[list[Mention]]:
    groups: list[list[Mention]] = []
    cur: list[Mention] = []
    for m in sorted(mentions, key=lambda m: m.seconds):
        if cur and m.seconds - cur[-1].seconds > window:
            groups.append(cur)
            cur = []
        cur.append(m)
    if cur:
        groups.append(cur)
    return groups


def _join_captions(*captions: str) -> str:
    out: list[str] = []
    for c in captions:
        for part in c.split(" · "):
            if part and part not in out:
                out.append(part)
    return " · ".join(out)


def _merge(a: Clip, b: Clip, s: Settings) -> Clip:
    mentions = a.mentions + b.mentions
    return Clip(
        video=a.video,
        t=a.t,
        mentions=mentions,
        score=_score(mentions),
        caption=_join_captions(a.caption, b.caption),
        pre=a.pre,
        post=(b.t - a.t) + b.effective_post(s),
    )


def _merge_overlaps(clips: list[Clip], s: Settings) -> list[Clip]:
    clips = sorted(clips, key=lambda c: c.t)
    changed = True
    while changed:
        changed = False
        out: list[Clip] = []
        for c in clips:
            if out and out[-1].end(s) > c.start(s):
                out[-1] = _merge(out[-1], c, s)
                changed = True
            else:
                out.append(c)
        clips = out
    return clips


def apply_length_limit(clips: list[Clip], s: Settings) -> None:
    order = sorted(clips, key=lambda c: (-c.score, -clip_likes(c), c.video.index, c.t))
    total = 0
    for c in order:
        d = c.duration(s)
        if total + d <= s.max_total_seconds:
            c.enabled, c.over_limit = True, False
            total += d
        else:
            c.enabled, c.over_limit = False, True


def build_clips(mentions: list[Mention], videos: list[VideoInfo], s: Settings) -> list[Clip]:
    by_id = {v.video_id: v for v in videos}
    grouped: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        grouped[m.video_id].append(m)
    clips: list[Clip] = []
    for vid, ms in grouped.items():
        video = by_id[vid]
        vclips = [
            Clip(video=video, t=g[0].seconds, mentions=g, score=_score(g), caption=_pick_caption(g))
            for g in _cluster(ms, s.cluster_window_seconds)
        ]
        clips.extend(_merge_overlaps(vclips, s))
    apply_length_limit(clips, s)
    clips.sort(key=lambda c: (c.video.index, c.t))
    return clips
