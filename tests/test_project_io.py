import json

from stampcut.core import project_io
from stampcut.core.models import AudioMix, ClipStatus, Mention, Project


def _project(make_video, make_clip, tmp_path, with_preview=False):
    v = make_video()
    clip = make_clip(v, 758, caption="원더골")
    clip.pre, clip.post = 5, 20
    clip.enabled = False
    clip.zoom, clip.pan_x, clip.pan_y = 1.5, 0.2, 0.8
    clip.mentions = [Mention(v.video_id, 758, "원더골", "cid", "작성자", 3, False)]
    if with_preview:
        p = tmp_path / "preview.mp4"
        p.write_bytes(b"x")
        clip.preview_path, clip.preview_start, clip.preview_end = p, 700, 800
        clip.status = ClipStatus.READY
    return Project([v.url], "26.08.14 하이라이트", [v], [clip])


def test_roundtrip_preserves_edits(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path, with_preview=True)
    project_io.save(project, pf)
    assert not pf.with_suffix(".tmp").exists()  # 원자적 쓰기 잔여물 없음
    loaded = project_io.load(pf)
    assert loaded is not None
    assert loaded.urls == project.urls and loaded.title == project.title
    assert loaded.videos[0] == project.videos[0]
    c0, c1 = project.clips[0], loaded.clips[0]
    assert (c1.id, c1.t, c1.caption, c1.pre, c1.post, c1.enabled) == (c0.id, 758, "원더골", 5, 20, False)
    assert (c1.zoom, c1.pan_x, c1.pan_y) == (1.5, 0.2, 0.8)
    assert c1.mentions == c0.mentions
    assert c1.status is ClipStatus.READY
    assert (c1.preview_path, c1.preview_start, c1.preview_end) == (c0.preview_path, 700, 800)


def test_missing_preview_file_resets_to_pending(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path, with_preview=True)
    project.clips[0].preview_path = tmp_path / "gone.mp4"  # 캐시가 지워진 상황
    project_io.save(project, pf)
    c = project_io.load(pf).clips[0]
    assert c.status is ClipStatus.PENDING
    assert c.preview_path is None and c.preview_start is None and c.preview_end is None


def test_bad_files_return_none(tmp_path):
    assert project_io.load(tmp_path / "none.json") is None
    p = tmp_path / "p.json"
    p.write_text("{broken", "utf-8")
    assert project_io.load(p) is None
    p.write_text(json.dumps({"version": 999, "urls": [], "title": "", "videos": [], "clips": []}), "utf-8")
    assert project_io.load(p) is None
    p.write_text(json.dumps({"version": 1}), "utf-8")  # 필수 키 누락
    assert project_io.load(p) is None


def test_unknown_video_id_clip_skipped(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["clips"][0]["video_id"] = "UNKNOWN"
    pf.write_text(json.dumps(data), "utf-8")
    loaded = project_io.load(pf)
    assert loaded is not None and loaded.clips == []


def test_project_path_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert project_io.project_path() == tmp_path / "StampCut" / "project.json"


def test_non_numeric_fields_fail_safe(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["clips"][0]["pre"] = "abc"
    pf.write_text(json.dumps(data), "utf-8")
    assert project_io.load(pf) is None  # 손으로 망가뜨린 파일 → 새 작업


def test_final_path_restored_only_if_file_exists(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path)
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x")
    project.clips[0].final_path = final
    project_io.save(project, pf)
    assert project_io.load(pf).clips[0].final_path == final  # 파일이 있으면 보존
    final.unlink()
    assert project_io.load(pf).clips[0].final_path is None  # 파일이 지워졌으면 None


def test_audio_roundtrip_v2(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path)
    project.audio = AudioMix(original_volume=0.5, bgm_path=str(tmp_path / "song.mp3"), bgm_volume=0.2, bgm_offset=30.0, bgm_start=5.0, bgm_end=60.0)
    project_io.save(project, pf)
    assert json.loads(pf.read_text("utf-8"))["version"] == 2
    assert project_io.load(pf).audio == project.audio


def test_v1_file_loads_with_default_audio(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["version"] = 1
    del data["audio"]
    pf.write_text(json.dumps(data), "utf-8")
    loaded = project_io.load(pf)
    assert loaded is not None and loaded.audio == AudioMix() and len(loaded.clips) == 1


def test_broken_audio_falls_back_to_default(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project_io.save(_project(make_video, make_clip, tmp_path), pf)
    data = json.loads(pf.read_text("utf-8"))
    data["audio"] = {"bgm_volume": "loud"}
    pf.write_text(json.dumps(data), "utf-8")
    loaded = project_io.load(pf)
    assert loaded is not None and loaded.audio == AudioMix() and len(loaded.clips) == 1
    data["audio"] = "nope"
    pf.write_text(json.dumps(data), "utf-8")
    assert project_io.load(pf).audio == AudioMix()


def test_missing_bgm_file_path_is_kept(tmp_path, make_video, make_clip):
    pf = tmp_path / "p.json"
    project = _project(make_video, make_clip, tmp_path)
    project.audio.bgm_path = str(tmp_path / "gone.mp3")
    project_io.save(project, pf)
    assert project_io.load(pf).audio.bgm_path == str(tmp_path / "gone.mp3")
