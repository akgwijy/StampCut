"""Confirm all matching channel-period videos before comment collection."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout
from stampcut.gui.channel_models import ChannelVideoModel, V_CHECK, V_STAMPS, V_TITLE


class PeriodVideoDialog(QDialog):
    def __init__(self, videos, channel_title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("기간 영상 확인")
        self.resize(850, 500)
        self.model = ChannelVideoModel(self)
        self.model.append(list(videos))
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.hideColumn(V_STAMPS)
        header = self.table.horizontalHeader()
        for column in range(self.model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Stretch if column == V_TITLE else QHeaderView.ResizeToContents)
        all_btn, none_btn = QPushButton("전체 선택"), QPushButton("선택 해제")
        all_btn.clicked.connect(lambda: self.set_all_checked(True))
        none_btn.clicked.connect(lambda: self.set_all_checked(False))
        row = QHBoxLayout()
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("선택 영상 사용")
        self.buttons.button(QDialogButtonBox.Cancel).setText("취소")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{channel_title} · 영상 {len(videos)}개 — 분석할 영상을 선택하세요"))
        layout.addLayout(row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.buttons)
        self.model.checked_changed.connect(self._sync_button)
        self.set_all_checked(True)

    def selected_videos(self):
        selected = set(self.model.checked_urls())
        return [v for v in self.model.videos if v.url in selected]

    def set_all_checked(self, checked):
        blocked = self.model.blockSignals(True)
        try:
            for row in range(self.model.rowCount()):
                self.model.setData(self.model.index(row, V_CHECK), Qt.Checked if checked else Qt.Unchecked, Qt.CheckStateRole)
        finally:
            self.model.blockSignals(blocked)
        if self.model.rowCount():
            self.model.dataChanged.emit(self.model.index(0,V_CHECK),self.model.index(self.model.rowCount()-1,V_CHECK))
        self.model.checked_changed.emit()

    def _sync_button(self):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.model.checked_urls()))
