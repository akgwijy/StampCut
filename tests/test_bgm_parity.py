"""미리보기(bgm_position)와 렌더(build_mix_command)가 같은 BGM 타임라인을 쓰는지 맞춰 본다."""
import re

import pytest

from stampcut.core.bgm_sync import bgm_position
from stampcut.core.ffmpeg import FfmpegPaths
from stampcut.core.models import AudioMix
from stampcut.core.renderer import build_mix_command

D, TOTAL = 120.0, 200.0
TIMES = [0.0, 3.0, 9.99, 10.0, 15.0, 99.99, 100.0, 150.0, 199.9]


def _mix_timeline(cmd):
    """필터 문자열에서 (음원 시작, 구간 길이, 영상 내 시작)을 읽는다. BGM 입력이 없으면 None."""
    fc = cmd[cmd.index("-filter_complex") + 1]
    m = re.search(r"atrim=start=([\d.]+),asetpts=PTS-STARTPTS,atrim=duration=([\d.]+).*?adelay=(\d+)\|", fc)
    return (float(m.group(1)), float(m.group(2)), int(m.group(3)) / 1000) if m else None


@pytest.mark.parametrize(
    "audio",
    [
        AudioMix(bgm_path="s.mp3", bgm_offset=30.0, bgm_start=10.0, bgm_end=100.0),
        AudioMix(bgm_path="s.mp3", bgm_offset=125.0, bgm_start=0.0, bgm_end=None),  # offset > 곡 길이
        AudioMix(bgm_path="s.mp3", bgm_offset=-5.0, bgm_start=3.0, bgm_end=None),  # 음수 offset
        AudioMix(bgm_path="s.mp3", bgm_offset=0.0, bgm_start=50.0, bgm_end=20.0),  # 끝 < 시작 → BGM 없음
        AudioMix(bgm_path="s.mp3", bgm_offset=0.0, bgm_start=10.0, bgm_end=10.3),  # 0.5초 미만 → BGM 없음
        AudioMix(bgm_path="s.mp3", bgm_offset=0.0, bgm_start=199.8, bgm_end=300.0),  # 영상 끝에 잘려 0.2초
    ],
)
def test_preview_rule_matches_mix_command(tmp_path, audio):
    cmd = build_mix_command(FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"), tmp_path / "c.mp4", audio, TOTAL, tmp_path / "o.mp4")
    timeline = _mix_timeline(cmd)
    for t in TIMES:
        pos = bgm_position(t, audio, D, TOTAL)
        if timeline is None:
            assert pos is None, (audio, t)
            continue
        offset, section, start = timeline
        e = t - start
        if 0 <= e < section:
            assert pos is not None and abs(pos - (offset + e) % D) < 1e-6, (audio, t)
        else:
            assert pos is None, (audio, t)
