"""분석한 댓글의 시간별 링크를 검토하고 복사한다."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from html import escape
from io import StringIO
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from stampcut.core.models import Clip, Project
from stampcut.core.timestamps import format_time

_LINK_COLUMN = 3


@dataclass(frozen=True)
class _Row:
    clip: Clip
    title: str
    time: str
    comment: str
    link: str

    def columns(self) -> tuple[str, str, str, str]:
        return self.title, self.time, self.comment, self.link


class UrlExportDialog(QDialog):
    clip_delete_requested = Signal(object)

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("commentUrlExport")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("댓글 타임스탬프 링크")
        self.resize(960, 460)
        self._project = project
        self._rows: list[_Row] = []
        self._can_delete = True

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["영상 제목", "시간", "댓글", "링크"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 230)
        header.resizeSection(_LINK_COLUMN, 340)
        self.table.cellClicked.connect(self._open_link)
        self.table.itemSelectionChanged.connect(self._update_actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        action = QDialogButtonBox.ButtonRole.ActionRole
        self._copy_row_btn = buttons.addButton("한 줄 복사", action)
        self._copy_link_btn = buttons.addButton("링크만 복사", action)
        self._copy_all_btn = buttons.addButton("전체 복사", action)
        self._delete_btn = buttons.addButton("클립 삭제", action)
        self._copy_row_btn.clicked.connect(self._copy_selected)
        self._copy_link_btn.clicked.connect(self._copy_link)
        self._copy_all_btn.clicked.connect(self._copy_all)
        self._delete_btn.clicked.connect(self._request_delete)
        buttons.rejected.connect(self.reject)
        self._status = QLabel("링크를 누르면 유튜브에서 해당 시간부터 재생됩니다.")
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self._status)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        selected = self.table.currentRow()
        old_count = len(self._rows)
        mentions = sorted(
            ((clip, mention) for clip in self._project.clips for mention in clip.mentions),
            key=lambda pair: (pair[0].video.index, pair[1].seconds),
        )
        self._rows = []
        for clip, mention in mentions:
            query = urlencode({"v": clip.video.video_id, "t": f"{mention.seconds}s"})
            self._rows.append(_Row(
                clip,
                " ".join(clip.video.title.splitlines()),
                format_time(mention.seconds),
                " ".join((mention.comment_text or mention.caption).splitlines()),
                f"https://www.youtube.com/watch?{query}",
            ))
        self.table.clearContents()
        self.table.setRowCount(len(self._rows))
        for row, record in enumerate(self._rows):
            for column, value in enumerate(record.columns()):
                item = QTableWidgetItem(value)
                item.setToolTip(f"<span>{escape(value)}</span>")
                if column == _LINK_COLUMN:
                    item.setForeground(self.palette().brush(QPalette.ColorRole.Link))
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                self.table.setItem(row, column, item)
        if self._rows:
            self.table.selectRow(max(0, min(selected, len(self._rows) - 1)))
        else:
            self._status.setText("남은 링크가 없습니다.")
        if self._rows and len(self._rows) < old_count:
            self._status.setText(f"클립과 연결된 링크 {old_count - len(self._rows)}개를 삭제했습니다.")
        self._update_actions()

    def set_delete_allowed(self, allowed: bool) -> None:
        self._can_delete = allowed
        self._update_actions()

    def _selected_row(self) -> _Row | None:
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _update_actions(self) -> None:
        selected = self._selected_row() is not None
        self._copy_row_btn.setEnabled(selected)
        self._copy_link_btn.setEnabled(selected)
        self._copy_all_btn.setEnabled(bool(self._rows))
        self._delete_btn.setEnabled(selected and self._can_delete)
        self._delete_btn.setToolTip(
            "클립 목록에서도 삭제되며, 같은 클립의 링크는 함께 삭제됩니다."
            if self._can_delete else "미리보기 다운로드를 중지한 뒤 삭제하세요."
        )

    def _open_link(self, row: int, column: int) -> None:
        if column == _LINK_COLUMN:
            QDesktopServices.openUrl(QUrl(self._rows[row].link))

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self._status.setText("클립보드에 복사했습니다.")

    def _copy_rows(self, rows: list[_Row]) -> None:
        text = StringIO()
        csv.writer(text, lineterminator="\n").writerows(row.columns() for row in rows)
        self._copy(text.getvalue().removesuffix("\n"))

    def _copy_all(self) -> None:
        self._copy_rows(self._rows)

    def _copy_selected(self) -> None:
        if (row := self._selected_row()) is not None:
            self._copy_rows([row])

    def _copy_link(self) -> None:
        if (row := self._selected_row()) is not None:
            self._copy(row.link)

    def _request_delete(self) -> None:
        if self._can_delete and (row := self._selected_row()) is not None:
            self.clip_delete_requested.emit(row.clip)
