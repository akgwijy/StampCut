"""메인 창: 패널 조립과 분석 → 미리보기 → 렌더 흐름."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from stampcut import __version__
from stampcut.core import pipeline
from stampcut.core import settings as settings_mod
from stampcut.core.downloader import Downloader, preview_covers
from stampcut.core.ffmpeg import find_ffmpeg
from stampcut.core.models import Clip, ClipStatus, Project, Settings
from stampcut.core.renderer import unique_output_path
from stampcut.core.youtube_api import ApiKeyError, QuotaError, VideoNotFound, YouTubeClient
from stampcut.gui.clip_table import COL_CAPTION, COL_POST, COL_PRE, ClipTableModel, SecondsDelegate
from stampcut.gui.preview_widget import PreviewWidget, load_font_family
from stampcut.gui.settings_dialog import SettingsDialog
from stampcut.gui.status_bar import StatusPanel
from stampcut.gui.url_panel import UrlPanel
from stampcut.gui.workers import Worker

log = logging.getLogger(__name__)
FFMPEG_MISSING = "ffmpeg를 찾지 못했습니다 — ⚙ 설정에서 경로를 지정하세요 (설치: winget install Gyan.FFmpeg)"


class _ClipBridge(QObject):
    """워커 스레드의 on_clip 콜백을 GUI 스레드 시그널로 옮긴다."""

    updated = Signal(object)


def _analyze_job(urls, title, settings, client, progress, cancel):
    try:
        return pipeline.analyze(urls, title, settings, client, progress=progress, cancel=cancel)
    except ApiKeyError as e:
        raise RuntimeError(f"API 키가 잘못되었거나 YouTube Data API v3가 사용 설정되지 않았습니다.\n설정에서 키를 확인하세요.\n({e})") from e
    except QuotaError as e:
        raise RuntimeError(f"오늘의 API 할당량을 다 썼습니다. 내일 오후 4시(태평양 자정)에 초기화됩니다.\n({e})") from e
    except VideoNotFound as e:
        raise RuntimeError(f"영상을 찾을 수 없습니다 (비공개·삭제·잘못된 ID): {e.video_id}") from e


class MainWindow(QMainWindow):
    analysis_done = Signal()
    render_done = Signal(object)

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else settings_mod.load()
        self.project: Project | None = None
        self.pool = QThreadPool.globalInstance()
        self._workers: list[Worker] = []
        self._render_worker: Worker | None = None
        self._render_candidates: set[str] = set()
        self._bridge = _ClipBridge()
        self._bridge.updated.connect(self._on_clip_updated)
        self._rebuild_tools()

        self.setWindowTitle(f"StampCut {__version__}")
        self.resize(1280, 820)
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.settings_action = toolbar.addAction("⚙ 설정", self.open_settings)

        self.url_panel = UrlPanel()
        self.url_panel.analyze_requested.connect(self.start_analysis)
        self.url_panel.title_edit.textChanged.connect(self._on_title_changed)

        self.model = ClipTableModel(self.settings)
        self.model.changed.connect(self._on_table_changed)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setItemDelegateForColumn(COL_PRE, SecondsDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_POST, SecondsDelegate(self.table))
        header = self.table.horizontalHeader()
        for col in range(self.model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == COL_CAPTION else QHeaderView.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_selected)
        retry = QAction("미리보기 다시 받기", self.table)
        retry.triggered.connect(self._retry_preview)
        self.table.addAction(retry)
        self.table.setContextMenuPolicy(Qt.ActionsContextMenu)

        self.preview = PreviewWidget(self.settings, load_font_family(settings_mod.resolve_font(self.settings)))
        self.preview.clip_changed.connect(self._on_clip_edited)
        self.preview.style_changed.connect(self._on_style_changed)

        self.status_panel = StatusPanel()
        self.status_panel.render_requested.connect(self.start_render)
        self.status_panel.output_dir_changed.connect(self._on_output_dir_changed)
        self.status_panel.set_output_dir(settings_mod.resolve_output_dir(self.settings))
        self.status_panel.update_summary(None, self.settings)
        if self.ffpaths is None:  # ffmpeg가 없으면 아무것도 못 하므로 API 키 안내보다 우선한다
            self.status_panel.set_idle(FFMPEG_MISSING)
        elif not self.settings.api_key:
            self.status_panel.set_idle("⚙ 설정에서 YouTube API 키를 먼저 입력하세요")

        left = QWidget()
        left.setMinimumWidth(420)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.url_panel)
        style_box = QGroupBox("세부 설정")
        QVBoxLayout(style_box).addWidget(self.preview.controls_panel)
        left_layout.addWidget(style_box)
        left_layout.addWidget(self.table, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 820])
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status_panel)
        self.setCentralWidget(central)

    # --- 설정 ---
    def _rebuild_tools(self) -> None:
        # PyInstaller로 묶였을 때는 실행 파일 옆 bin\ffmpeg.exe를 먼저 찾는다 (스펙 §4 순서 ①).
        self.ffpaths = find_ffmpeg(self.settings.ffmpeg_path, settings_mod.app_dir())
        self.downloader = Downloader(settings_mod.cache_dir(), str(self.ffpaths.ffmpeg) if self.ffpaths else None)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self, busy=any(not w.done for w in self._workers))
        if dlg.exec() == SettingsDialog.Accepted:
            self.apply_settings(dlg.result_settings())

    def apply_settings(self, s: Settings) -> None:
        self.settings = s
        settings_mod.save(s)
        self._rebuild_tools()
        self.model.set_settings(s)
        self.preview.set_settings(s)
        self.status_panel.set_output_dir(settings_mod.resolve_output_dir(s))
        self.status_panel.update_summary(self.project, s)

    def _on_style_changed(self) -> None:
        # PreviewWidget이 공유 Settings 객체를 직접 고쳤으므로 저장만 하면 된다
        settings_mod.save(self.settings)

    # --- 분석 ---
    def start_analysis(self) -> None:
        if self.url_panel.highlight_invalid():
            self._warn("URL 형식이 잘못된 줄이 있습니다 (빨간 줄).")
            return
        urls = self.url_panel.urls()
        if not urls:
            self._warn("유튜브 URL을 입력하세요.")
            return
        if not self.settings.api_key:
            self._warn("YouTube API 키가 필요합니다. 설정에서 입력하세요.")
            self.open_settings()
            return
        self._set_busy(True)
        self.status_panel.set_idle("댓글 분석 중")
        w = Worker(_analyze_job, urls, self.url_panel.title(), self.settings, YouTubeClient(self.settings.api_key))
        w.signals.finished.connect(self._on_analyzed)
        w.signals.failed.connect(self._on_analyze_failed)
        w.signals.cancelled.connect(lambda: self._set_busy(False))
        self._start(w)

    def _on_analyzed(self, project: Project) -> None:
        self.project = project
        self.url_panel.set_title(project.title)
        self.model.set_clips(project.clips)
        self.preview.set_title(project.title)
        self.status_panel.update_summary(project, self.settings)
        self._set_busy(False)
        if not project.clips:
            self.status_panel.set_idle("타임스탬프가 적힌 댓글이 없습니다")
            self._info("타임스탬프가 적힌 댓글이 없습니다.")
        else:
            self.table.selectRow(0)
            if self.ffpaths is None:
                self.status_panel.set_idle(FFMPEG_MISSING)
            else:
                self.start_previews(project.clips)
        if project.warnings:
            self._info("\n".join(project.warnings))
        self.analysis_done.emit()

    def _on_analyze_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.status_panel.set_idle("분석 실패")
        self._warn(f"댓글 분석 실패:\n{msg}")

    # --- 미리보기 ---
    def start_previews(self, clips: list[Clip]) -> None:
        assert self.project is not None
        w = Worker(pipeline.fetch_previews, self.project, self.settings, self.downloader, self._bridge.updated.emit, clips=clips)
        w.signals.finished.connect(self._on_previews_finished)
        w.signals.failed.connect(self._on_previews_failed)
        self._start(w)

    def _on_previews_finished(self, _result) -> None:
        if self._rendering() or self.status_panel.has_result():
            return
        self.status_panel.set_idle("미리보기 준비됨")

    def _on_previews_failed(self, msg: str) -> None:
        if self._rendering() or self.status_panel.has_result():
            return
        self.status_panel.set_idle(f"미리보기 오류: {msg}")

    def _on_clip_updated(self, clip: Clip) -> None:
        self.model.refresh_row(clip)
        if self.preview.clip is clip and clip.status is ClipStatus.READY:
            self.preview.refresh_media()

    def _retry_preview(self) -> None:
        clip = self._selected_clip()
        if clip and self.project:
            if clip.preview_path and clip.preview_path.exists():
                clip.preview_path.unlink(missing_ok=True)  # 깨진 캐시 파일이면 다시 받도록 지운다
            clip.preview_path = None
            self.start_previews([clip])

    # --- 편집 ---
    def _selected_clip(self) -> Clip | None:
        idx = self.table.currentIndex()
        return self.model.clip_at(idx.row()) if idx.isValid() else None

    def _on_row_selected(self, current, _previous) -> None:
        self.preview.set_clip(self.model.clip_at(current.row()) if current.isValid() else None)

    def _on_clip_edited(self, clip: Clip) -> None:
        self.model.refresh_row(clip)
        self.status_panel.update_summary(self.project, self.settings)
        if self.project and clip.status is ClipStatus.READY and not preview_covers(clip, self.settings):
            self.start_previews([clip])

    def _on_table_changed(self) -> None:
        self.status_panel.update_summary(self.project, self.settings)
        clip = self._selected_clip()
        if clip is not None and self.preview.clip is clip:
            self.preview.sync_from_clip()

    def _on_title_changed(self, text: str) -> None:
        if self.project:
            self.project.title = text
        self.preview.set_title(text)

    def _on_output_dir_changed(self, d: str) -> None:
        self.settings.output_dir = d
        settings_mod.save(self.settings)

    # --- 렌더 ---
    def start_render(self) -> None:
        if not self.project or not self.project.enabled_clips():
            self._warn("켜진 클립이 없습니다.")
            return
        if self.ffpaths is None:
            self._warn("ffmpeg를 찾지 못했습니다. 설정에서 경로를 지정하세요.\n(설치: winget install Gyan.FFmpeg)")
            self.open_settings()
            return
        if any(c.duration(self.settings) < 1 for c in self.project.enabled_clips()):
            self._warn("길이가 0초인 클립이 있습니다. 앞/뒤 초를 확인하세요.")
            return
        broken = [c for c in self.project.enabled_clips() if c.status is ClipStatus.ERROR]
        if broken:
            names = ", ".join(f"{c.video.short_name} {c.t // 60}:{c.t % 60:02d}" for c in broken)
            if QMessageBox.question(self, "StampCut", f"미리보기 다운로드에 실패한 클립이 켜져 있습니다:\n{names}\n\n그래도 시도할까요? (실패하면 빼고 계속합니다)") != QMessageBox.Yes:
                return
        out_dir = self.status_panel.output_dir()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._warn(f"출력 폴더를 만들 수 없습니다:\n{e}")
            return
        # 예상 크기(1080x1920 crf18 ≈ 3MB/s)의 2배가 없으면 중단 (스펙 §10)
        need = self.project.total_duration(self.settings) * 3_000_000 * 2
        free = shutil.disk_usage(out_dir).free
        if free < need:
            self._warn(f"출력 드라이브 여유 공간이 부족합니다.\n필요 약 {need // 1_000_000} MB, 남은 공간 {free // 1_000_000} MB")
            return
        output = unique_output_path(out_dir, self.project.title or "highlight")
        self._set_busy(True)
        self.status_panel.set_idle("렌더링 준비")
        self._render_candidates = {c.id for c in self.project.enabled_clips()}
        w = Worker(pipeline.render, self.project, self.settings, output, self.downloader, self.ffpaths, on_clip_failed=lambda clip, why: True)
        w.signals.finished.connect(self._on_rendered)
        w.signals.failed.connect(self._on_render_failed)
        w.signals.cancelled.connect(lambda: (self._set_busy(False), self.status_panel.set_idle("취소됨")))
        self._render_worker = w
        self._start(w)

    def _on_rendered(self, path: Path) -> None:
        self._set_busy(False)
        assert self.project is not None
        for c in self.project.clips:  # 모델을 갈아끼우면 선택/미리보기가 풀리므로 행만 갱신한다
            self.model.refresh_row(c)
        self.status_panel.update_summary(self.project, self.settings)
        self.status_panel.set_done(path)
        skipped = [c for c in self.project.clips if c.id in self._render_candidates and not c.enabled]
        if skipped:
            names = "\n".join(f"- {c.video.short_name} {c.t // 60}:{c.t % 60:02d}: {c.error}" for c in skipped)
            self._info(f"완성했지만 다운로드 실패로 {len(skipped)}개 클립을 뺐습니다:\n{names}")
        self.render_done.emit(path)

    def _on_render_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.status_panel.set_idle("렌더링 실패")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("StampCut")
        box.setText("렌더링에 실패했습니다. 자세한 내용은 아래를 펼쳐 보세요.\nyt-dlp 오류라면 설정 → yt-dlp 업데이트를 먼저 시도하세요.")
        box.setDetailedText(msg)
        box.exec()

    # --- 공통 ---
    def _rendering(self) -> bool:
        return self._render_worker is not None and not self._render_worker.done

    def _start(self, w: Worker) -> None:
        self._workers = [x for x in self._workers if not x.done]
        w.signals.progress.connect(lambda stage, done, total, msg, w=w: self._on_worker_progress(w, stage, done, total, msg))
        self._workers.append(w)
        self.pool.start(w)

    def _on_worker_progress(self, worker: Worker, stage: str, done: int, total: int, message: str) -> None:
        """렌더 중에는 렌더 워커의 진행률만 상태줄에 반영한다 (미리보기 틱이 덮어쓰지 않도록)."""
        if self._rendering() and worker is not self._render_worker:
            return
        self.status_panel.set_progress(stage, done, total, message)

    def _set_busy(self, busy: bool) -> None:
        self.url_panel.set_busy(busy)
        self.status_panel.set_busy(busy)
        self.table.setEnabled(not busy)
        self.preview.setEnabled(not busy)
        self.preview.controls_panel.setEnabled(not busy)
        self.settings_action.setEnabled(not busy)

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "StampCut", text)

    def _info(self, text: str) -> None:
        QMessageBox.information(self, "StampCut", text)

    def closeEvent(self, event) -> None:
        active = [w for w in self._workers if not w.done]
        if active:
            if QMessageBox.question(self, "StampCut", "작업이 진행 중입니다. 취소하고 종료할까요?") != QMessageBox.Yes:
                event.ignore()
                return
            for w in active:
                w.cancel.set()
            self.pool.waitForDone(5000)
            self._bridge.blockSignals(True)
            for w in active:  # 시간 안에 안 끝난 워커가 뒤늦게 시그널로 죽은 위젯을 건드리지 않도록
                w.signals.blockSignals(True)
        self.preview.shutdown()
        event.accept()
