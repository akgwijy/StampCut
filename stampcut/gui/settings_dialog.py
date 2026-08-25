"""설정 창."""
from __future__ import annotations

import re
import shutil
import sys
from dataclasses import replace

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from stampcut.core import settings as settings_mod
from stampcut.core.ffmpeg import find_ffmpeg
from stampcut.core.models import Settings

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "알 수 없음"


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setMinimumWidth(620)
        self._initial = settings

        self.api_key = QLineEdit(settings.api_key)
        self.api_key.setPlaceholderText("Google Cloud Console에서 발급한 YouTube Data API v3 키")
        self.pre = self._spin(0, 120, settings.pre_seconds, "초")
        self.post = self._spin(0, 120, settings.post_seconds, "초")
        self.max_total = self._spin(10, 1800, settings.max_total_seconds, "초")
        self.cluster = QDoubleSpinBox()
        self.cluster.setRange(0.5, 60.0)
        self.cluster.setDecimals(1)
        self.cluster.setSuffix("초")
        self.cluster.setValue(settings.cluster_window_seconds)
        self.output_dir = QLineEdit(settings.output_dir)
        self.title_template = QLineEdit(settings.title_template)
        self.background = QLineEdit(settings.background_color)
        self.font_path = QLineEdit(settings.font_path)
        self.font_path.setPlaceholderText(f"비우면 동봉 폰트: {settings_mod.bundled_font_path().name}")
        self.show_time = QCheckBox("자막 위에 원본 시간(예: 12:38) 표시")
        self.show_time.setChecked(settings.show_time_in_caption)
        self.ffmpeg_path = QLineEdit(settings.ffmpeg_path)
        self.ffmpeg_path.setPlaceholderText("비우면 앱 폴더 bin\\ffmpeg.exe → PATH 순으로 찾음")
        self.ffmpeg_status = QLabel()
        self.ffmpeg_path.textChanged.connect(self._refresh_ffmpeg_status)
        self._refresh_ffmpeg_status()
        self.parallel = self._spin(1, 4, settings.parallel_downloads, "개")

        self.update_btn = QPushButton(f"yt-dlp 업데이트 (현재 {ytdlp_version()})")
        self.update_btn.clicked.connect(self._update_ytdlp)
        self.cache_btn = QPushButton("캐시 비우기")
        self.cache_btn.clicked.connect(self._clear_cache)
        self.logs_btn = QPushButton("로그 폴더 열기")
        self.logs_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(settings_mod.log_dir()))))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(120)
        self.log_view.hide()
        self._proc: QProcess | None = None

        form = QFormLayout()
        form.addRow("YouTube API 키", self.api_key)
        form.addRow("앞 기본값", self.pre)
        form.addRow("뒤 기본값", self.post)
        form.addRow("최대 총 길이", self.max_total)
        form.addRow("묶음 간격", self.cluster)
        form.addRow("출력 폴더", self._with_browse(self.output_dir, self._browse_dir))
        form.addRow("타이틀 템플릿", self.title_template)
        form.addRow("배경색 (#RRGGBB)", self.background)
        form.addRow("자막 폰트 파일", self._with_browse(self.font_path, self._browse_font))
        form.addRow("", self.show_time)
        form.addRow("ffmpeg 경로", self._with_browse(self.ffmpeg_path, self._browse_ffmpeg))
        form.addRow("", self.ffmpeg_status)
        form.addRow("병렬 다운로드", self.parallel)

        tools = QHBoxLayout()
        tools.addWidget(self.update_btn)
        tools.addWidget(self.cache_btn)
        tools.addWidget(self.logs_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(tools)
        layout.addWidget(self.log_view)
        layout.addWidget(buttons)

    # --- 헬퍼 ---
    @staticmethod
    def _spin(lo: int, hi: int, value: int, suffix: str) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setValue(value)
        sb.setSuffix(suffix)
        return sb

    @staticmethod
    def _with_browse(edit: QLineEdit, slot) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("찾기…")
        btn.clicked.connect(slot)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return w

    def _browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", self.output_dir.text())
        if d:
            self.output_dir.setText(d)

    def _browse_font(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "자막 폰트", "", "폰트 (*.ttf *.otf)")
        if f:
            self.font_path.setText(f)

    def _browse_ffmpeg(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "ffmpeg.exe", "", "ffmpeg (ffmpeg.exe)")
        if f:
            self.ffmpeg_path.setText(f)

    def _refresh_ffmpeg_status(self) -> None:
        found = find_ffmpeg(self.ffmpeg_path.text())
        if found:
            self.ffmpeg_status.setText(f"감지됨: {found.ffmpeg}")
            self.ffmpeg_status.setStyleSheet("color: #2a8a2a")
        else:
            self.ffmpeg_status.setText("찾지 못함 — PowerShell에서 `winget install Gyan.FFmpeg` 후 새로 열거나 경로를 지정하세요")
            self.ffmpeg_status.setStyleSheet("color: #d00000")

    # --- 도구 ---
    def _update_ytdlp(self) -> None:
        self.log_view.show()
        self.log_view.setPlainText("pip install -U yt-dlp 실행 중…\n")
        self.update_btn.setEnabled(False)
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(
            lambda: self.log_view.appendPlainText(bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace").rstrip())
        )
        self._proc.finished.connect(self._on_update_done)
        self._proc.start(sys.executable, ["-m", "pip", "install", "-U", "yt-dlp"])

    def _on_update_done(self, code: int, _status) -> None:
        self.update_btn.setEnabled(True)
        self.log_view.appendPlainText("완료. 앱을 다시 시작하면 새 버전이 적용됩니다." if code == 0 else f"실패 (종료 코드 {code})")

    def _clear_cache(self) -> None:
        if QMessageBox.question(self, "캐시 비우기", "받아 둔 미리보기·최종 구간 파일을 모두 지울까요?") != QMessageBox.Yes:
            return
        shutil.rmtree(settings_mod.cache_dir(), ignore_errors=True)
        QMessageBox.information(self, "캐시 비우기", "캐시를 비웠습니다.")

    # --- 결과 ---
    def result_settings(self) -> Settings:
        return replace(
            self._initial,
            api_key=self.api_key.text().strip(),
            pre_seconds=self.pre.value(),
            post_seconds=self.post.value(),
            max_total_seconds=self.max_total.value(),
            cluster_window_seconds=self.cluster.value(),
            output_dir=self.output_dir.text().strip() or "~/Videos/StampCut",
            title_template=self.title_template.text().strip() or "{date} {channel} 하이라이트",
            background_color=self.background.text().strip(),
            font_path=self.font_path.text().strip(),
            show_time_in_caption=self.show_time.isChecked(),
            ffmpeg_path=self.ffmpeg_path.text().strip(),
            parallel_downloads=self.parallel.value(),
        )

    def accept(self) -> None:
        if not _COLOR_RE.match(self.background.text().strip()):
            QMessageBox.warning(self, "설정", "배경색은 #RRGGBB 형식이어야 합니다 (예: #000000).")
            return
        super().accept()
