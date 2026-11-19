import pytest
from PySide6.QtCore import QPersistentModelIndex
from stampcut.core import project_io
from stampcut.core.models import Project, Settings
from stampcut.gui import main_window
from stampcut.gui.clip_table import ClipTableModel


def test_model_moves_existing_clips_and_keeps_persistent_selection(qtbot, make_video, make_clip):
    clips=[make_clip(make_video(),t,caption_x=200+t) for t in (60,120,180)]
    model=ClipTableModel(Settings())
    model.set_clips(clips)
    selected=QPersistentModelIndex(model.index(0,0))
    changed=[]
    model.changed.connect(lambda:changed.append(True))
    assert model.move_clip(0,2)
    assert model.clips==[clips[1],clips[2],clips[0]]
    assert selected.row()==2 and model.clip_at(2) is clips[0]
    assert model.move_clip(2,0)
    assert model.clips==clips and selected.row()==0
    assert [c.caption_x for c in clips]==[260,320,380]
    assert len(changed)==2


@pytest.mark.parametrize('source,target',[(-1,0),(0,-1),(0,3),(3,0),(0,0)])
def test_invalid_move_leaves_model_unchanged(qtbot,make_video,make_clip,source,target):
    clips=[make_clip(make_video(),t) for t in (60,120,180)]
    model=ClipTableModel(Settings())
    model.set_clips(clips)
    changed=[]
    model.changed.connect(lambda:changed.append(True))
    assert not model.move_clip(source,target)
    assert model.clips==clips and changed==[]


def make_window(qtbot,monkeypatch,make_video,make_clip,mode='shorts',project_file=None):
    monkeypatch.setattr(main_window.MainWindow,'start_previews',lambda *a,**k:None)
    monkeypatch.setattr(main_window.settings_mod,'save',lambda *a,**k:None)
    settings=Settings(output_mode=mode)
    window=main_window.MainWindow(settings,project_file=project_file)
    qtbot.addWidget(window)
    a=make_video()
    b=make_video(index=1,video_id='BBBBBBBBBBB',url='https://youtu.be/BBBBBBBBBBB',title='Another source')
    clips=[make_clip(a,300,caption='A',caption_x=200),make_clip(b,60,caption='B',caption_font_size=42),make_clip(a,120,caption='C',enabled=False)]
    project=Project([a.url,b.url],'title',[a,b],clips,output_mode=mode)
    window._adopt_project(project)
    return window,project,clips[:]


@pytest.mark.parametrize('mode',['shorts','video'])
def test_buttons_change_export_order_and_restore_saved_order(qtbot,monkeypatch,tmp_path,make_video,make_clip,mode):
    path=tmp_path/'project.json'
    window,project,clips=make_window(qtbot,monkeypatch,make_video,make_clip,mode,path)
    window.table.selectRow(1)
    window._autosave_timer.stop()
    window.move_up_btn.click()
    assert project.clips==window.model.clips==[clips[1],clips[0],clips[2]]
    assert project.enabled_clips()==[clips[1],clips[0]]
    assert window.preview.clip is clips[1] and window.table.currentIndex().row()==0
    assert not window.move_up_btn.isEnabled() and window.move_down_btn.isEnabled()
    assert window._autosave_timer.isActive()
    window._flush_autosave()
    restored=project_io.load(path)
    assert [c.id for c in restored.clips]==[clips[1].id,clips[0].id,clips[2].id]
    assert restored.clips[0].caption_font_size==42 and restored.clips[1].caption_x==200
    window.close()
    reopened=main_window.MainWindow(Settings(output_mode=mode),project_file=path)
    qtbot.addWidget(reopened)
    assert [c.id for c in reopened.model.clips]==[c.id for c in restored.clips]
    window=reopened
    window.table.selectRow(0)
    window.move_down_btn.click()
    assert [c.id for c in window.project.clips]==[c.id for c in clips]
    assert window.preview.clip.id==clips[1].id


def test_move_buttons_respect_selection_boundaries_and_busy_state(qtbot,monkeypatch,make_video,make_clip):
    window,project,clips=make_window(qtbot,monkeypatch,make_video,make_clip)
    assert not window.move_up_btn.isEnabled() and window.move_down_btn.isEnabled()
    window.table.selectRow(2)
    assert window.move_up_btn.isEnabled() and not window.move_down_btn.isEnabled()
    window._set_busy(True)
    assert not window.move_up_btn.isEnabled() and not window.move_down_btn.isEnabled()
    window._move_selected_clip(-1)
    assert project.clips==clips
    window._set_busy(False)
    assert window.move_up_btn.isEnabled()
    window.table.selectionModel().clearSelection()
    assert not window.move_up_btn.isEnabled() and not window.move_down_btn.isEnabled()
    window.model.set_clips([])
    assert not window.move_up_btn.isEnabled() and not window.move_down_btn.isEnabled()


def test_reordering_marks_full_preview_stale(qtbot,monkeypatch,tmp_path,make_video,make_clip):
    window,project,clips=make_window(qtbot,monkeypatch,make_video,make_clip)
    monkeypatch.setattr(window.preview.player,'setSource',lambda *a:None)
    monkeypatch.setattr(window.preview.player,'play',lambda *a:None)
    signature=main_window.pipeline.preview_signature(project,window.settings)
    window.preview.set_full_preview(tmp_path/'full.mp4',signature)
    window.table.selectRow(1)
    window.move_up_btn.click()
    assert main_window.pipeline.preview_signature(project,window.settings)!=signature
    assert '다시 만들기' in window.preview.full_status.text()
    assert window.preview.clip is clips[1]


def test_reordering_during_preview_target_collection_does_not_skip_clips(qtbot,monkeypatch,make_video,make_clip):
    start_previews=main_window.MainWindow.start_previews
    window,project,clips=make_window(qtbot,monkeypatch,make_video,make_clip)
    queued=[]
    monkeypatch.setattr(window.pool,'start',queued.append)
    downloaded=[]
    moved=False

    def covers(clip,settings):
        nonlocal moved
        if not moved:
            moved=True
            window.move_down_btn.click()
        return False

    monkeypatch.setattr(main_window.pipeline,'preview_covers',covers)
    monkeypatch.setattr(main_window.pipeline,'_preview_one',lambda clip,*args:downloaded.append(clip.caption))
    start_previews(window,project.clips)
    queued[0].run()
    assert project.clips==[clips[1],clips[0],clips[2]]
    assert sorted(downloaded)==['A','B','C']
