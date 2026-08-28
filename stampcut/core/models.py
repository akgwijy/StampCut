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
    title_y: int = 210  # 타이틀 블록 세로 중심 (기존 420px 밴드 중앙과 동일)
    title_color: str = "#FFFFFF"
    caption_y: int = 1552  # 자막 블록 상단
    caption_color: str = "#FFFFFF"
    bgm_dir: str = ""  # 마지막으로 고른 배경 음악 폴더


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
class ChannelInfo:
    channel_id: str
    title: str
    uploads_playlist_id: str  # 채널 업로드 재생목록 (UU…)


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
    audio: AudioMix = field(default_factory=AudioMix)

    def enabled_clips(self) -> list[Clip]:
        return [c for c in self.clips if c.enabled]

    def total_duration(self, s: Settings) -> int:
        return sum(c.duration(s) for c in self.enabled_clips())
