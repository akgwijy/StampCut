import pytest
from stampcut.core.models import Settings
from stampcut.gui.preview_widget import PreviewWidget


def test_shorts_preview_retains_legacy_odd_pan_offset(qtbot,make_video,make_clip):
    widget=PreviewWidget(Settings(),"Malgun Gothic")
    qtbot.addWidget(widget)
    widget.set_clip(make_clip(make_video(),60,pan_x=0.33))
    assert widget.video_item.pos().x()==-277


def test_video_preview_contains_portrait_source(qtbot,make_video,make_clip):
    widget=PreviewWidget(Settings(output_mode="video"),"Malgun Gothic")
    qtbot.addWidget(widget)
    widget._source_size=(1080,1920)
    widget.set_clip(make_clip(make_video(),60))
    assert (widget.scene.width(),widget.scene.height())==(1920,1080)
    assert (widget.video_item.size().width(),widget.video_item.size().height())==(608,1080)
    assert widget.video_item.pos().x()==656
    assert widget.caption_x_spin.maximum()==1920


def test_video_default_caption_keeps_two_lines_inside_canvas(qtbot,make_video,make_clip):
    widget=PreviewWidget(Settings(output_mode="video"),"Malgun Gothic")
    qtbot.addWidget(widget)
    widget.set_clip(make_clip(make_video(),60,caption="\uac00"*40))
    assert widget.caption_item.sceneBoundingRect().bottom() <= widget.scene.height()


def test_mode_switch_resizes_background_with_canvas(qtbot):
    from stampcut.core.output_modes import switch_output_mode
    widget=PreviewWidget(Settings(),"Arial")
    qtbot.addWidget(widget)
    widget.set_settings(switch_output_mode(widget.settings,"video"))
    assert widget.bg_item.rect()==widget.scene.sceneRect()
    widget.set_settings(switch_output_mode(widget.settings,"shorts"))
    assert widget.bg_item.rect()==widget.scene.sceneRect()
