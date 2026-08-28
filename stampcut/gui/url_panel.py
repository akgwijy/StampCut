"""URL 목록 + 타이틀 입력 + 분석 버튼."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

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

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("유튜브 URL (한 줄에 하나)"))
        layout.addWidget(self.urls_edit)
        layout.addWidget(QLabel("타이틀 (상단 띠)"))
        trow = QHBoxLayout()
        trow.addWidget(self.title_edit, 1)
        trow.addWidget(self.analyze_btn)
        layout.addLayout(trow)

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

    def add_urls(self, urls: list[str]) -> int:
        """이미 있는 영상(id 기준)은 건너뛰고 새 줄로 덧붙인다. 추가한 개수를 돌려준다."""
        known = {vid for vid in (parse_video_id(line) for line in self._lines()) if vid}
        fresh: list[str] = []
        for u in urls:
            vid = parse_video_id(u)
            if vid and vid not in known:
                known.add(vid)
                fresh.append(u.strip())
        if fresh:
            # appendPlainText는 마지막 줄이 빈 줄이어도 새 블록을 하나 더 넣어 빈 줄이 생긴다.
            # 끝에 커서를 두고 직접 넣으면 개행 유무에 맞춰 붙고 textChanged는 한 번만 난다.
            text = self.urls_edit.toPlainText()
            cursor = self.urls_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(("" if not text or text.endswith("\n") else "\n") + "\n".join(fresh))
        return len(fresh)

    def title(self) -> str:
        return self.title_edit.text()

    def set_title(self, title: str) -> None:
        self.title_edit.setText(title)

    def set_busy(self, busy: bool) -> None:
        self.analyze_btn.setEnabled(not busy)
        self.urls_edit.setReadOnly(busy)
