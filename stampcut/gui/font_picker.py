"""설치된 폰트와 사용자 폰트 파일을 선택한다. 실제 파일을 저장해 Qt/FFmpeg의 글꼴을 맞춘다."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont, QRawFont
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QSizePolicy, QWidget

from stampcut.core.settings import bundled_font_path


@lru_cache(maxsize=1)
def installed_fonts() -> tuple[tuple[str, str], ...]:
    directories = [
        bundled_font_path().parent,
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft/Windows/Fonts",
    ]
    entries = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.suffix.lower() not in {".ttf", ".otf", ".ttc"} or not path.is_file():
                continue
            raw = QRawFont(str(path), 16)
            if raw.isValid():
                style = raw.styleName()
                if not style:
                    try:
                        style = QFont.Weight(raw.weight()).name
                    except ValueError:
                        style = str(raw.weight())
                    if raw.style() != QFont.StyleNormal:
                        style += " Italic"
                label = raw.familyName() + " " + style
                entries[str(path)] = label
    return tuple(sorted(((label, path) for path, label in entries.items()), key=lambda entry: (entry[0].casefold(), entry[1])))


class FontPicker(QWidget):
    path_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.addItem("기본 글꼴 (공통 설정)", "")
        for label, path in installed_fonts():
            self.combo.addItem(label, path)
        self.combo.currentIndexChanged.connect(self._changed)
        self.browse_btn = QPushButton("파일…")
        self.browse_btn.setToolTip("TTF / OTF / TTC 글꼴 파일 선택")
        self.browse_btn.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.browse_btn)

    def path(self) -> str:
        return str(self.combo.currentData() or "")

    def set_path(self, path: str) -> None:
        """설정에서 동기화할 때는 사용자 변경 신호를 내지 않는다."""
        path = str(Path(path)) if path else ""
        blocked = self.combo.blockSignals(True)
        index = self.combo.findData(path)
        if index < 0:
            label = Path(path).name + (" (파일 없음)" if not Path(path).is_file() else "")
            self.combo.addItem(label, path)
            index = self.combo.count() - 1
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(blocked)
        self.combo.setToolTip(path or "설정의 공통 기본 글꼴을 사용합니다")

    def _changed(self, index: int) -> None:
        self.combo.setToolTip(self.path() or "설정의 공통 기본 글꼴을 사용합니다")
        self.path_changed.emit(self.path())

    def _browse(self) -> None:
        directory = str(Path(self.path()).parent) if self.path() else str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
        path, _ = QFileDialog.getOpenFileName(self, "글꼴 선택", directory, "글꼴 파일 (*.ttf *.otf *.ttc)")
        if not path:
            return
        if not QRawFont(path, 16).isValid():
            QMessageBox.warning(self, "글꼴 선택", "이 글꼴 파일을 읽을 수 없습니다. 다른 파일을 선택해 주세요.")
            return
        previous = self.path()
        self.set_path(path)
        if self.path() != previous:
            self.path_changed.emit(self.path())
