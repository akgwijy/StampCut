"""URL 목록 + 타이틀 입력 + 분석 버튼."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QGridLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QWidget

from stampcut.core.youtube_api import parse_video_id


class UrlPanel(QWidget):
    analyze_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.urls_edit = QPlainTextEdit()
        self.urls_edit.setPlaceholderText("유튜브 URL을 한 줄에 하나씩 붙여넣으세요")
        self.urls_edit.setFixedHeight(96)
        self.urls_edit.textChanged.connect(self._clear_marks)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("분석 후 자동으로 채워집니다 (예: 26.08.20 문성FC 하이라이트)")
        self.analyze_btn = QPushButton("댓글 분석")
        self.analyze_btn.setDefault(True)
        self.analyze_btn.clicked.connect(self.analyze_requested)

        layout = QGridLayout(self)
        layout.addWidget(QLabel("유튜브 URL (한 줄에 하나)"), 0, 0)
        layout.addWidget(QLabel("타이틀 (상단 띠)"), 0, 1)
        layout.addWidget(self.urls_edit, 1, 0, 2, 1)
        layout.addWidget(self.title_edit, 1, 1)
        layout.addWidget(self.analyze_btn, 2, 1, alignment=Qt.AlignRight | Qt.AlignBottom)
        layout.setColumnStretch(0, 3)
        layout.setColumnStretch(1, 2)

    def _lines(self) -> list[str]:
        return self.urls_edit.toPlainText().splitlines()

    def urls(self) -> list[str]:
        return [line.strip() for line in self._lines() if line.strip()]

    def invalid_lines(self) -> list[int]:
        return [i for i, line in enumerate(self._lines()) if line.strip() and parse_video_id(line) is None]

    def highlight_invalid(self) -> bool:
        bad = self.invalid_lines()
        selections = []
        doc = self.urls_edit.document()
        for i in bad:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = QTextCursor(doc.findBlockByNumber(i))
            sel.format.setBackground(QColor("#ffd6d6"))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            selections.append(sel)
        self.urls_edit.setExtraSelections(selections)
        return bool(bad)

    def _clear_marks(self) -> None:
        self.urls_edit.setExtraSelections([])

    def title(self) -> str:
        return self.title_edit.text()

    def set_title(self, title: str) -> None:
        self.title_edit.setText(title)

    def set_busy(self, busy: bool) -> None:
        self.analyze_btn.setEnabled(not busy)
        self.urls_edit.setReadOnly(busy)
