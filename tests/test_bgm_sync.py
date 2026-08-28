from stampcut.core.bgm_sync import bgm_position
from stampcut.core.models import AudioMix


def mix(**kw):
    base = dict(bgm_path="song.mp3", bgm_offset=30.0, bgm_start=10.0, bgm_end=100.0)
    base.update(kw)
    return AudioMix(**base)


def test_outside_section_is_none():
    assert bgm_position(9.9, mix(), 120.0, 200.0) is None
    assert bgm_position(100.0, mix(), 120.0, 200.0) is None  # 끝은 포함하지 않음


def test_inside_first_pass_adds_offset():
    assert bgm_position(10.0, mix(), 120.0, 200.0) == 30.0
    assert bgm_position(50.0, mix(), 120.0, 200.0) == 70.0


def test_wraps_to_song_start_after_first_pass():
    # 곡 120초, offset 30 → 첫 재생은 90초 분량. 그 뒤엔 곡 처음부터.
    m = mix(bgm_end=None)
    assert bgm_position(100.0, m, 120.0, 400.0) == 0.0  # e = 90
    assert bgm_position(105.0, m, 120.0, 400.0) == 5.0
    assert bgm_position(10.0 + 90 + 120 + 7, m, 120.0, 400.0) == 7.0  # 두 번째 반복


def test_end_none_uses_total():
    assert bgm_position(150.0, mix(bgm_end=None), 120.0, 160.0) is not None
    assert bgm_position(160.0, mix(bgm_end=None), 120.0, 160.0) is None


def test_offset_beyond_duration_is_normalized():
    assert bgm_position(10.0, mix(bgm_offset=125.0), 120.0, 200.0) == 5.0


def test_no_bgm_or_zero_duration():
    assert bgm_position(20.0, AudioMix(), 120.0, 200.0) is None
    assert bgm_position(20.0, mix(), 0.0, 200.0) is None
