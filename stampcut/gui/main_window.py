from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow

from stampcut import __version__


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"StampCut {__version__}")
        self.resize(1280, 820)
        self.setCentralWidget(QLabel("StampCut", alignment=Qt.AlignCenter))
