"""채널 영상 찾기: 채널 해석 → 댓글 있는 영상 목록, 영상별 댓글. GUI는 이 함수들을 Worker에서 부른다."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable

from stampcut.core import ffmpeg as ff
from stampcut.core.models import KST, ChannelInfo, RawComment, VideoInfo
from stampcut.core.timestamps import comment_has_timestamp
from stampcut.core.youtube_api import short_name_from_title

ProgressFn = Callable[[str, int, int, str], None]


def _noop(stage: str, done: int, total: int, message: str) -> None:
    pass


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ff.Cancelled()


@dataclass
class ChannelPage:
    channel: ChannelInfo
    videos: list[VideoInfo]
    next_token: str | None  # 더 보기용. None이면 마지막 페이지


def find_channel_videos(
    client,
    text: str,
    limit: int = 200,
    page_token: str | None = None,
    channel: ChannelInfo | None = None,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
) -> ChannelPage:
    """channel이 없으면 text로 채널을 찾고, 업로드 목록에서 댓글 있는 영상을 limit개까지 받는다."""
    if hasattr(client, "cancel_event"):
        client.cancel_event = cancel
    if channel is None:
        progress("channel", 0, 2, "채널 찾는 중")
        channel = client.resolve_channel(text)
    _check(cancel)
    progress("channel", 1, 2, f"{channel.title} 영상 목록 받는 중")
    videos, next_token = client.fetch_channel_videos(channel, limit=limit, page_token=page_token)
    progress("channel", 2, 2, f"{channel.title} — 댓글 있는 영상 {len(videos)}개")
    return ChannelPage(channel, videos, next_token)


def find_channel_videos_in_period(
    client,
    text: str,
    start: date,
    end: date,
    progress: ProgressFn = _noop,
    cancel: threading.Event | None = None,
) -> ChannelPage:
    """KST 게시일이 시작일~종료일인 댓글 있는 영상을 전체 업로드 목록에서 찾는다."""
    if not text.strip():
        raise ValueError("채널 주소를 입력해 주세요")
    if start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다")
    lower = datetime.combine(start, time.min, tzinfo=KST)
    upper = datetime.combine(end, time.min, tzinfo=KST) + timedelta(days=1)
    if hasattr(client, "cancel_event"):
        client.cancel_event = cancel
    _check(cancel)
    progress("channel", 0, 1, "채널 찾는 중")
    resolved = client.resolve_channel(text)
    _check(cancel)
    progress("channel", 0, 1, f"{resolved.title} 영상 목록 받는 중")
    found: dict[str, VideoInfo] = {}
    token = None
    while True:
        _check(cancel)
        videos, token = client.fetch_channel_videos(resolved, limit=50, page_token=token)
        _check(cancel)
        for video in videos:
            if lower <= video.published_at < upper:
                found.setdefault(video.video_id, video)
        progress("channel", 0, 1, f"{resolved.title} — 기간 내 댓글 있는 영상 {len(found)}개 찾음")
        if not token:
            break
    ordered = sorted(found.values(), key=lambda video: (video.published_at, video.video_id))
    for index, video in enumerate(ordered):
        video.index = index
        video.short_name = short_name_from_title(video.title, index)
    progress("channel", 1, 1, f"{resolved.title} — 기간 내 댓글 있는 영상 {len(ordered)}개")
    return ChannelPage(resolved, ordered, None)


def load_comments(client, video: VideoInfo, progress: ProgressFn = _noop, cancel: threading.Event | None = None) -> list[RawComment]:
    """영상의 댓글 전부. 타임스탬프 댓글이 앞에 오고, 그 안에서는 좋아요 많은 순. 댓글이 막힌 영상은 []."""
    if hasattr(client, "cancel_event"):
        client.cancel_event = cancel
    progress("comments", 0, 1, f"{video.short_name} 댓글 받는 중")
    _check(cancel)
    comments = client.fetch_all_comments(video.video_id)
    ordered = sorted(comments, key=lambda c: (not comment_has_timestamp(c, video), -c.like_count))
    progress("comments", 1, 1, f"{video.short_name} 댓글 {len(ordered)}개")
    return ordered
