"""아래 상태 패널: 요약 · 출력 경로 · 만들기 버튼 · 진행률 · 완료 버튼."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from stampcut.core.models import Project, Settings
from stampcut.core.timestamps import format_time

STAGE_WEIGHTS = {"download": (0, 40), "render": (40, 50), "concat": (90, 10)}


def overall_percent(stage: str, done: int, total: int) -> int:
    frac = min(1.0, done / total) if total else 0.0
    if stage in STAGE_WEIGHTS:
        start, width = STAGE_WEIGHTS[stage]
        return int(start + width * frac)
    return int(100 * frac)


class StatusPanel(QWidget):
    render_requested = Signal()
    output_dir_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._output_dir = Path.home()
        self._result: Path | None = None
        self._busy = False
        self._clip_count = 0

        self.summary = QLabel("클립 0개 · 총 0:00 / 3:00")
        self.output_btn = QPushButton("출력: …")
        self.output_btn.clicked.connect(self._choose_dir)
        self.render_btn = QPushButton("하이라이트 만들기")
        self.render_btn.setEnabled(False)
        self.render_btn.clicked.connect(self.render_requested)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.message = QLabel("")
        self.open_folder_btn = QPushButton("폴더 열기")
        self.open_folder_btn.clicked.connect(self._open_folder)
        self.open_folder_btn.hide()
        self.play_btn = QPushButton("재생")
        self.play_btn.clicked.connect(self._play)
        self.play_btn.hide()

        top = QHBoxLayout()
        top.addWidget(self.summary)
        top.addStretch(1)
        top.addWidget(self.output_btn)
        top.addWidget(self.render_btn)
        bottom = QHBoxLayout()
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.message, 2)
        bottom.addWidget(self.open_folder_btn)
        bottom.addWidget(self.play_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(bottom)

    def set_output_dir(self, d: Path) -> None:
        self._output_dir = Path(d)
        self.output_btn.setText(f"출력: {self._output_dir}")

    def output_dir(self) -> Path:
        return self._output_dir

    def update_summary(self, project: Project | None, s: Settings) -> None:
        self._clip_count = len(project.enabled_clips()) if project else 0
        total = project.total_duration(s) if project else 0
        self.summary.setText(f"클립 {self._clip_count}개 · 총 {format_time(total)} / {format_time(s.max_total_seconds)}")
        self.summary.setStyleSheet("color: #d00000; font-weight: bold" if total > s.max_total_seconds else "")
        self.render_btn.setEnabled(self._clip_count > 0 and not self._busy)

    def set_progress(self, stage: str, done: int, total: int, message: str) -> None:
        self.progress.setValue(overall_percent(stage, done, total))
        self.message.setText(message)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.render_btn.setEnabled(self._clip_count > 0 and not busy)
        if busy:
            self._result = None
            self.open_folder_btn.hide()
            self.play_btn.hide()
            self.progress.setValue(0)

    def set_done(self, path: Path) -> None:
        self._result = path
        self.progress.setValue(100)
        self.message.setText(f"완료: {path.name}")
        self.open_folder_btn.show()
        self.play_btn.show()

    def has_result(self) -> bool:
        return self._result is not None

    def set_idle(self, message: str = "") -> None:
        self._result = None
        self.progress.setValue(0)
        self.message.setText(message)
        self.open_folder_btn.hide()
        self.play_btn.hide()

    def _open_folder(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.parent)))

    def _play(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result)))

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", str(self._output_dir))
        if d:
            self.set_output_dir(Path(d))
            self.output_dir_changed.emit(d)
