from stampcut.core.highlights import apply_length_limit, build_clips
from stampcut.core.models import Mention, Settings


def mention(video, secs, caption="골", author="@a", likes=0, cid="c"):
    return Mention(video.video_id, secs, caption, cid, author, likes, False)


def test_cluster_within_window_and_split_beyond(make_video):
    v = make_video()
    ms = [mention(v, 100), mention(v, 104, author="@b"), mention(v, 130)]
    clips = build_clips(ms, [v], Settings())
    assert [c.t for c in clips] == [100, 130]
    assert len(clips[0].mentions) == 2 and clips[0].score == 2


def test_chain_clustering_uses_gap_to_previous(make_video):
    v = make_video()
    clips = build_clips([mention(v, 100), mention(v, 105), mention(v, 110)], [v], Settings())
    assert [c.t for c in clips] == [100]


def test_score_counts_distinct_authors_and_likes(make_video):
    v = make_video()
    ms = [mention(v, 100, author="@a", likes=3), mention(v, 101, author="@a", likes=1)]
    clip = build_clips(ms, [v], Settings())[0]
    assert 1.15 < clip.score < 1.17


def test_caption_prefers_likes_then_length_and_skips_empty(make_video):
    v = make_video()
    ms = [mention(v, 100, caption=""), mention(v, 101, caption="골"), mention(v, 102, caption="원더골", likes=0)]
    assert build_clips(ms, [v], Settings())[0].caption == "원더골"
    ms2 = [mention(v, 100, caption="짧", likes=5), mention(v, 101, caption="길게 쓴 자막")]
    assert build_clips(ms2, [v], Settings())[0].caption == "짧"


def test_overlapping_windows_merge(make_video):
    v = make_video()
    s = Settings()
    ms = [mention(v, 100, caption="종범 골"), mention(v, 112, caption="역습", author="@b")]
    clips = build_clips(ms, [v], s)
    assert len(clips) == 1
    c = clips[0]
    assert (c.t, c.start(s), c.end(s)) == (100, 97, 127)
    assert c.caption == "종범 골 · 역습"
    assert c.score == 2


def test_merge_chain_and_dedupe_caption(make_video):
    v = make_video()
    s = Settings()
    ms = [mention(v, 100, caption="선방"), mention(v, 112, caption="선방"), mention(v, 124, caption="골")]
    clips = build_clips(ms, [v], s)
    assert len(clips) == 1
    assert clips[0].end(s) == 139 and clips[0].caption == "선방 · 골"


def test_length_limit_marks_over_limit(make_video):
    v = make_video(duration=10000)
    s = Settings(max_total_seconds=40)
    ms = [mention(v, 100, author="@a"), mention(v, 100, author="@b"), mention(v, 500), mention(v, 900)]
    clips = build_clips(ms, [v], s)
    assert [(c.t, c.enabled, c.over_limit) for c in clips] == [(100, True, False), (500, True, False), (900, False, True)]


def test_length_limit_respects_clamped_duration(make_video):
    v = make_video(duration=110)
    s = Settings(max_total_seconds=31)  # 18초(2~20) + 13초(97~110, 끝에서 잘림) = 31
    clips = build_clips([mention(v, 5), mention(v, 100)], [v], s)
    assert [c.enabled for c in clips] == [True, True]


def test_final_order_by_url_index_then_time(make_video):
    v0 = make_video(index=0, video_id="A")
    v1 = make_video(index=1, video_id="B")
    ms = [mention(v1, 50), mention(v0, 900), mention(v0, 30)]
    clips = build_clips(ms, [v0, v1], Settings())
    assert [(c.video.video_id, c.t) for c in clips] == [("A", 30), ("A", 900), ("B", 50)]


def test_apply_length_limit_reenables_when_room(make_video, make_clip):
    v = make_video(duration=10000)
    s = Settings(max_total_seconds=100)
    clips = [make_clip(v, 10, enabled=False, over_limit=True), make_clip(v, 500)]
    apply_length_limit(clips, s)
    assert all(c.enabled and not c.over_limit for c in clips)
