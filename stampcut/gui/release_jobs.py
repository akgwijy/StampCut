"""Keep publishing workers alive until their terminal GUI callbacks."""
from __future__ import annotations
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QDialog
from stampcut.gui.workers import Worker

class JobDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.pool=QThreadPool.globalInstance()
        self._job=None
        self._jobs=[]
        self._pending_close=None
        self.close_declined=False
        self._result_handler=None

    @property
    def is_busy(self):
        return self._job is not None

    def run_job(self,fn,*args,on_finished=None,**kwargs):
        if self.is_busy:
            return
        self._result_handler=on_finished
        job=Worker(fn,*args,**kwargs)
        self._job=job
        self._jobs=[j for j in self._jobs if not j.done]+[job]
        job.signals.progress.connect(self._progress)
        job.signals.finished.connect(self._finished)
        job.signals.failed.connect(self._failed)
        job.signals.cancelled.connect(self._cancelled)
        self.set_job_busy(True)
        self.pool.start(job)

    def set_job_busy(self,busy):
        raise NotImplementedError

    def _progress(self,stage,done,total,message):
        if not self.is_busy:
            return
        self.message.setText(message)
        if total:
            self.progress.setRange(0,100)
            self.progress.setValue(max(0,min(100,int(done/total*100))))
        else:
            self.progress.setRange(0,0)

    def _terminal(self):
        self._job=None
        self.set_job_busy(False)
        self.progress.setRange(0,100)

    def _finish_close(self):
        if self._pending_close is not None:
            result=self._pending_close
            self._pending_close=None
            self.done(result)

    def _finished(self,result):
        self._terminal()
        handler=self._result_handler
        self._result_handler=None
        if handler is not None:
            try:
                handler(result)
            except Exception:
                self.message.setText("결과를 표시하지 못했습니다. 계정 설정과 저장 경로를 확인하세요.")
        self._finish_close()

    def _failed(self,message):
        self._terminal()
        self._result_handler=None
        self.message.setText(message)
        self._finish_close()

    def _cancelled(self):
        self._terminal()
        self._result_handler=None
        self.message.setText("작업 취소됨")
        self._finish_close()

    def cancel_job(self):
        if self._job is not None:
            self._job.cancel.set()
            self.cancel_btn.setEnabled(False)
            self.message.setText("취소하는 중… 진행 중인 요청이 끝나면 중단합니다.")

    def before_close(self):
        return True

    def done(self,result):
        self.close_declined=False
        if self.is_busy:
            self._pending_close=result
            self.cancel_job()
            if not self.before_close():
                self._pending_close=None
                self.close_declined=True
            return
        if self.before_close():
            super().done(result)
        else:
            self.close_declined=True

    def reject(self):
        self.done(QDialog.Rejected)

    def accept(self):
        self.done(QDialog.Accepted)

    def closeEvent(self,event):
        self.done(QDialog.Rejected)
        if self.is_busy or self.isVisible():
            event.ignore()
        else:
            event.accept()
