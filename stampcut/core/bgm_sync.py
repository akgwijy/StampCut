"""BGM 재생 위치 계산. 미리보기(Qt)와 렌더(ffmpeg)가 같은 규칙을 쓴다. Qt 의존 없음."""
from __future__ import annotations

from stampcut.core.models import AudioMix


def bgm_position(t: float, audio: AudioMix, bgm_duration: float, total: float) -> float | None:
    """영상 시각 t(초)에 들려야 할 음원 위치(초). 구간 밖·BGM 없음·길이 0이면 None.

    첫 재생은 bgm_offset부터 곡 끝까지, 이후엔 곡 처음부터 반복 (renderer.build_mix_command와 동일).
    """
    if not audio.has_bgm() or bgm_duration <= 0:
        return None
    end = audio.bgm_end if audio.bgm_end is not None else total
    if t < audio.bgm_start or t >= end:
        return None
    e = t - audio.bgm_start
    offset = max(0.0, audio.bgm_offset) % bgm_duration  # 음수는 0으로 (build_mix_command와 동일)
    first = bgm_duration - offset
    if e < first:
        return offset + e
    return (e - first) % bgm_duration
