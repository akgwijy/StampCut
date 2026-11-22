import json
from dataclasses import replace

from stampcut.core import project_io
from stampcut.core import settings as sm
from stampcut.core.models import Project, Settings


def test_mode_switch_restores_common_and_per_clip_edits(make_video, make_clip):
    from stampcut.core.output_modes import switch_output_mode
    original=Settings(title_x=450, caption_x=320, caption_y=1400, max_total_seconds=600)
    clip=make_clip(make_video(),60,caption_x=200,caption_y=1300,caption_font_path="clip.otf",zoom=2.0,pan_x=0.2)
    video=switch_output_mode(original,"video",[clip])
    assert video.output_mode == video.style_mode == "video"
    assert (video.title_x,video.title_y,video.caption_x,video.caption_y)==(960,100,960,900)
    assert original.output_mode == "shorts" and original.caption_x == 320
    assert clip.caption_x is None and clip.caption_y is None
    assert clip.caption_font_path == "clip.otf" and clip.zoom == 1.0
    video.title_x=1100
    video.caption_y=880
    clip.caption_x,clip.caption_y,clip.zoom=1000,800,1.25
    shorts=switch_output_mode(video,"shorts",[clip])
    assert (shorts.title_x,shorts.caption_x,shorts.caption_y,shorts.max_total_seconds)==(450,320,1400,600)
    assert (clip.caption_x,clip.caption_y,clip.zoom,clip.pan_x)==(200,1300,2.0,0.2)
    again=switch_output_mode(shorts,"video",[clip])
    assert (again.title_x,again.caption_y)==(1100,880)
    assert (clip.caption_x,clip.caption_y,clip.zoom)==(1000,800,1.25)


def test_video_constructor_is_normalized_before_use():
    from stampcut.core.output_modes import normalize_output_settings
    raw=Settings(output_mode="video")
    effective=normalize_output_settings(raw)
    assert effective.style_mode == "video"
    assert (effective.title_x,effective.caption_x,effective.caption_y)==(960,960,900)
    assert raw.caption_y==1552


def test_invalid_stored_mode_profile_cannot_override_unrelated_settings():
    from stampcut.core.output_modes import switch_output_mode
    raw=Settings(api_key="KEEP",mode_settings={"video":{"title_x":"bad","caption_y":None,"api_key":"CHANGED"}})
    effective=switch_output_mode(raw,"video")
    assert effective.api_key=="KEEP"
    assert (effective.title_x,effective.caption_y)==(960,900)


def test_mode_settings_and_project_source_roundtrip(tmp_path,make_video,make_clip):
    from stampcut.core.output_modes import switch_output_mode
    v=make_video()
    clip=make_clip(v,60,caption_x=0,caption_font_path="")
    settings=switch_output_mode(Settings(),"video",[clip])
    settings.title_x=1000
    settings_path=tmp_path/"settings.json"
    sm.save(settings,settings_path)
    restored_settings=sm.load(settings_path)
    assert restored_settings==settings
    project=Project([v.url],"title",[v],[clip],output_mode="video",source_kind="channel",
                    source_channel="@demo",source_start="2026-11-01",source_end="2026-11-15")
    path=tmp_path/"project.json"
    project_io.save(project,path)
    restored=project_io.load(path)
    assert restored is not None
    assert (restored.output_mode,restored.source_kind,restored.source_channel,restored.source_start,restored.source_end)==("video","channel","@demo","2026-11-01","2026-11-15")
    back=switch_output_mode(restored_settings,"shorts",restored.clips)
    assert back.caption_y==1552
    assert restored.clips[0].caption_x==0 and restored.clips[0].caption_font_path==""


def test_old_settings_and_projects_restore_shorts(tmp_path,make_video,make_clip):
    settings_path=tmp_path/"old-settings.json"
    settings_path.write_text(json.dumps({"caption_y":1400}),encoding="utf-8")
    settings=sm.load(settings_path)
    assert settings.output_mode==settings.style_mode=="shorts" and settings.caption_y==1400
    v=make_video()
    path=tmp_path/"old-project.json"
    project_io.save(Project([v.url],"old",[v],[make_clip(v,60)]),path)
    data=json.loads(path.read_text("utf-8"))
    for field in ("output_mode","source_kind","source_channel","source_start","source_end"):
        data.pop(field,None)
    for item in data["clips"]:
        item.pop("mode_settings",None)
    path.write_text(json.dumps(data),encoding="utf-8")
    restored=project_io.load(path)
    assert restored is not None
    assert restored.output_mode=="shorts" and restored.source_kind=="urls"
    assert restored.clips[0].mode_settings=={}
