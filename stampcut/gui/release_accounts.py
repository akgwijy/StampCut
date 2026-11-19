"""Connect publishing accounts without putting credentials in plain settings."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from stampcut.core.ffmpeg import Cancelled
from stampcut.core.publishing.google_oauth import connect_youtube
from stampcut.core.publishing.instagram import connect_instagram
from stampcut.core.publishing.models import PublishError
from stampcut.gui.release_jobs import JobDialog


def _label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class ReleaseAccountsDialog(JobDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._yt_connected = False
        self._ig_connected = False
        self._ig_api_version = "v26.0"
        self.setWindowTitle("게시 계정 연결")
        self.resize(720, 650)

        self.yt_file_edit = QLineEdit()
        self.yt_file_edit.setPlaceholderText("Google Cloud 데스크톱 앱 OAuth 클라이언트 JSON")
        self.yt_browse_btn = QPushButton("찾기…")
        self.yt_browse_btn.clicked.connect(self._browse_youtube)
        file_row = QHBoxLayout()
        file_row.addWidget(self.yt_file_edit, 1)
        file_row.addWidget(self.yt_browse_btn)
        self.yt_connect_btn = QPushButton("Google 계정 연결")
        self.yt_connect_btn.clicked.connect(self._start_youtube)
        self.yt_disconnect_btn = QPushButton("앱에서 연결 해제")
        self.yt_disconnect_btn.clicked.connect(self._disconnect_youtube)
        yt_actions = QHBoxLayout()
        yt_actions.addWidget(self.yt_connect_btn)
        yt_actions.addWidget(self.yt_disconnect_btn)
        yt_actions.addStretch()
        self.yt_account_label = _label()
        yt_box = QGroupBox("YouTube")
        yt_layout = QVBoxLayout(yt_box)
        yt_layout.addWidget(_label("Google Cloud에서 YouTube Data API v3를 사용 설정하고, 데스크톱 앱 OAuth 클라이언트 JSON을 선택하세요. 연결하면 시스템 브라우저에서 Google 계정 승인을 받습니다."))
        yt_layout.addLayout(file_row)
        yt_layout.addLayout(yt_actions)
        yt_layout.addWidget(self.yt_account_label)

        self.ig_id_edit = QLineEdit()
        self.ig_id_edit.setPlaceholderText("프로페셔널 Instagram 계정 ID (숫자)")
        self.ig_token_edit = QLineEdit()
        self.ig_token_edit.setEchoMode(QLineEdit.Password)
        self.ig_token_edit.setPlaceholderText("선택한 방식에 맞는 장기 액세스 토큰")
        self.ig_method_combo = QComboBox()
        self.ig_method_combo.addItem("PC 파일 직접 전송 · Instagram 통합 로그인", "unified")
        self.ig_method_combo.addItem("PC 파일 직접 전송 · Facebook 페이지 연결", "resumable")
        self.ig_method_combo.addItem("공개 영상 URL · Instagram 연결", "url")
        self.ig_method_combo.currentIndexChanged.connect(self._update_method_help)
        self.ig_method_help = _label()
        self.ig_connect_btn = QPushButton("Instagram 계정 확인·연결")
        self.ig_connect_btn.clicked.connect(self._start_instagram)
        self.ig_disconnect_btn = QPushButton("앱에서 연결 해제")
        self.ig_disconnect_btn.clicked.connect(self._disconnect_instagram)
        ig_actions = QHBoxLayout()
        ig_actions.addWidget(self.ig_connect_btn)
        ig_actions.addWidget(self.ig_disconnect_btn)
        ig_actions.addStretch()
        self.ig_account_label = _label()
        ig_box = QGroupBox("Instagram")
        ig_layout = QVBoxLayout(ig_box)
        ig_form = QFormLayout()
        ig_form.addRow("업로드 방식", self.ig_method_combo)
        ig_form.addRow("계정 ID", self.ig_id_edit)
        ig_form.addRow("액세스 토큰", self.ig_token_edit)
        ig_layout.addLayout(ig_form)
        ig_layout.addWidget(self.ig_method_help)
        ig_layout.addLayout(ig_actions)
        ig_layout.addWidget(self.ig_account_label)

        self.message = _label("계정을 연결하면 게시 창에서 사용할 수 있습니다.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.cancel_btn = QPushButton("연결 취소")
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.reject)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.close_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(yt_box)
        layout.addWidget(ig_box)
        layout.addWidget(_label("연결 정보는 현재 Windows 사용자 계정으로 암호화해 이 PC에 저장합니다. 계정 연결만으로 영상이 게시되지는 않습니다."))
        layout.addWidget(self.message)
        layout.addWidget(self.progress)
        layout.addLayout(actions)
        self._controls = (self.yt_file_edit, self.yt_browse_btn, self.yt_connect_btn,
                          self.ig_id_edit, self.ig_token_edit, self.ig_method_combo, self.ig_connect_btn)
        try:
            youtube = self.store.get("youtube")
            instagram = self.store.get("instagram")
        except PublishError as error:
            youtube, instagram = {}, {}
            self.message.setText(str(error))
        self.yt_file_edit.setText(self._string(youtube.get("client_file")))
        self.ig_id_edit.setText(self._string(instagram.get("account_id")))
        self.ig_token_edit.setText(self._string(instagram.get("access_token")))
        method_index = self.ig_method_combo.findData(instagram.get("method", "unified"))
        self.ig_method_combo.setCurrentIndex(max(0, method_index))
        self._ig_api_version = self._string(instagram.get("api_version")) or "v26.0"
        self._show_youtube(youtube)
        self._show_instagram(instagram)
        self._update_method_help()
        self.set_job_busy(False)

    @staticmethod
    def _string(value):
        return value if isinstance(value, str) else ""

    def _update_method_help(self, *args):
        unified = self.ig_method_combo.currentData() == "unified"
        self.ig_id_edit.setPlaceholderText("팀 계정 1개만 승인했으면 비워 두세요" if unified else "프로페셔널 Instagram 계정 ID (숫자)")
        if unified:
            text = "Meta 비즈니스 포트폴리오에서 통합 로그인으로 팀 Instagram 계정을 승인하고 발급한 토큰을 입력하세요. 영상은 PC에서 릴스로 직접 전송됩니다."
        elif self.ig_method_combo.currentData() == "resumable":
            text = "Facebook 페이지에 연결된 비즈니스·크리에이터 계정을 사용합니다. Meta 앱에서 Facebook Login 장기 토큰을 발급하고 Instagram 기본·콘텐츠 게시 및 연결된 페이지 읽기 권한을 부여하세요."
        else:
            text = "Meta 앱의 Instagram Login으로 프로페셔널 계정 ID와 장기 토큰을 발급하고 Instagram 기본·콘텐츠 게시 권한을 부여하세요. 게시 시 외부에서 접근 가능한 공개 HTTPS 영상 URL이 필요합니다."
        self.ig_method_help.setText(text)

    def _browse_youtube(self):
        path, _ = QFileDialog.getOpenFileName(self, "Google OAuth 클라이언트 JSON 선택", self.yt_file_edit.text(), "JSON (*.json)")
        if path:
            self.yt_file_edit.setText(path)

    def set_job_busy(self, busy):
        for control in self._controls:
            control.setEnabled(not busy)
        self.yt_disconnect_btn.setEnabled(not busy and self._yt_connected)
        self.ig_disconnect_btn.setEnabled(not busy and self._ig_connected)
        self.cancel_btn.setEnabled(busy)
        if busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def _start_youtube(self):
        if self.is_busy:
            return
        path = self.yt_file_edit.text().strip()
        if not path:
            self.message.setText("Google 데스크톱 앱 OAuth 클라이언트 JSON 파일을 선택하세요.")
            return
        self.message.setText("Google 계정 연결을 시작합니다.")
        self.run_job(self._connect_youtube, path, on_finished=self._show_youtube)

    def _connect_youtube(self, path, *, progress, cancel):
        try:
            result = dict(connect_youtube(path, self.store, progress=progress, cancel=cancel))
            result["client_file"] = path
            self.store.put("youtube", result)
            return result
        except (PublishError, Cancelled):
            raise
        except Exception:
            raise PublishError("Google 계정을 연결하지 못했습니다. JSON 파일과 계정 설정을 확인하세요.") from None

    def _start_instagram(self):
        if self.is_busy:
            return
        account_id, token = self.ig_id_edit.text().strip(), self.ig_token_edit.text().strip()
        if not token or (not account_id and self.ig_method_combo.currentData() != "unified"):
            self.message.setText("인스타그램 프로페셔널 계정 ID와 액세스 토큰을 입력하세요.")
            return
        self.message.setText("인스타그램 계정을 확인하고 있습니다.")
        self.run_job(self._connect_instagram, account_id, token, self.ig_method_combo.currentData(), on_finished=self._show_instagram)

    def _connect_instagram(self, account_id, token, method, *, progress, cancel):
        try:
            return connect_instagram(account_id, token, method, self.store, api_version="v27.0" if method == "unified" else self._ig_api_version, progress=progress, cancel=cancel)
        except (PublishError, Cancelled):
            raise
        except Exception:
            raise PublishError("인스타그램 계정을 연결하지 못했습니다. 계정 ID와 토큰 권한을 확인하세요.") from None

    def _show_youtube(self, result):
        title = self._string(result.get("channel_title")) or self._string(result.get("channel_id"))
        self._yt_connected = bool(title)
        self.yt_account_label.setText("연결됨: " + title if title else "연결된 유튜브 계정 없음")
        self.yt_disconnect_btn.setEnabled(self._yt_connected and not self.is_busy)
        if self._yt_connected:
            self.message.setText("유튜브 계정을 연결했습니다.")
            self.progress.setValue(100)

    def _show_instagram(self, result):
        username = self._string(result.get("username"))
        self._ig_connected = bool(username)
        self.ig_account_label.setText("연결됨: @" + username if username else "연결된 인스타그램 계정 없음")
        self.ig_disconnect_btn.setEnabled(self._ig_connected and not self.is_busy)
        if self._ig_connected:
            self.ig_id_edit.setText(self._string(result.get("account_id")))
            self.message.setText("인스타그램 계정을 연결했습니다.")
            self.progress.setValue(100)

    def _disconnect_youtube(self):
        if self.is_busy:
            return
        try:
            self.store.delete("youtube")
        except PublishError as error:
            self.message.setText(str(error))
            return
        self.yt_file_edit.clear()
        self._show_youtube({})
        self.message.setText("앱에서 유튜브 연결을 해제했습니다.")

    def _disconnect_instagram(self):
        if self.is_busy:
            return
        try:
            self.store.delete("instagram")
        except PublishError as error:
            self.message.setText(str(error))
            return
        self.ig_id_edit.clear()
        self.ig_token_edit.clear()
        self._show_instagram({})
        self.message.setText("앱에서 인스타그램 연결을 해제했습니다.")
