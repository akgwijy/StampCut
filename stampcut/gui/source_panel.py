"""URL/channel-period source selection and confirmed-target invalidation."""
from __future__ import annotations
from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
from stampcut.core.youtube_api import parse_channel_ref


class SourcePanel(QWidget):
    changed = Signal()
    find_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_videos = []
        self._confirmed_query = None
        self.source_combo = QComboBox()
        self.source_combo.addItem("영상 주소", "urls")
        self.source_combo.addItem("채널 + 기간", "channel")
        top = QHBoxLayout()
        top.addWidget(QLabel("대상"))
        top.addWidget(self.source_combo, 1)
        self.channel_edit = QLineEdit()
        self.channel_edit.setPlaceholderText("@채널 또는 채널 주소")
        self.find_btn = QPushButton("영상 조회")
        self.find_btn.clicked.connect(self.find_requested)
        self.channel_edit.returnPressed.connect(self.find_requested)
        self.start_date = self._date(QDate.currentDate().addDays(-30))
        self.end_date = self._date(QDate.currentDate())
        self.summary = QLabel("채널과 기간을 입력한 뒤 영상을 조회하세요")
        self.summary.setWordWrap(True)
        self.period_box = QWidget()
        period = QVBoxLayout(self.period_box)
        period.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.channel_edit, 1)
        row.addWidget(self.find_btn)
        period.addLayout(row)
        dates = QHBoxLayout()
        dates.addWidget(QLabel("게시일"))
        dates.addWidget(self.start_date, 1)
        dates.addWidget(QLabel("~"))
        dates.addWidget(self.end_date, 1)
        period.addLayout(dates)
        period.addWidget(self.summary)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.period_box)
        self.source_combo.currentIndexChanged.connect(self._kind_changed)
        self.channel_edit.textChanged.connect(self._query_changed)
        self.start_date.dateChanged.connect(self._query_changed)
        self.end_date.dateChanged.connect(self._query_changed)
        self._kind_changed()

    @staticmethod
    def _date(value):
        widget = QDateEdit(value)
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("yyyy-MM-dd")
        return widget

    def is_channel(self):
        return self.source_combo.currentData() == "channel"

    def _kind_changed(self, *_):
        self.period_box.setVisible(self.is_channel())
        self.changed.emit()

    def _query_changed(self, *_):
        self.selected_videos = []
        self._confirmed_query = None
        self.summary.setText("조건이 바뀌었습니다 — 영상을 다시 조회하세요")
        self.changed.emit()

    def query_key(self):
        return (self.channel_edit.text().strip(), self.start_date.date().toString("yyyy-MM-dd"), self.end_date.date().toString("yyyy-MM-dd"))

    def query(self):
        return self.channel_edit.text().strip(), self.start_date.date().toPython(), self.end_date.date().toPython()

    def validation_error(self):
        channel, start, end = self.query()
        if not channel:
            return "채널 주소를 입력하세요."
        if parse_channel_ref(channel) is None:
            return "올바른 채널 주소 또는 @핸들을 입력하세요."
        if start > end:
            return "시작일은 종료일보다 늦을 수 없습니다."
        return ""

    def selection_ready(self):
        return bool(self.selected_videos) and self._confirmed_query == self.query_key() and not self.validation_error()

    def selected_urls(self):
        return [v.url for v in self.selected_videos] if self.selection_ready() else []

    def set_videos(self, videos, channel_title=""):
        self.selected_videos = list(videos)
        self._confirmed_query = self.query_key()
        label = f"{channel_title} · " if channel_title else ""
        self.summary.setText(f"{label}영상 {len(self.selected_videos)}개 선택됨")
        self.changed.emit()

    def metadata(self):
        channel, start, end = self.query_key()
        return {
            "source_kind": "channel" if self.is_channel() else "urls",
            "source_channel": channel if self.is_channel() else "",
            "source_start": start if self.is_channel() else "",
            "source_end": end if self.is_channel() else "",
        }

    def restore(self, project):
        blocked = self.blockSignals(True)
        try:
            self.selected_videos = []
            self._confirmed_query = None
            self.source_combo.setCurrentIndex(1 if project.source_kind == "channel" else 0)
            self.channel_edit.setText(project.source_channel)
            start = QDate.fromString(project.source_start, "yyyy-MM-dd")
            end = QDate.fromString(project.source_end, "yyyy-MM-dd")
            self.start_date.setDate(start if start.isValid() else QDate.currentDate().addDays(-30))
            self.end_date.setDate(end if end.isValid() else QDate.currentDate())
            if self.is_channel() and start.isValid() and end.isValid() and not self.validation_error():
                urls = set(project.urls)
                self.set_videos([v for v in project.videos if v.url in urls])
        finally:
            self.blockSignals(blocked)

    def set_busy(self, busy):
        self.setEnabled(not busy)
