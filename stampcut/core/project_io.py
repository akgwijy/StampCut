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
    preview_start = int(d["preview_start"]) if d.get("preview_start") is not None else None
    preview_end = int(d["preview_end"]) if d.get("preview_end") is not None else None
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
        pre=int(d["pre"]) if d.get("pre") is not None else None,
        post=int(d["post"]) if d.get("post") is not None else None,
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
