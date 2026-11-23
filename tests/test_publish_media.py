from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from stampcut.core import ffmpeg
from stampcut.core.publishing import media
from stampcut.core.publishing.models import PublishError


PATHS = ffmpeg.FfmpegPaths(Path("ffmpeg"), Path("ffprobe"))
GOOD = media.MediaInfo(1080, 1920, 10, 1024, True)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "original.mp4"
    path.write_bytes(b"original-source-must-be-preserved")
    return path


def probe_fixture(**video_fields):
    video = {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
             "duration": "180.000", "avg_frame_rate": "30000/1001"}
    video.update(video_fields)
    return {"streams": [video, {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000", "duration": "180.045"}],
            "format": {"duration": "180.045"}}


def fake_probe(monkeypatch, payload, returncode=0):
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=returncode, stdout=json.dumps(payload)))


def test_inspection_uses_video_duration_instead_of_aac_tail(monkeypatch, source):
    fake_probe(monkeypatch, probe_fixture())
    info = media.inspect_video(source, PATHS)
    assert info.duration == 180.0 and info.fps == pytest.approx(29.97002997)
    assert info.size == source.stat().st_size
    media.validate_media(info, "youtube")


def test_missing_stream_duration_uses_video_timebase_not_container_tail(monkeypatch, source):
    fake_probe(monkeypatch, probe_fixture(duration="N/A", duration_ts=5400000, time_base="1/30000"))
    info = media.inspect_video(source, PATHS)
    assert info.duration == 180.0
    media.validate_media(info, "youtube")


def test_missing_timestamps_uses_video_frame_count_before_container(monkeypatch, source):
    fake_probe(monkeypatch, probe_fixture(duration=None, nb_frames="5400", avg_frame_rate="30/1"))
    info = media.inspect_video(source, PATHS)
    assert info.duration == 180.0
    media.validate_media(info, "youtube")


def test_unknown_average_frame_rate_falls_back_to_real_rate(monkeypatch, source):
    fake_probe(monkeypatch, probe_fixture(avg_frame_rate="0/0", r_frame_rate="30/1"))
    assert media.inspect_video(source, PATHS).fps == 30.0


@pytest.mark.parametrize("fields", [{"width": 0}, {"height": -1}, {"duration": "nan"}, {"duration": "inf"},
                                  {"duration": "-1"}, {"avg_frame_rate": "0/1"}, {"avg_frame_rate": "-30/1"},
                                  {"avg_frame_rate": "nan"}, {"avg_frame_rate": "1/0"}])
def test_malformed_stream_geometry_timing_and_fps_are_rejected(monkeypatch, source, fields):
    fake_probe(monkeypatch, probe_fixture(**fields))
    with pytest.raises(PublishError):
        media.inspect_video(source, PATHS)


@pytest.mark.parametrize("payload", [[], {}, {"streams": []}, {"streams": [{"codec_type": "audio"}]}])
def test_malformed_probe_shape_is_reported_as_actionable_error(monkeypatch, source, payload):
    fake_probe(monkeypatch, payload)
    with pytest.raises(PublishError, match="영상"):
        media.inspect_video(source, PATHS)


def test_failed_probe_is_not_used(monkeypatch, source):
    fake_probe(monkeypatch, probe_fixture(), returncode=1)
    with pytest.raises(PublishError):
        media.inspect_video(source, PATHS)


@pytest.mark.parametrize("info", [replace(GOOD, width=0), replace(GOOD, height=0), replace(GOOD, duration=float("nan")),
                                 replace(GOOD, duration=0), replace(GOOD, fps=0), replace(GOOD, size=0)])
def test_validation_rejects_invalid_basic_measurements_for_both_platforms(info):
    for platform in ("youtube", "instagram"):
        with pytest.raises(PublishError):
            media.validate_media(info, platform)


@pytest.mark.parametrize("duration", [0.001, 180.0, 180.001])
def test_shorts_accept_square_or_portrait_up_to_three_minutes(duration):
    media.validate_media(replace(GOOD, width=1080, height=1080, duration=duration), "youtube")
    media.validate_media(replace(GOOD, duration=duration), "youtube")


@pytest.mark.parametrize("info", [replace(GOOD, width=1920, height=1080), replace(GOOD, duration=180.002)])
def test_shorts_reject_landscape_and_over_three_minutes(info):
    with pytest.raises(PublishError):
        media.validate_media(info, "youtube")


@pytest.mark.parametrize("duration", [2.999, 900.001])
def test_instagram_rejects_duration_outside_three_seconds_to_fifteen_minutes(duration):
    with pytest.raises(PublishError):
        media.validate_media(replace(GOOD, duration=duration), "instagram")


@pytest.mark.parametrize("duration", [3.0, 900.0])
def test_instagram_direct_upload_can_prepare_landscape_and_noncompliant_codecs(duration):
    media.validate_media(replace(GOOD, width=3840, height=2160, duration=duration, fps=120,
                                 video_codec="vp9", audio_codec="opus", audio_bitrate=192000), "instagram")


@pytest.mark.parametrize("field,value", [("size", 300_000_001), ("width", 1921), ("fps", 22.9), ("fps", 60.1),
                                        ("video_codec", "vp9"), ("audio_codec", "mp3"), ("audio_bitrate", 192000)])
def test_url_upload_refuses_media_that_cannot_be_converted_remotely(field, value):
    with pytest.raises(PublishError):
        media.validate_media(replace(GOOD, **{field: value}), "instagram", "url")


def test_instagram_size_limit_is_decimal_300mb():
    media.validate_media(replace(GOOD, size=300_000_000), "instagram", "url")
    with pytest.raises(PublishError):
        media.validate_media(replace(GOOD, size=300_000_001), "instagram", "url")


def test_unknown_target_and_transport_are_rejected():
    with pytest.raises(PublishError):
        media.validate_media(GOOD, "unknown")
    with pytest.raises(PublishError):
        media.validate_media(GOOD, "instagram", "unknown")


def configure_preparation(monkeypatch, tmp_path, source_info=GOOD, cached_info=None, produced_info=GOOD):
    monkeypatch.setattr(media.settings_mod, "cache_dir", lambda: tmp_path / "cache")
    runs = []
    def inspect(path, paths):
        if path.name == "original.mp4":
            return source_info
        if path.name == "instagram-v2.mp4":
            if isinstance(cached_info, Exception):
                raise cached_info
            return cached_info or GOOD
        return produced_info
    def run(command, **kwargs):
        runs.append((command, kwargs))
        Path(command[-1]).write_bytes(b"prepared-output")
    monkeypatch.setattr(media, "inspect_video", inspect)
    monkeypatch.setattr(media.ffmpeg, "run", run)
    return runs


def cached_output(tmp_path):
    output = tmp_path / "cache" / "release" / "source-sha" / "instagram-v2.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"cached-output")
    return output


def test_compatible_cached_file_is_reused_without_touching_source(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path, cached_info=GOOD)
    output = cached_output(tmp_path)
    original = source.read_bytes()
    result = media.prepare_instagram(source, PATHS, "source-sha")
    assert result == output and output.read_bytes() == b"cached-output" and runs == []
    assert source.read_bytes() == original


def test_legacy_cache_is_not_used_for_instagram_retries(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path)
    legacy = tmp_path / "cache" / "release" / "source-sha" / "instagram-v1.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"old-container")

    output = media.prepare_instagram(source, PATHS, "source-sha")

    assert output != legacy and output.read_bytes() == b"prepared-output"
    assert legacy.read_bytes() == b"old-container" and len(runs) == 1


@pytest.mark.parametrize("cached", [PublishError("corrupt cache"), replace(GOOD, video_codec="vp9"),
                                    replace(GOOD, audio_codec="mp3"), replace(GOOD, audio_bitrate=192000),
                                    replace(GOOD, width=3840), replace(GOOD, pixel_aspect=2), replace(GOOD, fps=120), replace(GOOD, duration=3.0),
                                    replace(GOOD, has_audio=False), replace(GOOD, size=300_000_001)])
def test_stale_or_incompatible_cache_is_rebuilt_atomically(monkeypatch, tmp_path, source, cached):
    runs = configure_preparation(monkeypatch, tmp_path, cached_info=cached)
    output = cached_output(tmp_path)
    before = source.read_bytes()
    result = media.prepare_instagram(source, PATHS, "source-sha")
    assert result == output and output.read_bytes() == b"prepared-output" and len(runs) == 1
    assert source.read_bytes() == before and not list(output.parent.glob("preparing-*.mp4"))


def test_conversion_sets_required_codecs_and_does_not_modify_source(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path)
    before = source.read_bytes()
    output = media.prepare_instagram(source, PATHS, "source-sha")
    command = runs[0][0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
    assert "faststart" in command[command.index("-movflags") + 1].split("+")
    assert command[command.index("-use_editlist") + 1] == "0"
    assert output != source and source.read_bytes() == before


def test_oversize_source_is_blocked_before_conversion(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path, source_info=replace(GOOD, width=3840))
    with pytest.raises(PublishError, match="해상도"):
        media.prepare_instagram(source, PATHS, "source-sha")
    assert not runs


def test_smaller_cached_copy_is_rebuilt(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path, cached_info=replace(GOOD, width=720, height=1280))
    cached_output(tmp_path)
    media.prepare_instagram(source, PATHS, "source-sha")
    assert len(runs) == 1


def test_silent_source_gets_an_audio_track(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path, source_info=replace(GOOD, has_audio=False))
    media.prepare_instagram(source, PATHS, "source-sha")
    command = runs[0][0]
    assert "anullsrc=r=48000:cl=stereo" in command and "1:a:0" in command


@pytest.mark.parametrize("bad_output", [replace(GOOD, size=300_000_001), replace(GOOD, duration=1), replace(GOOD, video_codec="vp9")])
def test_failed_output_validation_does_not_promote_bad_conversion(monkeypatch, tmp_path, source, bad_output):
    configure_preparation(monkeypatch, tmp_path, produced_info=bad_output)
    with pytest.raises(PublishError):
        media.prepare_instagram(source, PATHS, "source-sha")
    assert not list((tmp_path / "cache").rglob("*.mp4"))


@pytest.mark.parametrize("late", [False, True])
def test_cancellation_cleans_partial_output_and_preserves_source(monkeypatch, tmp_path, source, late):
    configure_preparation(monkeypatch, tmp_path)
    original = source.read_bytes()
    cancel = threading.Event()
    def interrupted(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial-output")
        cancel.set()
        if not late:
            raise ffmpeg.Cancelled()
    monkeypatch.setattr(media.ffmpeg, "run", interrupted)
    with pytest.raises(ffmpeg.Cancelled):
        media.prepare_instagram(source, PATHS, "source-sha", cancel=cancel)
    assert source.read_bytes() == original
    assert not list((tmp_path / "cache").rglob("*.mp4"))


def test_pre_cancelled_preparation_never_probes_or_creates_cache(monkeypatch, tmp_path, source):
    runs = configure_preparation(monkeypatch, tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ffmpeg.Cancelled):
        media.prepare_instagram(source, PATHS, "source-sha", cancel=cancel)
    assert runs == [] and not (tmp_path / "cache").exists()


@pytest.fixture
def local_ffmpeg():
    paths = ffmpeg.find_ffmpeg()
    if paths is None:
        pytest.skip("local ffmpeg and ffprobe are unavailable")
    return paths


def synthetic_video(paths, output, *, duration=3.2, audio_duration=3.2, size="1280x720", fps=30):
    command = [str(paths.ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               f"color=c=blue:s={size}:r={fps}:d={duration}", "-f", "lavfi", "-i",
               f"sine=frequency=440:sample_rate=48000:duration={audio_duration}", "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-y", str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=45,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_local_preparation_transcodes_192k_audio_and_verifies_faststart(local_ffmpeg, tmp_path, record_property):
    source = tmp_path / "synthetic-source.mp4"
    synthetic_video(local_ffmpeg, source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    source_info = media.inspect_video(source, local_ffmpeg)
    assert source_info.audio_bitrate > 128000
    output = media.prepare_instagram(source, local_ffmpeg, before)
    info = media.inspect_video(output, local_ffmpeg)
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    assert info.audio_bitrate <= 132000 and 3 <= info.duration <= 900
    assert (info.width, info.height, info.fps) == (1280, 720, 30)
    assert info.size <= 300_000_000 and output != source
    binary = output.read_bytes()
    assert 0 <= binary.find(b"moov") < binary.find(b"mdat")
    trace = subprocess.run(
        [str(local_ffmpeg.ffprobe), "-v", "trace", "-show_entries", "format=format_name", str(output)],
        check=True, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert b"type:'moov'" in trace.stderr and b"type:'elst'" not in trace.stderr
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    previous_mtime = output.stat().st_mtime_ns
    assert media.prepare_instagram(source, local_ffmpeg, before) == output
    assert output.stat().st_mtime_ns == previous_mtime
    metrics = {"source_audio_bitrate": source_info.audio_bitrate, "video_codec": info.video_codec,
               "audio_codec": info.audio_codec, "audio_bitrate": info.audio_bitrate, "duration": info.duration,
               "width": info.width, "height": info.height, "fps": info.fps, "bytes": info.size,
               "faststart": True, "source_unchanged": True, "cache_reused": True}
    record_property("media_qa", json.dumps(metrics))
    print("MEDIA_QA " + json.dumps(metrics))


def test_local_three_minute_short_ignores_longer_aac_track(local_ffmpeg, tmp_path, record_property):
    source = tmp_path / "synthetic-three-minute-short.mp4"
    synthetic_video(local_ffmpeg, source, duration=180, audio_duration=180.045, size="96x160", fps=1)
    info = media.inspect_video(source, local_ffmpeg)
    media.validate_media(info, "youtube")
    assert info.duration == 180
    raw = subprocess.run([str(local_ffmpeg.ffprobe), "-v", "error", "-show_format", "-of", "json", str(source)],
                         check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    container_duration = float(json.loads(raw.stdout)["format"]["duration"])
    assert container_duration > 180.001
    metrics = {"video_duration": info.duration, "container_duration": container_duration, "shorts_accepted": True}
    record_property("duration_qa", json.dumps(metrics))
    print("DURATION_QA " + json.dumps(metrics))


def test_nominal_128k_encoder_output_can_be_used_as_public_url_input():
    # The local FFmpeg integration measured128098bps for an explicit128k target.
    info=media.MediaInfo(1080,1920,3.2,64349,True,audio_bitrate=128098)
    media.validate_media(info,"instagram","url")


def test_instagram_file_preparation_keeps_shorts_resolution_and_duration(local_ffmpeg, tmp_path):
    source = tmp_path / "highlight.mp4"
    synthetic_video(local_ffmpeg, source, size="1080x1920")
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()

    output = media.prepare_instagram(source, local_ffmpeg, fingerprint)
    info = media.inspect_video(output, local_ffmpeg)

    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(3.2, abs=0.05)
