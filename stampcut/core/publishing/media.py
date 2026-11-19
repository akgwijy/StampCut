from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from dataclasses import dataclass,replace
from fractions import Fraction
from pathlib import Path
from stampcut.core import ffmpeg
from stampcut.core import settings as settings_mod
from .models import PublishError,check_cancel,noop

INSTAGRAM_MAX_BYTES=300_000_000

@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration: float
    size: int
    has_audio: bool
    fps: float=30.0
    video_codec: str="h264"
    audio_codec: str="aac"
    audio_bitrate: int=128000


def _frame_rate(stream):
    for name in ("avg_frame_rate", "r_frame_rate"):
        value=stream.get(name)
        if value in (None,"","N/A","0/0"):
            continue
        fps=float(Fraction(value))
        if not math.isfinite(fps) or fps<=0:
            raise ValueError("invalid frame rate")
        return fps
    raise ValueError("missing frame rate")


def _video_duration(stream,container,fps):
    value=stream.get("duration")
    if value not in (None,"","N/A"):
        return float(value)
    ticks=stream.get("duration_ts")
    time_base=stream.get("time_base")
    if ticks not in (None,"","N/A") and time_base not in (None,"","N/A"):
        return float(Fraction(str(ticks))*Fraction(time_base))
    frames=stream.get("nb_frames")
    if frames not in (None,"","N/A"):
        return int(frames)/fps
    return float(container.get("duration") or 0)


def inspect_video(video:Path,paths=None)->MediaInfo:
    paths=paths or ffmpeg.find_ffmpeg()
    if paths is None:
        raise PublishError("ffmpeg/ffprobe 경로를 설정해야 업로드 영상을 확인할 수 있습니다.")
    try:
        response=subprocess.run([str(paths.ffprobe),"-v","error","-show_streams","-show_format","-of","json",str(video)],capture_output=True,text=True,encoding="utf-8",errors="replace",creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),timeout=30)
        if response.returncode:
            raise ValueError("probe failed")
        data=json.loads(response.stdout)
        stream=next(s for s in data["streams"] if s.get("codec_type")=="video")
        audio=next((s for s in data["streams"] if s.get("codec_type")=="audio"),{})
        # Reuse display-rotation parsing independently of the container's audio
        # duration. All eligibility timing below comes from the video if present.
        geometry=ffmpeg.parse_probe_json(json.dumps({"streams":data["streams"],"format":{"duration":1}}))
        fps=_frame_rate(stream)
        duration=_video_duration(stream,data.get("format",{}),fps)
        size=Path(video).stat().st_size
        if min(geometry.width,geometry.height,size)<=0 or not math.isfinite(duration) or duration<=0:
            raise ValueError("invalid media")
        bitrate=audio.get("bit_rate")
        audio_bitrate=int(bitrate) if bitrate not in (None,"","N/A") else 0
        return MediaInfo(geometry.width,geometry.height,duration,size,geometry.has_audio,fps,stream.get("codec_name",""),audio.get("codec_name",""),audio_bitrate)
    except Exception:
        raise PublishError("영상 정보를 확인하지 못했습니다. 재생 가능한 MP4/MOV 파일인지 확인하세요.") from None


def validate_media(info:MediaInfo,platform:str,method="resumable"):
    if platform not in ("youtube","instagram"):
        raise PublishError("지원하지 않는 업로드 대상입니다.")
    if not all(math.isfinite(value) and value>0 for value in (info.width,info.height,info.duration,info.fps,info.size)):
        raise PublishError("영상의 크기·길이·프레임 정보를 확인할 수 없습니다.")
    if platform=="youtube":
        if info.width>info.height or info.duration>180.001:
            raise PublishError("유튜브 쇼츠는 세로 또는 정사각형이며 3분 이내인 영상이어야 합니다.")
        if info.size>256*1024**3:
            raise PublishError("유튜브 업로드 파일 크기 제한을 초과했습니다.")
    else:
        if method not in ("resumable","unified","url"):
            raise PublishError("인스타그램 업로드 방식을 선택하세요.")
        if not 3<=info.duration<=900:
            raise PublishError("인스타그램 릴스는 3초~15분 길이의 영상이어야 합니다.")
        if method=="url":
            if info.size>INSTAGRAM_MAX_BYTES or info.width>1920 or not 23<=info.fps<=60 or info.video_codec not in ("h264","hevc"):
                raise PublishError("공개 URL 영상은 릴스 규격(H264/HEVC, 23~60fps, 가로 1920px 이하, 300MB 이하)에 맞아야 합니다.")
            if info.has_audio and (info.audio_codec!="aac" or info.audio_bitrate>128000*1.05):
                raise PublishError("공개 URL 영상의 오디오는 AAC 128kbps 이하로 준비하세요. 파일 직접 전송 방식은 앱에서 변환합니다.")


def _compatible_instagram(info,source):
    # AAC's requested 128k target may probe slightly higher on short clips due
    # to packet rounding and encoder rate variation. Validation allows the
    # same 5% nominal-rate tolerance for generated and public-URL files.
    if not info.has_audio or not 0<info.audio_bitrate<=128000*1.05:
        return False
    try:
        validate_media(replace(info,audio_bitrate=128000),"instagram","url")
    except PublishError:
        return False
    return (info.video_codec=="h264" and info.has_audio and info.audio_codec=="aac"
            and info.width<=1920 and info.height<=1920 and info.width%2==info.height%2==0
            and abs(info.fps-30)<0.001 and abs(info.duration-source.duration)<=0.05)


def prepare_instagram(video:Path,paths,cache_key:str,progress=noop,cancel=None)->Path:
    paths=paths or ffmpeg.find_ffmpeg()
    if paths is None:
        raise PublishError("인스타그램용 영상 준비에 ffmpeg가 필요합니다.")
    check_cancel(cancel)
    root=settings_mod.cache_dir()/"release"/cache_key
    root.mkdir(parents=True,exist_ok=True)
    output=root/"instagram-v1.mp4"
    source=inspect_video(video,paths)
    validate_media(source,"instagram")
    check_cancel(cancel)
    if output.is_file():
        try:
            cached=inspect_video(output,paths)
        except PublishError:
            pass
        else:
            if _compatible_instagram(cached,source):
                check_cancel(cancel)
                return output
    temp=root/("preparing-"+uuid.uuid4().hex+".mp4")
    cmd=[str(paths.ffmpeg),"-hide_banner","-i",str(video)]
    if not source.has_audio:
        cmd += ["-f","lavfi","-i","anullsrc=r=48000:cl=stereo"]
    cmd += ["-map","0:v:0","-map","0:a:0" if source.has_audio else "1:a:0","-map_metadata","-1","-vf","scale=1920:1920:force_original_aspect_ratio=decrease:force_divisible_by=2","-c:v","libx264","-preset","fast","-crf","20","-maxrate","25M","-bufsize","50M","-pix_fmt","yuv420p","-r","30","-x264-params","open-gop=0","-c:a","aac","-b:a","128k","-ar","48000","-ac","2","-t",str(source.duration),"-movflags","+faststart",str(temp)]
    try:
        ffmpeg.run(cmd,on_progress=lambda fraction:progress("prepare",int(fraction*100),100,"인스타그램용 영상 준비 중"),cancel=cancel,total_seconds=source.duration)
        check_cancel(cancel)
        prepared=inspect_video(temp,paths)
        if prepared.size>INSTAGRAM_MAX_BYTES:
            raise PublishError("인스타그램용 파일이 300MB를 넘습니다. 영상 길이를 줄여 주세요.")
        if not _compatible_instagram(prepared,source):
            raise PublishError("변환된 영상이 인스타그램 릴스 규격에 맞지 않습니다. 영상 길이와 ffmpeg 설정을 확인하세요.")
        check_cancel(cancel)
        os.replace(temp,output)
        return output
    except ffmpeg.Cancelled:
        raise
    except PublishError:
        raise
    except Exception:
        raise PublishError("인스타그램용 영상을 준비하지 못했습니다. ffmpeg 설정과 여유 공간을 확인하세요.") from None
    finally:
        temp.unlink(missing_ok=True)
