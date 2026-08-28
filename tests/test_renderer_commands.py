from pathlib import Path

from stampcut.core.ffmpeg import FfmpegPaths, ProbeInfo
from stampcut.core.models import AudioMix, Settings
from stampcut.core.renderer import (
    PREVIEW_PROFILE,
    build_clip_command,
    build_concat_command,
    build_mix_command,
    ff_color,
    ff_path,
    sanitize_filename,
    unique_output_path,
    write_concat_list,
)


def paths(tmp_path):
    return FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")


def probe(has_audio=True):
    return ProbeInfo(1920, 1080, 18.0, has_audio)


def build(tmp_path, clip, settings=None, has_audio=True, title="26.08.20 문성FC 하이라이트"):
    clip.final_path = tmp_path / "final.mp4"
    return build_clip_command(paths(tmp_path), clip, settings or Settings(), title, probe(has_audio), tmp_path, 3, tmp_path / "font.otf")


def filter_complex(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_basic_command(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(), t=758, caption="원더골")
    cmd, out = build(tmp_path, clip)
    fc = filter_complex(cmd)
    assert out == tmp_path / "clip_003.mp4" and cmd[-1] == str(out)
    assert "[0:v]scale=1920:1080,pad=1920:1080:0:0:color=0x000000,crop=1080:1080:420:0[sq]" in fc
    assert "color=c=0x000000:s=1080x1920:r=30:d=18[bg]" in fc
    assert "[bg][sq]overlay=0:420[c0]" in fc
    assert fc.count("drawtext=") == 3
    assert "fontsize=64" in fc and "fontsize=36" in fc and "fontsize=60" in fc
    assert "fontcolor=0xFFD60A" in fc and "y=1510" in fc and "y=1552" in fc and "borderw=4" in fc
    assert "[0:a]afade=t=in:d=0.2,afade=t=out:st=17.80:d=0.2[a]" in fc
    assert "anullsrc" not in " ".join(cmd)
    assert cmd[cmd.index("-t") + 1] == "18"
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and cmd[cmd.index("-crf") + 1] == "18"
    assert (tmp_path / "clip_003_caption.txt").read_text("utf-8") == "원더골"
    assert (tmp_path / "clip_003_time.txt").read_text("utf-8") == "12:38"
    assert (tmp_path / "clip_003_title.txt").read_text("utf-8") == "26.08.20 문성FC 하이라이트"


def test_silent_source_adds_anullsrc(tmp_path, make_video, make_clip):
    cmd, _ = build(tmp_path, make_clip(make_video(), t=758), has_audio=False)
    assert "anullsrc=r=48000:cl=stereo" in cmd and cmd[cmd.index("anullsrc=r=48000:cl=stereo") - 1] == "-i"
    assert "[1:a]afade" in filter_complex(cmd)


def test_optional_time_and_empty_caption(tmp_path, make_video, make_clip):
    cmd, _ = build(tmp_path, make_clip(make_video(), t=758, caption=""), Settings(show_time_in_caption=False))
    fc = filter_complex(cmd)
    assert fc.count("drawtext=") == 1 and "null[v]" in fc


def test_zoom_and_pan_reach_filter(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(), t=758, zoom=2.0, pan_x=0.0, pan_y=1.0)
    fc = filter_complex(build(tmp_path, clip)[0])
    assert "scale=3840:2160,pad=3840:2160:0:0:color=0x000000,crop=1080:1080:0:1080" in fc


def test_background_color_from_settings(tmp_path, make_video, make_clip):
    fc = filter_complex(build(tmp_path, make_clip(make_video(), t=10), Settings(background_color="#112233"))[0])
    assert "color=0x112233" in fc and "color=c=0x112233" in fc


def test_ff_path_and_color():
    assert ff_path(Path(r"C:\Users\me\f.txt")) == "C\\:/Users/me/f.txt"
    assert ff_color("#FFD60A") == "0xFFD60A"


def test_sanitize_and_unique(tmp_path):
    assert sanitize_filename('a/b:c*?"<>|') == "a_b_c______"
    assert sanitize_filename("  ") == "highlight"
    (tmp_path / "t.mp4").write_bytes(b"")
    (tmp_path / "t (2).mp4").write_bytes(b"")
    assert unique_output_path(tmp_path, "t") == tmp_path / "t (3).mp4"
    assert unique_output_path(tmp_path, "새 파일") == tmp_path / "새 파일.mp4"


def test_concat_list_and_command(tmp_path):
    files = [tmp_path / "a.mp4", tmp_path / "b's.mp4"]
    lst = write_concat_list(files, tmp_path)
    lines = lst.read_text("utf-8").splitlines()
    assert lines[0] == "file '" + str(files[0]).replace("\\", "/") + "'"
    assert "'\\''" in lines[1]
    cmd = build_concat_command(paths(tmp_path), lst, tmp_path / "out.mp4")
    assert cmd[1:7] == ["-hide_banner", "-f", "concat", "-safe", "0", "-i"] and cmd[-1].endswith("out.mp4")
    assert "copy" in cmd


def test_custom_text_style_from_settings(tmp_path, make_video, make_clip):
    s = Settings(title_y=300, title_color="#ff8800", caption_y=1400, caption_color="#00ff00")
    fc = filter_complex(build(tmp_path, make_clip(make_video(), t=758), s)[0])
    assert "y=300-text_h/2" in fc and "fontcolor=0xff8800" in fc  # 타이틀: 세로 중심 기준
    assert "y=1400" in fc and "fontcolor=0x00ff00" in fc          # 자막: 상단 기준
    assert "y=1358" in fc                                          # 시간: 자막 위 42px


def test_style_y_clamped_to_canvas(tmp_path, make_video, make_clip):
    s = Settings(title_y=-50, caption_y=99999)
    fc = filter_complex(build(tmp_path, make_clip(make_video(), t=758), s)[0])
    assert "y=0-text_h/2" in fc and "y=1920" in fc and "y=1878" in fc


def test_preview_profile_scales_layout_and_speeds_encode(tmp_path, make_video, make_clip):
    clip = make_clip(make_video(), t=758, caption="원더골")
    clip.preview_path = tmp_path / "preview.mp4"
    cmd, out = build_clip_command(
        paths(tmp_path), clip, Settings(), "제목", probe(), tmp_path, 3, tmp_path / "font.otf",
        profile=PREVIEW_PROFILE, source=clip.preview_path, in_offset=7,
    )
    fc = filter_complex(cmd)
    assert cmd[cmd.index("-ss") + 1] == "7" and cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == str(clip.preview_path)
    assert out == tmp_path / "clip_003.mp4"
    assert "crop=540:540" in fc and "s=540x960" in fc and "overlay=0:210" in fc
    assert "fontsize=32" in fc and "fontsize=18" in fc and "fontsize=30" in fc
    assert "y=105-text_h/2" in fc and "y=755" in fc and "y=776" in fc and "borderw=2" in fc
    assert cmd[cmd.index("-preset") + 1] == "ultrafast" and cmd[cmd.index("-crf") + 1] == "28"
    assert (tmp_path / "clip_003_caption.txt").read_text("utf-8") == "원더골"


def test_default_profile_has_no_seek_and_uses_final_path(tmp_path, make_video, make_clip):
    cmd, _ = build(tmp_path, make_clip(make_video(), t=758))
    assert "-ss" not in cmd and cmd[cmd.index("-i") + 1] == str(tmp_path / "final.mp4")
    assert cmd[cmd.index("-preset") + 1] == "medium"


def test_mix_command_with_bgm(tmp_path):
    song = tmp_path / "song.mp3"
    audio = AudioMix(original_volume=0.5, bgm_path=str(song), bgm_volume=0.3, bgm_offset=30.0, bgm_start=5.0, bgm_end=65.0)
    cmd = build_mix_command(paths(tmp_path), tmp_path / "concat.mp4", audio, 180.0, tmp_path / "out.mp4")
    fc = filter_complex(cmd)
    i_bgm = cmd.index(str(song))
    assert cmd[i_bgm - 1] == "-i" and cmd[i_bgm - 3:i_bgm - 1] == ["-stream_loop", "-1"]
    assert cmd[cmd.index("-i") + 1] == str(tmp_path / "concat.mp4")  # 첫 -i는 영상
    assert "[0:a]volume=0.500[a0]" in fc
    assert "[1:a]atrim=start=30.000,asetpts=PTS-STARTPTS,atrim=duration=60.000,asetpts=PTS-STARTPTS" in fc
    assert "afade=t=in:d=1,afade=t=out:st=58.000:d=2,volume=0.300,adelay=5000|5000,apad[a1]" in fc
    assert "[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]" in fc
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-t") + 1] == "180.000"
    assert cmd[cmd.index("-map") + 1] == "0:v" and "[a]" in cmd
    assert cmd[-1] == str(tmp_path / "out.mp4")


def test_mix_command_without_bgm_only_scales_original(tmp_path):
    cmd = build_mix_command(paths(tmp_path), tmp_path / "c.mp4", AudioMix(original_volume=0.25), 30.0, tmp_path / "o.mp4")
    assert filter_complex(cmd) == "[0:a]volume=0.250[a]"
    assert "-stream_loop" not in cmd and cmd.count("-i") == 1


def test_mix_command_drops_bgm_when_section_too_short(tmp_path):
    audio = AudioMix(bgm_path="s.mp3", bgm_start=100.0, bgm_end=100.2)
    cmd = build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 180.0, tmp_path / "o.mp4")
    assert filter_complex(cmd) == "[0:a]volume=1.000[a]" and "s.mp3" not in cmd
    audio = AudioMix(bgm_path="s.mp3", bgm_start=500.0)  # 구간이 영상 밖 → total로 잘려 0초
    assert "s.mp3" not in build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 180.0, tmp_path / "o.mp4")


def test_mix_command_end_none_runs_to_total(tmp_path):
    audio = AudioMix(bgm_path="s.mp3", bgm_start=10.0)
    fc = filter_complex(build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 100.0, tmp_path / "o.mp4"))
    assert "atrim=duration=90.000" in fc and "afade=t=out:st=88.000:d=2" in fc and "adelay=10000|10000" in fc


def test_mix_command_clamps_negative_offset_to_zero(tmp_path):
    audio = AudioMix(bgm_path="s.mp3", bgm_offset=-5.0)
    fc = filter_complex(build_mix_command(paths(tmp_path), tmp_path / "c.mp4", audio, 30.0, tmp_path / "o.mp4"))
    assert "atrim=start=0.000" in fc
