"""배경 음악 패널: 음원 선택·볼륨·위치 + BGM 단독 듣기."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from stampcut.core.models import AudioMix

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
NONE_LABEL = "없음"
MISSING_SUFFIX = " (파일 없음)"
LISTEN_LABEL = "▶ BGM만 듣기"
STOP_LABEL = "⏹ 정지"


def list_audio_files(folder: str | Path) -> list[Path]:
    """폴더 안의 오디오 파일을 이름순으로. 폴더가 없으면 []."""
    if not folder:
        return []
    d = Path(folder)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS), key=lambda p: p.name.lower())


class BgmPanel(QGroupBox):
    changed = Signal()  # mix를 제자리 수정한 뒤 발생
    bgm_dir_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("배경 음악", parent)
        self.mix: AudioMix | None = None
        self._bgm_dir = ""

        self.folder_btn = QPushButton("폴더…")
        self.folder_btn.clicked.connect(self._choose_folder)
        self.file_combo = QComboBox()
        self.file_combo.currentIndexChanged.connect(self._on_file_changed)
        self.browse_btn = QPushButton("찾아보기…")
        self.browse_btn.clicked.connect(self._browse_file)

        self.original_slider, self.original_label = self._volume_slider(100)
        self.original_slider.valueChanged.connect(self._on_original_volume)
        self.bgm_slider, self.bgm_label = self._volume_slider(30)
        self.bgm_slider.valueChanged.connect(self._on_bgm_volume)

        self.offset_spin = self._seconds_spin()
        self.offset_spin.valueChanged.connect(self._on_offset)
        self.start_spin = self._seconds_spin()
        self.start_spin.valueChanged.connect(self._on_start)
        self.end_spin = self._seconds_spin()
        self.end_spin.setSpecialValueText("끝까지")  # 0 = None
        self.end_spin.valueChanged.connect(self._on_end)

        self.listen_btn = QPushButton(LISTEN_LABEL)
        self.listen_btn.setCheckable(True)
        self.listen_btn.toggled.connect(self._on_listen_toggled)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        layout = QVBoxLayout(self)
        row1 = QHBoxLayout()
        row1.addWidget(self.folder_btn)
        row1.addWidget(self.file_combo, 1)
        row1.addWidget(self.browse_btn)
        layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("원본 볼륨"))
        row2.addWidget(self.original_slider, 1)
        row2.addWidget(self.original_label)
        row2.addSpacing(12)
        row2.addWidget(QLabel("BGM 볼륨"))
        row2.addWidget(self.bgm_slider, 1)
        row2.addWidget(self.bgm_label)
        layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("음원 시작"))
        row3.addWidget(self.offset_spin)
        row3.addSpacing(12)
        row3.addWidget(QLabel("영상 구간"))
        row3.addWidget(self.start_spin)
        row3.addWidget(QLabel("~"))
        row3.addWidget(self.end_spin)
        row3.addStretch(1)
        row3.addWidget(self.listen_btn)
        layout.addLayout(row3)
        self.set_mix(None)

    @staticmethod
    def _volume_slider(value: int) -> tuple[QSlider, QLabel]:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(5)
        slider.setPageStep(10)
        slider.setValue(value)
        label = QLabel(f"{value}%")
        label.setMinimumWidth(36)
        return slider, label

    @staticmethod
    def _seconds_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 3600.0)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix("초")
        spin.setKeyboardTracking(False)
        return spin

    # --- 외부 API ---
    def set_bgm_dir(self, folder: str) -> None:
        self._bgm_dir = folder
        self._rebuild_files()

    def bgm_dir(self) -> str:
        return self._bgm_dir

    def set_mix(self, mix: AudioMix | None) -> None:
        """패널이 mix를 직접 참조해 제자리 수정한다. None이면 전체 비활성."""
        self.stop()
        self.mix = mix
        self._rebuild_files()
        self._sync_controls()

    def set_busy(self, busy: bool) -> None:
        self.stop()
        self.setEnabled(not busy and self.mix is not None)

    def stop(self) -> None:
        if self.listen_btn.isChecked():
            self.listen_btn.setChecked(False)  # → _on_listen_toggled(False)
        else:
            self.player.stop()

    def select_file(self, path: str) -> None:
        """드롭다운에 없으면 항목을 추가하고 선택한다 (찾아보기·복원·테스트용)."""
        if self.file_combo.findData(path) < 0:
            self.file_combo.addItem(self._display_name(path), path)
        self.file_combo.setCurrentIndex(self.file_combo.findData(path))  # → _on_file_changed

    # --- 내부 ---
    @staticmethod
    def _display_name(path: str) -> str:
        p = Path(path)
        return p.name + ("" if p.is_file() else MISSING_SUFFIX)

    def _rebuild_files(self) -> None:
        """드롭다운 = 없음 + 폴더 내 오디오 + (목록에 없는) 현재 선택 파일."""
        current = self.mix.bgm_path if self.mix is not None else ""
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        self.file_combo.addItem(NONE_LABEL, "")
        paths = [str(p) for p in list_audio_files(self._bgm_dir)]
        if current and current not in paths:
            paths.append(current)
        for p in paths:
            self.file_combo.addItem(self._display_name(p), p)
        self.file_combo.setCurrentIndex(max(0, self.file_combo.findData(current)) if current else 0)
        self.file_combo.blockSignals(False)

    def _sync_controls(self) -> None:
        mix = self.mix
        self.setEnabled(mix is not None)
        if mix is None:
            return
        widgets = (self.original_slider, self.bgm_slider, self.offset_spin, self.start_spin, self.end_spin)
        for w in widgets:
            w.blockSignals(True)
        self.original_slider.setValue(int(round(mix.original_volume * 100)))
        self.bgm_slider.setValue(int(round(mix.bgm_volume * 100)))
        self.offset_spin.setValue(mix.bgm_offset)
        self.start_spin.setValue(mix.bgm_start)
        self.end_spin.setValue(mix.bgm_end if mix.bgm_end is not None else 0.0)
        for w in widgets:
            w.blockSignals(False)
        self.original_label.setText(f"{self.original_slider.value()}%")
        self.bgm_label.setText(f"{self.bgm_slider.value()}%")
        has = mix.has_bgm()
        for w in (self.bgm_slider, self.bgm_label, self.offset_spin, self.start_spin, self.end_spin, self.listen_btn):
            w.setEnabled(has)

    def _emit(self) -> None:
        if self.mix is not None:
            self.changed.emit()

    def _choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "BGM 폴더", self._bgm_dir or str(Path.home()))
        if d:
            self.set_bgm_dir(d)
            self.bgm_dir_changed.emit(d)

    def _browse_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        f, _ = QFileDialog.getOpenFileName(self, "배경 음악 파일", self._bgm_dir or str(Path.home()), f"오디오 파일 ({exts})")
        if f:
            self.select_file(f)

    def _on_file_changed(self, index: int) -> None:
        if self.mix is None:
            return
        self.stop()
        self.mix.bgm_path = self.file_combo.itemData(index) or ""
        self._sync_controls()
        self._emit()

    def _on_original_volume(self, value: int) -> None:
        self.original_label.setText(f"{value}%")
        if self.mix is not None:
            self.mix.original_volume = value / 100
            self._emit()

    def _on_bgm_volume(self, value: int) -> None:
        self.bgm_label.setText(f"{value}%")
        if self.mix is not None:
            self.mix.bgm_volume = value / 100
            self.audio_out.setVolume(self.mix.bgm_volume)
            self._emit()

    def _on_offset(self, value: float) -> None:
        if self.mix is not None:
            self.mix.bgm_offset = value
            if self.listen_btn.isChecked():
                self.player.setPosition(int(value * 1000))
            self._emit()

    def _on_start(self, value: float) -> None:
        if self.mix is not None:
            self.mix.bgm_start = value
            self._emit()

    def _on_end(self, value: float) -> None:
        if self.mix is not None:
            self.mix.bgm_end = value if value > 0 else None
            self._emit()

    # --- BGM만 듣기 ---
    def _on_listen_toggled(self, on: bool) -> None:
        if not on:
            self.player.stop()
            self.listen_btn.setText(LISTEN_LABEL)
            return
        if self.mix is None or not self.mix.has_bgm() or not Path(self.mix.bgm_path).is_file():
            self.listen_btn.setChecked(False)  # → 위의 not on 경로
            return
        self.player.setSource(QUrl.fromLocalFile(self.mix.bgm_path))
        self.audio_out.setVolume(self.mix.bgm_volume)
        self.player.play()
        self.player.setPosition(int(self.mix.bgm_offset * 1000))
        self.listen_btn.setText(STOP_LABEL)

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.listen_btn.isChecked():
            self.listen_btn.setChecked(False)
