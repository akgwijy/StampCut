from stampcut.core.models import ClipStatus, Project, Settings


def test_clip_uses_settings_defaults(make_video, make_clip):
    clip = make_clip(make_video(), t=758)
    s = Settings()
    assert (clip.start(s), clip.end(s), clip.duration(s)) == (755, 773, 18)


def test_clip_explicit_override(make_video, make_clip):
    clip = make_clip(make_video(), t=758, pre=5, post=20)
    s = Settings()
    assert (clip.start(s), clip.end(s), clip.duration(s)) == (753, 778, 25)


def test_clip_clamped_at_video_bounds(make_video, make_clip):
    s = Settings()
    early = make_clip(make_video(), t=1)
    late = make_clip(make_video(duration=1449), t=1440)
    assert early.start(s) == 0 and early.duration(s) == 16
    assert late.end(s) == 1449 and late.duration(s) == 12


def test_clip_defaults(make_video, make_clip):
    clip = make_clip(make_video(), t=10)
    assert clip.enabled is True
    assert (clip.zoom, clip.pan_x, clip.pan_y) == (1.0, 0.5, 0.5)
    assert clip.status is ClipStatus.PENDING
    assert clip.over_limit is False
    assert len(clip.id) == 32


def test_project_total_duration_counts_enabled_only(make_video, make_clip):
    v = make_video()
    a = make_clip(v, t=100)
    b = make_clip(v, t=300, enabled=False)
    p = Project(urls=[v.url], title="t", videos=[v], clips=[a, b])
    assert p.enabled_clips() == [a]
    assert p.total_duration(Settings()) == 18


def test_audio_mix_flags():
    from stampcut.core.models import AudioMix

    assert AudioMix().is_default() and not AudioMix().has_bgm()
    assert not AudioMix(original_volume=0.5).is_default()
    a = AudioMix(bgm_path="x.mp3")
    assert a.has_bgm() and not a.is_default()
    assert Project([], "t", [], []).audio == AudioMix()
