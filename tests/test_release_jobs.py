from PySide6.QtWidgets import QLabel,QProgressBar,QPushButton


def dialog(qtbot):
    from stampcut.gui.release_jobs import JobDialog
    class TestDialog(JobDialog):
        def __init__(self):
            super().__init__()
            self.message=QLabel()
            self.progress=QProgressBar()
            self.cancel_btn=QPushButton()
            self.busy=[]
        def set_job_busy(self,busy):self.busy.append(busy)
    widget=TestDialog();qtbot.addWidget(widget)
    return widget


def test_busy_close_cancels_and_waits_for_terminal_callback(qtbot,monkeypatch):
    widget=dialog(qtbot);queued=[]
    monkeypatch.setattr(widget.pool,'start',queued.append)
    widget.show()
    widget.run_job(lambda progress,cancel:None)
    widget.reject()
    assert queued[0].cancel.is_set() and widget.isVisible() and widget.is_busy
    queued[0].run()
    assert not widget.is_busy and not widget.isVisible()


def test_job_result_and_failure_keep_dialog_usable(qtbot,monkeypatch):
    widget=dialog(qtbot);queued=[];results=[]
    monkeypatch.setattr(widget.pool,'start',queued.append)
    widget.run_job(lambda progress,cancel:42,on_finished=results.append)
    queued[0].run()
    assert results==[42] and not widget.is_busy
    def fail(progress,cancel):raise RuntimeError('expected error')
    widget.run_job(fail)
    queued[-1].run()
    assert 'expected error' in widget.message.text() and not widget.is_busy
