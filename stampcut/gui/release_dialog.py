"""Edit a reusable post and publish an explicitly selected completed video."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox,QComboBox,QFileDialog,QFormLayout,QHBoxLayout,QHeaderView,QLabel,QLineEdit,QMessageBox,QPlainTextEdit,QProgressBar,QPushButton,QTableWidget,QTableWidgetItem,QVBoxLayout)
from stampcut.core import settings as settings_mod
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.publishing.models import ReleaseDraft,PublishError
from stampcut.core.publishing.service import ReleaseService,file_fingerprint,file_stamp
from stampcut.core.publishing.media import prepare_instagram
from stampcut.gui.release_jobs import JobDialog

class ReleaseDialog(JobDialog):
    def __init__(self,settings,paths=None,video_path=None,suggested_title="",parent=None,store=None,service=None):
        super().__init__(parent)
        self.setWindowTitle("릴리즈 · 유튜브 쇼츠 / 인스타그램 릴스")
        self.resize(780,740)
        self.settings=settings
        self.paths=paths
        self.store=store if store is not None else CredentialStore()
        self.service=service if service is not None else ReleaseService(self.store,paths)
        self.accounts_dialog=None
        self._close_accounts=False
        self._discard_draft_on_close=False
        draft=ReleaseDraft.from_dict(settings.publish_draft)
        selected_video=str(video_path) if video_path is not None else draft.video_path
        self._current_video_path=selected_video.strip()
        self._current_video_stamp=file_stamp(self._current_video_path)
        self._source_fingerprint=draft.instagram_source_fingerprint
        self._last_upload_draft=None
        self._drive_connected = False
        self._instagram_method = None
        self._drive_preference = None
        if self._current_video_path == draft.video_path.strip():
            if "instagram_use_drive" in (settings.publish_draft or {}):
                self._drive_preference = draft.instagram_use_drive
            elif draft.instagram_video_url:
                self._drive_preference = False
        if (self._current_video_path!=draft.video_path.strip() or self._current_video_stamp!=draft.video_stamp or not self._source_fingerprint):
            draft.instagram_video_url="";draft.instagram_prepared_path="";self._source_fingerprint=""
        self.video_edit=QLineEdit(selected_video)
        self.browse_btn=QPushButton("영상 선택…")
        self.browse_btn.clicked.connect(self._browse)
        self.prepare_btn=QPushButton("릴스 호환 파일 준비")
        self.prepare_btn.clicked.connect(self._prepare)
        file_row=QHBoxLayout();file_row.addWidget(self.video_edit,1);file_row.addWidget(self.browse_btn);file_row.addWidget(self.prepare_btn)
        self.youtube_check=QCheckBox("YouTube Shorts")
        self.instagram_check=QCheckBox("Instagram Reels")
        self.youtube_check.setChecked(draft.youtube);self.instagram_check.setChecked(draft.instagram)
        self.accounts_btn=QPushButton("계정 연결 / 설정…")
        self.accounts_btn.clicked.connect(self._open_accounts)
        targets=QHBoxLayout();targets.addWidget(self.youtube_check);targets.addWidget(self.instagram_check);targets.addStretch();targets.addWidget(self.accounts_btn)
        self.account_summary=QLabel();self.account_summary.setTextFormat(Qt.PlainText)
        self.title_edit=QLineEdit(draft.title or suggested_title)
        self.body_edit=QPlainTextEdit(draft.body)
        self.body_edit.setPlaceholderText("유튜브 설명과 인스타그램 캡션으로 사용할 글을 입력하세요. #해시태그도 그대로 저장됩니다.")
        self.body_edit.setMinimumHeight(180)
        self.privacy_combo=QComboBox()
        for label,value in [("공개","public"),("일부 공개","unlisted"),("비공개","private")]:self.privacy_combo.addItem(label,value)
        self.privacy_combo.setCurrentIndex(self.privacy_combo.findData(draft.privacy))
        self.kids_check=QCheckBox("아동용 콘텐츠")
        self.kids_check.setChecked(draft.made_for_kids)
        privacy=QHBoxLayout();privacy.addWidget(self.privacy_combo);privacy.addWidget(self.kids_check);privacy.addStretch()
        self.drive_label = QLabel("릴스 영상 전송")
        self.drive_check = QCheckBox("Google Drive 자동 업로드")
        self.drive_help = QLabel()
        self.drive_help.setWordWrap(True)
        self.instagram_url_label=QLabel("인스타 공개 영상 URL")
        self.instagram_url_edit=QLineEdit(draft.instagram_video_url)
        self.instagram_url_edit.setPlaceholderText("현재 영상 파일이 있는 공개 HTTPS 주소")
        self.prepared_label=QLabel("릴스 호환 파일")
        self.prepared_edit=QLineEdit(draft.instagram_prepared_path);self.prepared_edit.setReadOnly(True)
        self.prepared_edit.setPlaceholderText("준비 후 이 파일을 공개 저장소에 올리세요")
        self.text_count=QLabel()
        self.saved_label=QLabel("입력한 글과 옵션은 자동 저장되어 다음에도 불러옵니다.")
        form = QFormLayout()
        form.addRow("유튜브 제목", self.title_edit)
        form.addRow("유튜브 공개 범위", privacy)
        form.addRow(self.drive_label, self.drive_check)
        form.addRow(self.drive_help)
        form.addRow(self.prepared_label, self.prepared_edit)
        form.addRow(self.instagram_url_label, self.instagram_url_edit)
        hint=QLabel("YouTube Shorts: 세로/정사각형 · 3분 이내. Instagram 파일 직접 전송은 호환 영상으로 준비합니다.")
        hint.setWordWrap(True)
        self.result_table=QTableWidget(0,3)
        self.result_table.setHorizontalHeaderLabels(["플랫폼","상태","게시 링크 · 더블클릭으로 열기"])
        self.result_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.verticalHeader().hide()
        self.result_table.setMaximumHeight(160)
        self.result_table.cellDoubleClicked.connect(self._open_link)
        self.message=QLabel();self.message.setTextFormat(Qt.PlainText);self.message.setWordWrap(True)
        self.progress=QProgressBar();self.progress.setRange(0,100)
        self.upload_btn=QPushButton("선택한 플랫폼에 업로드")
        self.upload_btn.clicked.connect(self._upload)
        self.repost_btn = QPushButton("삭제한 Instagram 게시물 다시 올리기")
        self.repost_btn.clicked.connect(self._repost_deleted)
        self.repost_btn.hide()
        self._instagram_post_id = None
        self.cancel_btn=QPushButton("작업 취소");self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.close_btn=QPushButton("닫기");self.close_btn.clicked.connect(self.reject)
        buttons=QHBoxLayout();buttons.addStretch();buttons.addWidget(self.repost_btn);buttons.addWidget(self.upload_btn);buttons.addWidget(self.cancel_btn);buttons.addWidget(self.close_btn)
        layout=QVBoxLayout(self)
        layout.addLayout(file_row);layout.addLayout(targets);layout.addWidget(self.account_summary);layout.addLayout(form)
        layout.addWidget(QLabel("글 내용 · 유튜브 설명 / 인스타그램 캡션"));layout.addWidget(self.body_edit,1);layout.addWidget(self.text_count);layout.addWidget(self.saved_label);layout.addWidget(hint)
        layout.addWidget(QLabel("같은 영상과 글로 완료한 업로드는 재시도해도 건너뜁니다."));layout.addWidget(self.result_table);layout.addWidget(self.message);layout.addWidget(self.progress);layout.addLayout(buttons)
        self._draft_timer=QTimer(self);self._draft_timer.setSingleShot(True);self._draft_timer.setInterval(350);self._draft_timer.timeout.connect(self.flush_draft)
        self.drive_check.toggled.connect(self._drive_toggled)
        self.video_edit.textChanged.connect(self._video_changed)
        self.title_edit.textChanged.connect(self._changed)
        self.instagram_url_edit.textChanged.connect(self._url_changed)
        self.body_edit.textChanged.connect(self._changed)
        self.youtube_check.toggled.connect(self._changed);self.instagram_check.toggled.connect(self._changed);self.privacy_combo.currentIndexChanged.connect(self._changed);self.kids_check.toggled.connect(self._changed)
        self._refresh_accounts();self._update_count()

    def draft(self):
        use_drive = self.drive_check.isChecked()
        return ReleaseDraft(
            title=self.title_edit.text(),
            body=self.body_edit.toPlainText(),
            youtube=self.youtube_check.isChecked(),
            instagram=self.instagram_check.isChecked(),
            privacy=self.privacy_combo.currentData(),
            made_for_kids=self.kids_check.isChecked(),
            video_path=self.video_edit.text().strip(),
            instagram_use_drive=use_drive,
            instagram_video_url="" if use_drive else self.instagram_url_edit.text().strip(),
            instagram_prepared_path=self.prepared_edit.text(),
            instagram_source_fingerprint=self._source_fingerprint,
            video_stamp=self._current_video_stamp,
        )

    def _video_changed(self,*args):
        selected=self.video_edit.text().strip()
        if selected!=self._current_video_path or file_stamp(selected)!=self._current_video_stamp:
            self._drive_preference = None
            self.drive_check.setChecked(self._drive_connected)
            self._current_video_path=selected
            self._current_video_stamp=file_stamp(selected)
            self._source_fingerprint="";self._last_upload_draft=None
            self.prepared_edit.clear();self.instagram_url_edit.clear()
        self._changed()

    def _url_changed(self,*args):
        stamp=file_stamp(self.video_edit.text().strip())
        if stamp!=self._current_video_stamp:
            self._current_video_stamp=stamp
            self._source_fingerprint="";self._last_upload_draft=None;self.prepared_edit.clear()
        self._changed()

    def _changed(self,*args):
        self._instagram_post_id = None
        self.repost_btn.hide()
        self._draft_timer.start();self._update_count()
        if not self.is_busy:
            self.upload_btn.setEnabled(True);self.upload_btn.setText("선택한 플랫폼에 업로드")
        self._refresh_url_visibility()

    def _update_count(self):
        text=self.body_edit.toPlainText()
        self.text_count.setText(f"YouTube {len(text.encode('utf-8')):,}/5,000바이트 · Instagram {len(text):,}/2,200자")

    def flush_draft(self):
        self._draft_timer.stop()
        draft=self.draft()
        uploaded=self._last_upload_draft
        if uploaded and uploaded.video_path==draft.video_path and uploaded.instagram_video_url==draft.instagram_video_url and uploaded.instagram_source_fingerprint:
            self._source_fingerprint=uploaded.instagram_source_fingerprint
            draft.instagram_source_fingerprint=self._source_fingerprint
        # Persist a video-specific URL only after its source hash is known.
        if not draft.instagram_source_fingerprint:draft.instagram_video_url=""
        self.settings.publish_draft=draft.to_dict()
        if not self._drive_connected and self._drive_preference is None:
            # A disabled checkbox records no choice for the next connection.
            self.settings.publish_draft.pop("instagram_use_drive", None)
        try:
            settings_mod.save(self.settings)
            self.saved_label.setText("입력 내용 저장됨 · 다음에도 같은 글을 불러옵니다.")
            return True
        except OSError:
            self.saved_label.setText("입력 내용을 저장하지 못했습니다. 설정 폴더의 쓰기 권한을 확인하세요.")
            return False

    def before_close(self):
        if self._discard_draft_on_close:return True
        if self.flush_draft():return True
        choice=QMessageBox.warning(self,"입력 내용 저장 실패","현재 입력은 메모리에 유지됩니다. 다시 저장하거나, 저장하지 않고 닫을 수 있습니다.",QMessageBox.Retry|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Retry)
        if choice==QMessageBox.Retry:return self.flush_draft()
        self._discard_draft_on_close=choice==QMessageBox.Discard
        return self._discard_draft_on_close

    def _browse(self):
        value,_=QFileDialog.getOpenFileName(self,"업로드할 완성 영상",self.video_edit.text(),"동영상 (*.mp4 *.mov)")
        if value:
            self.video_edit.setText(value)
            self._video_changed()

    def _drive_toggled(self, checked):
        self._drive_preference = checked
        self._changed()

    def _refresh_url_visibility(self):
        method = self._instagram_method
        show_instagram = self.instagram_check.isChecked()
        use_drive = self.drive_check.isChecked()
        self.drive_check.setVisible(show_instagram)
        self.drive_label.setVisible(show_instagram)
        self.drive_check.setEnabled(not self.is_busy and (self._drive_connected or use_drive))
        self.drive_help.setVisible(show_instagram and (use_drive or not self._drive_connected))
        if use_drive:
            text = "릴스용 파일을 Drive에 올리고 ‘링크가 있는 모든 사용자’에게 임시로 보기 권한을 부여합니다. Instagram 처리가 끝나면 이 권한을 해제합니다."
            if not self._drive_connected:
                text = "계정 연결 / 설정에서 Google Drive를 다시 연결하세요. " + text
        else:
            text = "자동 업로드를 사용하려면 계정 연결 / 설정에서 Google Drive를 연결하세요."
        self.drive_help.setText(text)
        visible = show_instagram and not use_drive and method in ("url", "resumable", "unified")
        self.instagram_url_label.setText("인스타 영상 URL" if method == "url" else "인스타 영상 URL (선택)")
        self.instagram_url_edit.setPlaceholderText("Meta가 내려받을 MP4의 HTTPS 주소" if method == "url" else "비우면 파일 직접 전송 · 실패 시 호환 파일의 HTTPS 주소 입력")
        self.instagram_url_label.setVisible(visible);self.instagram_url_edit.setVisible(visible);self.prepare_btn.setVisible(visible);self.prepared_label.setVisible(visible);self.prepared_edit.setVisible(visible)

    def _refresh_accounts(self):
        try:
            youtube=self.store.get("youtube");instagram=self.store.get("instagram")
            self._instagram_method = instagram.get("method")
            connected = bool(self.store.get("drive").get("refresh_token"))
            if connected and self._drive_preference is None and not self.instagram_url_edit.text().strip():
                self.drive_check.setChecked(True)
            elif self._drive_preference is not None:
                self.drive_check.setChecked(self._drive_preference)
            self._drive_connected = connected
            self.account_summary.setText("YouTube: "+str(youtube.get("channel_title") or "연결 안 됨")+"    Instagram: "+str(instagram.get("username") or "연결 안 됨"))
        except PublishError as error:
            self._instagram_method = None
            self._drive_connected = False
            self.account_summary.setText(str(error))
        self._refresh_url_visibility()

    def _open_accounts(self):
        from stampcut.gui.release_accounts import ReleaseAccountsDialog
        dialog=ReleaseAccountsDialog(self.store,self);self.accounts_dialog=dialog
        try:dialog.exec()
        finally:
            self.accounts_dialog=None;dialog.deleteLater();self._refresh_accounts()
            if self._close_accounts:
                self._close_accounts=False;self.reject()

    def _prepare(self):
        if self.is_busy or not self.flush_draft():return
        video=Path(self.video_edit.text().strip())
        if not video.is_file():
            self.message.setText("호환 파일로 준비할 완성 영상을 선택하세요.");return
        self.message.setText("인스타그램용 호환 파일 준비 중")
        self.run_job(self._prepare_file,video,on_finished=self._prepared)

    def _prepare_file(self,video,progress,cancel):
        fingerprint=file_fingerprint(video,cancel)
        prepared=prepare_instagram(video,self.paths,fingerprint,progress=progress,cancel=cancel)
        if file_fingerprint(video,cancel)!=fingerprint:raise PublishError("준비 중 원본 영상이 변경되었습니다. 다시 준비하세요.")
        return prepared,fingerprint

    def _prepared(self,result):
        prepared,self._source_fingerprint=result
        self.prepared_edit.setText(str(prepared))
        self._current_video_stamp=file_stamp(self.video_edit.text().strip())
        self.instagram_url_edit.clear();self.flush_draft()
        self.progress.setValue(100)
        self.message.setText("호환 파일 준비 완료. 릴스 호환 파일 칸의 파일을 공개 HTTPS 저장소에 올린 뒤 직접 주소를 입력하세요. 유튜브는 위의 원본 영상을 사용합니다.")

    def _repost_deleted(self):
        if self.is_busy or not self._instagram_post_id:
            return
        answer = QMessageBox.question(self, "Instagram 다시 올리기",
                                      "Instagram에서 기존 게시물을 삭제했나요? 선택한 영상을 새 릴스로 게시합니다.",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._start_upload(self._instagram_post_id)

    def _upload(self):
        self._start_upload()

    def _start_upload(self, deleted_instagram_id=None):
        if self.is_busy or not self.flush_draft():return
        draft=self.draft()
        if deleted_instagram_id:
            draft = replace(draft, youtube=False, instagram=True)
        try:
            video=Path(draft.video_path)
            if not draft.video_path or not video.is_file():raise PublishError("업로드할 완성 영상을 선택하세요.")
            if not draft.youtube and not draft.instagram:raise PublishError("업로드할 플랫폼을 하나 이상 선택하세요.")
            if draft.instagram and draft.instagram_use_drive and not self._drive_connected:
                raise PublishError("계정 연결 / 설정에서 Google Drive를 다시 연결하세요.")
            for target,selected in (("youtube",draft.youtube),("instagram",draft.instagram)):
                if selected:draft.validate_for(target)
        except PublishError as error:
            self.message.setText(str(error));return
        self.result_table.setRowCount(0);self.message.setText("업로드 준비 중")
        self._last_upload_draft=draft
        self.repost_btn.hide()
        self._instagram_post_id = None
        options = {"deleted_instagram_id": deleted_instagram_id} if deleted_instagram_id else {}
        self.run_job(self.service.publish, video, draft, on_finished=self._show_results, **options)

    def set_job_busy(self,busy):
        for widget in (self.video_edit,self.browse_btn,self.prepare_btn,self.youtube_check,self.instagram_check,self.accounts_btn,self.title_edit,self.body_edit,self.privacy_combo,self.kids_check,self.instagram_url_edit,self.upload_btn):widget.setEnabled(not busy)
        self._refresh_url_visibility()
        self.cancel_btn.setEnabled(busy)

    def _show_results(self,result):
        self.flush_draft()
        names={"youtube":"YouTube","instagram":"Instagram"}
        statuses={"success":"업로드 완료","error":"실패","uncertain":"상태 확인 필요","cancelled":"취소됨"}
        self.result_table.setRowCount(len(result.outcomes))
        successes=0
        for row,outcome in enumerate(result.outcomes):
            text=statuses.get(outcome.status,outcome.status)
            if outcome.post and outcome.post.already_uploaded:text="이미 업로드됨"
            if outcome.platform == "instagram" and outcome.status == "success" and outcome.post and not outcome.error:
                self._instagram_post_id = outcome.post.id
                self.repost_btn.show()
            if outcome.error:text+="\n"+outcome.error
            if outcome.status=="success":successes+=1
            for column,value in enumerate((names.get(outcome.platform,outcome.platform),text,outcome.post.url if outcome.post else "")):
                self.result_table.setItem(row,column,QTableWidgetItem(value))
        self.result_table.resizeRowsToContents()
        self.progress.setValue(100 if not result.cancelled else self.progress.value())
        self.message.setText("작업 취소됨 · 이미 완료된 업로드는 유지됩니다." if result.cancelled else f"업로드 완료 {successes}개 / 대상 {len(result.outcomes)}개")
        incomplete=result.cancelled or any(outcome.status!="success" for outcome in result.outcomes)
        self.upload_btn.setText("미완료 대상 재시도" if incomplete else "업로드 완료")
        cleanup_pending = any(
            outcome.platform == "instagram" and outcome.status == "success"
            and "Drive 공유 해제" in outcome.error and "로컬 완료 기록" not in outcome.error
            for outcome in result.outcomes
        )
        if cleanup_pending and not incomplete:
            self.upload_btn.setText("Drive 공유 해제 재시도")
        self.upload_btn.setEnabled(incomplete or cleanup_pending)

    def _open_link(self,row,column):
        item=self.result_table.item(row,2)
        url=QUrl(item.text() if item else "")
        if column==2 and url.scheme()=="https" and url.host() in ("www.youtube.com","youtube.com","youtu.be","studio.youtube.com","www.instagram.com","instagram.com"):
            QDesktopServices.openUrl(url)

    def done(self,result):
        if self.accounts_dialog is not None and self.accounts_dialog.isVisible():
            self._close_accounts=True;self.flush_draft();self.accounts_dialog.reject();return
        super().done(result)
