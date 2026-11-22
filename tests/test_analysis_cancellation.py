import threading

import pytest

from stampcut.core import pipeline
from stampcut.core.ffmpeg import Cancelled
from stampcut.core.models import Project, RawComment, Settings
from stampcut.gui.main_window import _analyze_job


@pytest.mark.parametrize("boundary", ["empty_video_response", "final_comments", "completion_progress"])
def test_analysis_rejects_cancel_after_last_retrieval(make_video, boundary):
    video = make_video()
    cancel = threading.Event()

    class Client:
        def fetch_video_infos(self, urls, strict=False):
            if boundary == "empty_video_response":
                cancel.set()
                return []
            return [video]

        def fetch_all_comments(self, video_id):
            if boundary == "final_comments":
                cancel.set()
            return [RawComment("last", "1:00 highlight", "author", 1, False)]

    def progress(stage, done, total, message):
        if boundary == "completion_progress" and done == total:
            cancel.set()

    with pytest.raises(Cancelled):
        pipeline.analyze([video.url], "title", Settings(), Client(), progress=progress, cancel=cancel)


def test_analysis_job_does_not_publish_project_cancelled_on_pipeline_return(monkeypatch):
    cancel = threading.Event()

    def analyze(*args, **kwargs):
        cancel.set()
        return Project([], "cancelled result", [], [])

    monkeypatch.setattr(pipeline, "analyze", analyze)
    with pytest.raises(Cancelled):
        _analyze_job([], "title", Settings(), object(), lambda *args: None, cancel)
