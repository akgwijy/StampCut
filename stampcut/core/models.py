"""StampCut 데이터 모델. Qt 의존 없음."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


@dataclass
class Settings:
    api_key: str = ""
    pre_seconds: int = 3
    post_seconds: int = 15
    max_total_seconds: int = 180
    cluster_window_seconds: float = 5.0
    output_dir: str = "~/Videos/StampCut"
    title_template: str = "{date} {channel} 하이라이트"
    background_color: str = "#000000"
    font_path: str = ""
    show_time_in_caption: bool = True
    ffmpeg_path: str = ""
    parallel_downloads: int = 2
    preview_margin_pre: int = 30
    preview_margin_post: int = 60


@dataclass
class VideoInfo:
    index: int
    video_id: str
    url: str
    title: str
    short_name: str
    channel_title: str
    published_at: datetime
    duration: int
    comment_count: int


@dataclass
class RawComment:
    id: str
    text: str
    author: str
    like_count: int
    is_reply: bool


@dataclass
class Mention:
    video_id: str
    seconds: int
    caption: str
    comment_id: str
    author: str
    like_count: int
    is_reply: bool


class ClipStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    READY = "ready"
    ERROR = "error"


@dataclass
class Clip:
    video: VideoInfo
    t: int
    mentions: list[Mention]
    score: float
    caption: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pre: int | None = None
    post: int | None = None
    enabled: bool = True
    over_limit: bool = False
    zoom: float = 1.0
    pan_x: float = 0.5
    pan_y: float = 0.5
    status: ClipStatus = ClipStatus.PENDING
    error: str = ""
    preview_path: Path | None = None
    preview_start: int | None = None
    preview_end: int | None = None
    final_path: Path | None = None

    def effective_pre(self, s: Settings) -> int:
        return s.pre_seconds if self.pre is None else self.pre

    def effective_post(self, s: Settings) -> int:
        return s.post_seconds if self.post is None else self.post

    def start(self, s: Settings) -> int:
        return max(0, self.t - self.effective_pre(s))

    def end(self, s: Settings) -> int:
        return min(self.video.duration, self.t + self.effective_post(s))

    def duration(self, s: Settings) -> int:
        return max(0, self.end(s) - self.start(s))


@dataclass
class Project:
    urls: list[str]
    title: str
    videos: list[VideoInfo]
    clips: list[Clip]
    warnings: list[str] = field(default_factory=list)

    def enabled_clips(self) -> list[Clip]:
        return [c for c in self.clips if c.enabled]

    def total_duration(self, s: Settings) -> int:
        return sum(c.duration(s) for c in self.enabled_clips())
