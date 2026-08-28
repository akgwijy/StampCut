from stampcut.core.models import AudioMix
from stampcut.gui.bgm_panel import BgmPanel, list_audio_files


def _folder(tmp_path):
    d = tmp_path / "music"
    d.mkdir()
    for name in ("b.MP3", "a.wav", "notes.txt", "c.flac"):
        (d / name).write_bytes(b"x")
    return d


def _panel(qtbot, monkeypatch):
    p = BgmPanel()
    qtbot.addWidget(p)
    # 가짜 파일을 실제로 열지 않는다
    monkeypatch.setattr(p.player, "setSource", lambda url: None)
    monkeypatch.setattr(p.player, "play", lambda: None)
    return p


def test_list_audio_files_filters_and_sorts(tmp_path):
    d = _folder(tmp_path)
    assert [p.name for p in list_audio_files(d)] == ["a.wav", "b.MP3", "c.flac"]
    assert list_audio_files("") == [] and list_audio_files(tmp_path / "nope") == []


def test_panel_lists_folder_and_none_first(qtbot, monkeypatch, tmp_path):
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(_folder(tmp_path)))
    p.set_mix(AudioMix())
    assert [p.file_combo.itemText(i) for i in range(p.file_combo.count())] == ["없음", "a.wav", "b.MP3", "c.flac"]
    assert p.file_combo.currentIndex() == 0 and p.file_combo.itemData(0) == ""
    assert not p.bgm_slider.isEnabled() and not p.listen_btn.isEnabled() and p.original_slider.isEnabled()


def test_selecting_file_updates_mix_and_enables_controls(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    mix = AudioMix()
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(d))
    p.set_mix(mix)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.file_combo.setCurrentIndex(1)
    assert mix.bgm_path == str(d / "a.wav") and p.bgm_slider.isEnabled() and p.listen_btn.isEnabled()
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.file_combo.setCurrentIndex(0)
    assert mix.bgm_path == "" and not p.bgm_slider.isEnabled()


def test_sliders_and_spins_write_mix(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    mix = AudioMix(bgm_path=str(d / "a.wav"))
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(d))
    p.set_mix(mix)
    assert p.file_combo.currentText() == "a.wav"
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.original_slider.setValue(40)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.bgm_slider.setValue(55)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.offset_spin.setValue(12.5)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.start_spin.setValue(3.0)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.end_spin.setValue(45.0)
    assert (mix.original_volume, mix.bgm_volume, mix.bgm_offset, mix.bgm_start, mix.bgm_end) == (0.4, 0.55, 12.5, 3.0, 45.0)
    assert p.original_label.text() == "40%" and p.bgm_label.text() == "55%"
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.end_spin.setValue(0.0)
    assert mix.bgm_end is None and p.end_spin.text() == "끝까지"


def test_browse_adds_outside_file_and_missing_file_is_marked(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(b"x")
    mix = AudioMix()
    p = _panel(qtbot, monkeypatch)
    p.set_bgm_dir(str(d))
    p.set_mix(mix)
    with qtbot.waitSignal(p.changed, timeout=1000):
        p.select_file(str(outside))
    assert mix.bgm_path == str(outside) and p.file_combo.currentText() == "elsewhere.mp3"
    p.set_mix(AudioMix(bgm_path=str(tmp_path / "gone.mp3")))
    assert p.file_combo.currentText() == "gone.mp3 (파일 없음)"


def test_none_mix_disables_and_busy_stops_listening(qtbot, monkeypatch, tmp_path):
    d = _folder(tmp_path)
    p = _panel(qtbot, monkeypatch)
    p.set_mix(None)
    assert not p.isEnabled()
    p.set_bgm_dir(str(d))
    p.set_mix(AudioMix(bgm_path=str(d / "a.wav")))
    assert p.isEnabled()
    p.listen_btn.setChecked(True)
    assert p.listen_btn.text() == "⏹ 정지"
    p.set_busy(True)
    assert not p.isEnabled() and not p.listen_btn.isChecked() and p.listen_btn.text() == "▶ BGM만 듣기"
    p.set_busy(False)
    assert p.isEnabled()
    p.listen_btn.setChecked(True)
    p.stop()
    assert not p.listen_btn.isChecked()
