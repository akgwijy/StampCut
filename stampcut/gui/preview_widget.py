"""최종 결과와 같은 9:16 미리보기. 정방형 안 영상을 드래그·줌으로 조절한다."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPainter, QPen, QRawFont
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from stampcut.core.bgm_sync import bgm_position
from stampcut.core.models import CAPTION_STYLE_FIELDS, AudioMix, Clip, Settings
from stampcut.core.settings import resolve_font
from stampcut.core.layouts import layout_for
from stampcut.core.output_modes import normalize_output_settings
from stampcut.gui.font_picker import FontPicker
from stampcut.core.renderer import FONT_SIZE_MIN, FONT_SIZE_MAX, LAYOUT, TIME_GAP, ZOOM_MAX, ZOOM_MIN, SquareGeometry, compute_square_geometry, compute_frame_geometry
from stampcut.core.textwrap_kr import wrap
from stampcut.core.timestamps import format_time

_S = LAYOUT["square"]
BGM_RESYNC_MS = 250  # 전체 미리보기와 BGM 위치 차이가 이보다 크면 다시 맞춘다
BGM_SEEK_COOLDOWN_MS = 500  # 재동기 seek이 착지할 시간을 준다 (연속 seek 방지)


def pan_after_drag(pan_x: float, pan_y: float, dx: float, dy: float, g: SquareGeometry, square: int = _S, frame_h: int | None = None) -> tuple[float, float]:
    """영상의 정방형 내 위치 = (square - sw) * pan 이므로, 위치 변화량을 pan으로 되돌린다."""
    denom_x, denom_y = square - g.sw, (frame_h if frame_h is not None else square) - g.sh
    if denom_x:
        pan_x += dx / denom_x
    if denom_y:
        pan_y += dy / denom_y
    return min(1.0, max(0.0, pan_x)), min(1.0, max(0.0, pan_y))


def video_item_placement(g: SquareGeometry, square: int = _S) -> tuple[int, int, int, int]:
    return g.pad_x - g.crop_x, g.pad_y - g.crop_y, g.sw, g.sh


def load_font_family(font_path: Path) -> str:
    fid = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    return families[0] if families else "Malgun Gothic"


def seek_target(pos_ms: int, delta_s: int, start_ms: int, end_ms: int) -> int:
    """구간 안에서 delta_s초 이동한 목표 위치(ms). 끝 200ms 앞에서 멈춰 즉시 루프-백을 피한다."""
    hi = max(start_ms, end_ms - 200)
    return min(hi, max(start_ms, pos_ms + delta_s * 1000))


class _DragView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, on_drag, hit_test=None, on_text_drag=None, on_drag_end=None) -> None:
        super().__init__(scene)
        self._on_drag = on_drag
        self._hit_test = hit_test or (lambda pos: None)
        self._on_text_drag = on_text_drag or (lambda kind, dx, dy: None)
        self._on_drag_end = on_drag_end or (lambda: None)
        self._last: QPointF | None = None
        self._target: str | None = None
        self.setBackgroundBrush(QColor("#202020"))
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCursor(Qt.OpenHandCursor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._last = self.mapToScene(event.position().toPoint())
            self._target = self._hit_test(self._last)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._last is not None:
            p = self.mapToScene(event.position().toPoint())
            d = p - self._last
            self._last = p
            if self._target:
                self._on_text_drag(self._target, d.x(), d.y())
            else:
                self._on_drag(d.x(), d.y())

    def mouseReleaseEvent(self, event) -> None:
        dragged = self._target
        self._last = None
        self._target = None
        self.setCursor(Qt.OpenHandCursor)
        if dragged:
            self._on_drag_end()


class PreviewWidget(QWidget):
    clip_changed = Signal(object)
    style_changed = Signal()  # 타이틀·자막 위치/색 변경 (settings에 직접 반영됨; 메인 창이 저장)
    full_preview_requested = Signal()
    bgm_error = Signal(str)
    playback_started = Signal()  # 이 위젯이 재생을 시작함 (BGM만 듣기와 겹치지 않도록 메인 창이 듣는다)

    def __init__(self, settings: Settings, font_family: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        settings = normalize_output_settings(settings)
        self.settings = settings
        self.output_layout = layout_for(settings.output_mode)
        self.font_family = font_family
        self._font_cache: dict[Path, QFont] = {}
        self._caption_scope_common = False
        self._caption_drag_clip: Clip | None = None
        self.clip: Clip | None = None
        self.title = ""
        self._source_size = (1920, 1080)
        self._loaded_path: Path | None = None
        self.audio_mix: AudioMix | None = None
        self._mode = "clip"
        self._full_path: Path | None = None
        self._full_signature: str | None = None
        self._full_stale = False
        self._full_duration_ms = 0
        self._bgm_loaded = ""
        self._bgm_duration_ms = 0
        self._bgm_last_seek = 0.0  # time.monotonic()

        L = self.output_layout
        self._shut_down = False
        self.scene = QGraphicsScene(0, 0, L["canvas_w"], L["canvas_h"], self)
        self.bg_item = self.scene.addRect(QRectF(0, 0, L["canvas_w"], L["canvas_h"]), QPen(Qt.NoPen), QBrush(QColor(settings.background_color)))
        self.square = QGraphicsRectItem(0, 0, L["frame_w"], L["frame_h"])
        self.square.setPen(QPen(Qt.NoPen))
        self.square.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.square.setPos(L["frame_x"], L["frame_y"])
        self.scene.addItem(self.square)
        self._apply_background()
        self.video_item = QGraphicsVideoItem(self.square)
        self.video_item.nativeSizeChanged.connect(self._on_native_size)

        self.full_item = QGraphicsVideoItem()  # 전체 미리보기: 캔버스 전체 (이미 9:16으로 구워진 파일)
        self.full_item.setSize(QSizeF(L["canvas_w"], L["canvas_h"]))
        self.full_item.setPos(0, 0)
        self.full_item.setVisible(False)
        self.scene.addItem(self.full_item)

        self.title_item = self._text_item(L["title_font"], self.settings.title_color)
        self.time_item = self._text_item(L["time_font"], L["time_color"])
        self.caption_item = self._text_item(L["caption_font"], self.settings.caption_color, border=L["caption_border"])

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_item)
        self.player.positionChanged.connect(self._on_position)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.bgm_player = QMediaPlayer(self)
        self.bgm_audio = QAudioOutput(self)
        self.bgm_player.setAudioOutput(self.bgm_audio)
        self.bgm_player.durationChanged.connect(self._on_bgm_duration)
        self.bgm_player.errorOccurred.connect(lambda _err, msg: self.bgm_error.emit(msg))

        self.view = _DragView(self.scene, self._on_drag, self._hit_text, self._on_text_drag, self._on_text_drag_end)
        self.play_btn = QPushButton("▶ 재생")
        self.play_btn.clicked.connect(self._toggle_play)
        self.pos_label = QLabel("0:00 / 0:00")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setSingleStep(1000)
        self.seek_slider.setPageStep(5000)
        self.seek_slider.valueChanged.connect(self._on_seek)
        self.back5_btn = QPushButton("-5초")
        self.back5_btn.clicked.connect(lambda: self._seek_by(-5))
        self.back1_btn = QPushButton("-1초")
        self.back1_btn.clicked.connect(lambda: self._seek_by(-1))
        self.fwd1_btn = QPushButton("+1초")
        self.fwd1_btn.clicked.connect(lambda: self._seek_by(1))
        self.fwd5_btn = QPushButton("+5초")
        self.fwd5_btn.clicked.connect(lambda: self._seek_by(5))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(int(ZOOM_MIN * 100), int(ZOOM_MAX * 100))
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(25)
        self.zoom_slider.setTickInterval(5)
        self.zoom_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self._on_zoom)
        self.zoom_label = QLabel("1.00×")
        self.pre_spin = QSpinBox()
        self.pre_spin.setRange(-120, 120)
        self.pre_spin.setToolTip('댓글 시각 기준 시작 위치: -5초 = 5초 전, 0초 = 댓글 시각, 5초 = 5초 후')
        self.pre_spin.setSuffix("초")
        self.pre_spin.setKeyboardTracking(False)  # 타이핑 중간값마다 미리보기를 다시 받지 않도록
        self.pre_spin.valueChanged.connect(self._on_pre)
        self.post_spin = QSpinBox()
        self.post_spin.setRange(0, 120)
        self.post_spin.setSuffix("초")
        self.post_spin.setKeyboardTracking(False)
        self.post_spin.valueChanged.connect(self._on_post)
        self.caption_scope_combo = QComboBox()
        self.caption_scope_combo.addItems(["이 클립", "공통 기본값"])
        self.caption_scope_combo.setCurrentIndex(1)
        self.caption_scope_combo.model().item(0).setEnabled(False)
        self.caption_scope_combo.setToolTip("자막의 위치·글꼴·크기·색상을 적용할 범위입니다. 공통값은 개별 지정하지 않은 항목에 적용됩니다.")
        self.caption_scope_combo.currentIndexChanged.connect(self._on_caption_scope_changed)
        self.caption_reset_btn = QPushButton("공통값 사용")
        self.caption_reset_btn.setToolTip("이 클립의 자막 설정을 해제하고 공통 기본값을 사용합니다")
        self.caption_reset_btn.setEnabled(False)
        self.caption_reset_btn.clicked.connect(self._reset_caption_style)
        self.caption_edit = QLineEdit()
        self.caption_edit.textChanged.connect(self._on_caption)
        self.title_color_btn = self._color_button(self.settings.title_color, self._on_title_color)
        self.title_y_spin = self._y_spin(self.settings.title_y, self._on_title_y)
        self.caption_color_btn = self._color_button(self.settings.caption_color, self._on_caption_color)
        self.caption_y_spin = self._y_spin(self.settings.caption_y, self._on_caption_y)
        self.title_x_spin = self._style_spin(self.settings.title_x, 0, self.output_layout["canvas_w"], "title_x")
        self.caption_x_spin = self._style_spin(self.settings.caption_x, 0, self.output_layout["canvas_w"], "caption_x")
        self.title_size_spin = self._style_spin(self.settings.title_font_size, FONT_SIZE_MIN, FONT_SIZE_MAX, "title_font_size", " px")
        self.caption_size_spin = self._style_spin(self.settings.caption_font_size, FONT_SIZE_MIN, FONT_SIZE_MAX, "caption_font_size", " px")
        self.title_font_picker = FontPicker()
        self.caption_font_picker = FontPicker()
        self.title_font_picker.set_path(self.settings.title_font_path)
        self.caption_font_picker.set_path(self.settings.caption_font_path)
        self.title_font_picker.path_changed.connect(lambda path: self._on_text_style("title_font_path", path))
        self.caption_font_picker.path_changed.connect(lambda path: self._on_text_style("caption_font_path", path))
        self.reset_btn = QPushButton("기본값으로")
        self.reset_btn.clicked.connect(self._reset)

        self.controls_panel = QWidget()
        controls = QFormLayout(self.controls_panel)
        zrow = QHBoxLayout()
        zrow.addWidget(self.zoom_slider)
        zrow.addWidget(self.zoom_label)
        controls.addRow("줌", zrow)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("앞"))
        srow.addWidget(self.pre_spin)
        srow.addWidget(QLabel("뒤"))
        srow.addWidget(self.post_spin)
        controls.addRow("클립", srow)
        controls.addRow("제목", self._text_style_row(self.title_x_spin, self.title_y_spin, self.title_size_spin, self.title_color_btn))
        controls.addRow("글꼴", self.title_font_picker)
        caption_row = QHBoxLayout()
        caption_row.addWidget(self.caption_edit, 1)
        caption_row.addWidget(QLabel("적용"))
        caption_row.addWidget(self.caption_scope_combo)
        caption_row.addWidget(self.caption_reset_btn)
        controls.addRow("자막 내용", caption_row)
        controls.addRow("자막", self._text_style_row(self.caption_x_spin, self.caption_y_spin, self.caption_size_spin, self.caption_color_btn))
        controls.addRow("글꼴", self.caption_font_picker)
        controls.addRow(self.reset_btn)

        self.clip_mode_btn = QPushButton("클립")
        self.clip_mode_btn.setCheckable(True)
        self.clip_mode_btn.setChecked(True)
        self.full_mode_btn = QPushButton("전체")
        self.full_mode_btn.setCheckable(True)
        self.full_mode_btn.setEnabled(False)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.clip_mode_btn)
        self.mode_group.addButton(self.full_mode_btn)
        self.clip_mode_btn.clicked.connect(self._on_clip_mode_clicked)
        self.full_mode_btn.clicked.connect(self._on_full_mode_clicked)
        self.make_full_btn = QPushButton("전체 미리보기 만들기")
        self.make_full_btn.clicked.connect(self.full_preview_requested)
        self.full_status = QLabel("")
        self.mode_row = QHBoxLayout()
        self.mode_row.addWidget(self.clip_mode_btn)
        self.mode_row.addWidget(self.full_mode_btn)
        self.mode_row.addWidget(self.make_full_btn)
        self.mode_row.addWidget(self.full_status, 1)

        self.transport = QHBoxLayout()
        self.transport.addWidget(self.play_btn)
        self.transport.addWidget(self.back5_btn)
        self.transport.addWidget(self.back1_btn)
        self.transport.addWidget(self.seek_slider, 1)
        self.transport.addWidget(self.fwd1_btn)
        self.transport.addWidget(self.fwd5_btn)
        self.transport.addWidget(self.pos_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addLayout(self.mode_row)
        layout.addLayout(self.transport)
        self._set_controls_enabled(False)

    # --- 구성 ---
    def _text_item(self, size: int, color: str, border: int = 0) -> QGraphicsSimpleTextItem:
        item = QGraphicsSimpleTextItem()
        font = QFont(self.font_family)
        font.setPixelSize(size)
        font.setBold(True)
        item.setFont(font)
        item.setBrush(QBrush(QColor(color)))
        item.setPen(QPen(QColor("black"), border) if border else QPen(Qt.NoPen))
        self.scene.addItem(item)
        return item

    def _y_spin(self, value: int, handler) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, self.output_layout["canvas_h"])
        spin.setKeyboardTracking(False)
        spin.setValue(value)
        spin.valueChanged.connect(handler)
        return spin

    def _style_spin(self, value: int, lo: int, hi: int, field: str, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setKeyboardTracking(False)
        spin.setSuffix(suffix)
        spin.setValue(value)
        spin.valueChanged.connect(lambda value: self._on_text_style(field, value))
        return spin

    @staticmethod
    def _text_style_row(x_spin, y_spin, size_spin, color_btn) -> QHBoxLayout:
        row = QHBoxLayout()
        for label, spin in (("X", x_spin), ("Y", y_spin), ("크기", size_spin)):
            row.addWidget(QLabel(label))
            row.addWidget(spin, 1)
        row.addWidget(color_btn)
        return row

    def _caption_clip_mode(self) -> bool:
        return self.clip is not None and not self._caption_scope_common

    def _on_caption_scope_changed(self, index: int) -> None:
        self._caption_scope_common = index == 1
        self._sync_style_controls()

    def _reset_caption_style(self) -> None:
        if self.clip is None:
            return
        for name in CAPTION_STYLE_FIELDS:
            setattr(self.clip, name, None)
        self._sync_style_controls()
        self.relayout()
        self._emit()

    def _on_text_style(self, field: str, value) -> None:
        target = self.clip if field in CAPTION_STYLE_FIELDS and self._caption_clip_mode() else self.settings
        setattr(target, field, value)
        self._sync_style_controls()
        self.relayout()
        if target is self.clip:
            self._emit()
        else:
            self.style_changed.emit()

    def _color_button(self, color: str, handler) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(28, 22)
        btn.setToolTip("색상 변경")
        self._paint_color_button(btn, color)
        btn.clicked.connect(handler)
        return btn

    @staticmethod
    def _paint_color_button(btn: QPushButton, color: str) -> None:
        btn.setStyleSheet(f"background-color: {color}; border: 1px solid #888888;")

    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self.play_btn, self.zoom_slider, self.pre_spin, self.post_spin, self.caption_edit, self.reset_btn,
                  self.seek_slider, self.back5_btn, self.back1_btn, self.fwd1_btn, self.fwd5_btn):
            w.setEnabled(on)

    def _apply_layout(self) -> None:
        self.output_layout = layout_for(self.settings.output_mode)
        layout = self.output_layout
        self.scene.setSceneRect(0, 0, layout["canvas_w"], layout["canvas_h"])
        self.bg_item.setRect(self.scene.sceneRect())
        self.square.setRect(0, 0, layout["frame_w"], layout["frame_h"])
        self.square.setPos(layout["frame_x"], layout["frame_y"])
        self.full_item.setSize(QSizeF(layout["canvas_w"], layout["canvas_h"]))
        for spin, maximum in ((self.title_x_spin, layout["canvas_w"]), (self.caption_x_spin, layout["canvas_w"]),
                              (self.title_y_spin, layout["canvas_h"]), (self.caption_y_spin, layout["canvas_h"])):
            blocked = spin.blockSignals(True)
            spin.setRange(0, maximum)
            spin.blockSignals(blocked)
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def _frame_geometry(self, zoom: float, pan_x: float, pan_y: float) -> SquareGeometry:
        layout = self.output_layout
        if self.settings.output_mode == "shorts":
            return compute_square_geometry(*self._source_size, zoom, pan_x, pan_y, layout["frame_w"])
        return compute_frame_geometry(*self._source_size, zoom, pan_x, pan_y,
                                      layout["frame_w"], layout["frame_h"], contain=True)

    def _apply_background(self) -> None:
        brush = QBrush(QColor(self.settings.background_color))
        self.bg_item.setBrush(brush)
        self.square.setBrush(brush)

    # --- 종료 ---
    def shutdown(self) -> None:
        """플레이어를 씬/비디오 아이템보다 먼저 정리한다 (테스트 종료 시 access violation 방지). 여러 번 호출해도 안전하다."""
        if self._shut_down:
            return
        self._shut_down = True
        self.player.stop()
        self.player.setVideoOutput(None)
        self.player.setSource(QUrl())
        self.bgm_player.stop()
        self.bgm_player.setSource(QUrl())

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    # --- 외부 API ---
    def set_settings(self, settings: Settings) -> None:
        self.settings = normalize_output_settings(settings)
        self._apply_layout()
        self._apply_background()
        self._sync_style_controls()
        if self.clip is not None:
            self.sync_from_clip()
        else:
            self.relayout()
            self._update_seek_range()

    def _sync_style_controls(self) -> None:
        caption = self.clip.caption_settings(self.settings) if self._caption_clip_mode() else self.settings
        self.caption_scope_combo.model().item(0).setEnabled(self.clip is not None)
        blocked = self.caption_scope_combo.blockSignals(True)
        self.caption_scope_combo.setCurrentIndex(0 if self._caption_clip_mode() else 1)
        self.caption_scope_combo.blockSignals(blocked)
        self.caption_reset_btn.setEnabled(self._caption_clip_mode() and any(getattr(self.clip, name) is not None for name in CAPTION_STYLE_FIELDS))
        values = (
            (self.title_x_spin, self.settings.title_x), (self.title_y_spin, self.settings.title_y),
            (self.caption_x_spin, caption.caption_x), (self.caption_y_spin, caption.caption_y),
            (self.title_size_spin, self.settings.title_font_size), (self.caption_size_spin, caption.caption_font_size),
        )
        for widget, value in values:
            blocked = widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(blocked)
        self.title_font_picker.set_path(self.settings.title_font_path)
        self.caption_font_picker.set_path(caption.caption_font_path)
        self._paint_color_button(self.title_color_btn, self.settings.title_color)
        self._paint_color_button(self.caption_color_btn, caption.caption_color)

    def pause(self) -> None:
        """재생 중이면 일시정지 (BGM 동기 플레이어 포함). 재생 중이 아니면 아무 일도 없다."""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶ 재생")
        self.bgm_player.pause()

    def set_title(self, title: str) -> None:
        self.title = title
        self.relayout()

    def set_clip(self, clip: Clip | None) -> None:
        if clip is not self.clip:
            self._caption_scope_common = False
        self.clip = clip
        self._sync_style_controls()
        if self._mode == "full":
            return  # 전체 모드에선 선택만 기억하고, 클립 모드로 돌아올 때 반영한다
        self.player.stop()
        self._set_controls_enabled(clip is not None)
        if clip is None:
            self.video_item.setVisible(False)
            self._loaded_path = None
            self.player.setSource(QUrl())
            self.relayout()
            self._update_seek_range()
            return
        self.video_item.setVisible(True)
        self.sync_from_clip()
        self.refresh_media()

    def refresh_media(self) -> None:
        if self._shut_down or self._mode == "full":
            return
        clip = self.clip
        if clip is None or clip.preview_path is None or not clip.preview_path.exists():
            return
        if self._loaded_path != clip.preview_path:
            self._loaded_path = clip.preview_path
            self.player.setSource(QUrl.fromLocalFile(str(clip.preview_path)))
        self._update_seek_range()
        self.player.setPosition(self._window_ms()[0])
        self.player.play()
        self.playback_started.emit()
        self.play_btn.setText("⏸ 일시정지")

    # --- 전체 미리보기 ---
    def mode(self) -> str:
        return self._mode

    def full_signature(self) -> str | None:
        return self._full_signature

    def set_full_preview(self, path: Path, signature: str) -> None:
        self._full_path, self._full_signature, self._full_stale = path, signature, False
        self._full_duration_ms = 0
        self.full_mode_btn.setEnabled(True)
        self.full_status.setText("전체 미리보기 최신")
        self.set_mode("full")

    def mark_full_preview_stale(self) -> None:
        if self._full_path is not None and not self._full_stale:
            self._full_stale = True
            self.full_status.setText("클립이 바뀌었습니다 — 다시 만들기")

    def clear_full_preview(self) -> None:
        was_full = self._mode == "full"
        self._full_path = self._full_signature = None
        self._full_stale = False
        self._full_duration_ms = 0
        self.full_mode_btn.setEnabled(False)
        self.full_status.setText("")
        if was_full:
            self.set_mode("clip")

    def set_mode(self, mode: str) -> None:
        """"clip" 또는 "full". 전체 파일이 없으면 clip. 같은 모드로 다시 부르면 미디어를 다시 연다."""
        if mode == "full" and self._full_path is None:
            mode = "clip"
        self.player.stop()
        self.bgm_player.pause()
        self._mode = mode
        self.clip_mode_btn.setChecked(mode == "clip")
        self.full_mode_btn.setChecked(mode == "full")
        self.controls_panel.setEnabled(mode == "clip")
        if mode == "full":
            self.player.setLoops(1)
            self.player.setVideoOutput(self.full_item)
            self._loaded_path = self._full_path
            self.player.setSource(QUrl.fromLocalFile(str(self._full_path)))
            self.audio.setVolume(self.audio_mix.original_volume if self.audio_mix is not None else 1.0)
            self.relayout()
            self._set_controls_enabled(True)
            self._update_seek_range()
            self.player.play()
            self.playback_started.emit()
            self.play_btn.setText("⏸ 일시정지")
        else:
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            self.player.setVideoOutput(self.video_item)
            self.audio.setVolume(1.0)
            self._loaded_path = None
            self.set_clip(self.clip)

    def _on_clip_mode_clicked(self) -> None:
        if self._mode != "clip":
            self.set_mode("clip")
        else:
            self.clip_mode_btn.setChecked(True)

    def _on_full_mode_clicked(self) -> None:
        if self._mode != "full":
            self.set_mode("full")
        else:
            self.full_mode_btn.setChecked(True)

    # --- BGM 동기 재생 ---
    def set_audio_mix(self, mix: AudioMix | None) -> None:
        self.audio_mix = mix
        path = mix.bgm_path if mix is not None and mix.has_bgm() and Path(mix.bgm_path).is_file() else ""
        if path != self._bgm_loaded:
            self._bgm_loaded = path
            self._bgm_duration_ms = 0
            self.bgm_player.stop()
            self.bgm_player.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        self.bgm_audio.setVolume(mix.bgm_volume if mix is not None else 0.0)
        if self._mode == "full":
            self.audio.setVolume(mix.original_volume if mix is not None else 1.0)
            self._sync_bgm(self.player.position())

    def _on_bgm_duration(self, ms: int) -> None:
        self._bgm_duration_ms = max(0, ms)
        if self._mode == "full":
            self._sync_bgm(self.player.position())

    def _seek_bgm(self, target_ms: int) -> None:
        self.bgm_player.setPosition(target_ms)
        self._bgm_last_seek = time.monotonic()

    def _sync_bgm(self, t_ms: int) -> None:
        """영상 위치 t_ms에 맞춰 BGM을 재생/정지/재동기한다. 규칙은 bgm_position (렌더와 동일)."""
        playing = self._mode == "full" and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        mix = self.audio_mix
        if not playing or mix is None or not self._bgm_loaded:
            self.bgm_player.pause()
            return
        pos = bgm_position(t_ms / 1000, mix, self._bgm_duration_ms / 1000, self._full_duration_ms / 1000)
        if pos is None:
            self.bgm_player.pause()
            return
        target = int(pos * 1000)
        if self.bgm_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._seek_bgm(target)
            self.bgm_player.play()
            return
        drift = abs(self.bgm_player.position() - target)
        cooling = (time.monotonic() - self._bgm_last_seek) * 1000 < BGM_SEEK_COOLDOWN_MS
        if drift > BGM_RESYNC_MS and not cooling:
            self._seek_bgm(target)

    # --- 배치 ---
    def relayout(self) -> None:
        L = self.output_layout
        clip = self.clip
        zoom, pan_x, pan_y = (clip.zoom, clip.pan_x, clip.pan_y) if clip else (1.0, 0.5, 0.5)
        g = self._frame_geometry(zoom, pan_x, pan_y)
        x, y, vw, vh = video_item_placement(g)
        self.video_item.setSize(QSizeF(vw, vh))
        self.video_item.setPos(x, y)
        s = clip.caption_settings(self.settings) if clip is not None else self.settings
        title_size = min(max(s.title_font_size, FONT_SIZE_MIN), FONT_SIZE_MAX)
        caption_size = min(max(s.caption_font_size, FONT_SIZE_MIN), FONT_SIZE_MAX)
        self._apply_text_font(self.title_item, resolve_font(s, "title"), title_size)
        self._apply_text_font(self.caption_item, resolve_font(s, "caption"), caption_size)
        self._apply_text_font(self.time_item, resolve_font(s, "caption"), L["time_font"])
        title_x = min(max(s.title_x, 0), L["canvas_w"])
        caption_x = min(max(s.caption_x, 0), L["canvas_w"])
        title_y = min(max(s.title_y, 0), L["canvas_h"])
        caption_y = min(max(s.caption_y, 0), L["canvas_h"])
        self.title_item.setBrush(QBrush(QColor(s.title_color)))
        self.caption_item.setBrush(QBrush(QColor(s.caption_color)))
        self._place_text(self.title_item, wrap(self.title, title_size, L["max_text_width"], L["max_lines"]), center=title_y, center_x=title_x)
        show_time = bool(clip) and s.show_time_in_caption
        if clip:
            self._place_text(self.time_item, format_time(clip.t), top=caption_y - TIME_GAP, center_x=caption_x)
            self._place_text(self.caption_item, wrap(clip.caption, caption_size, L["max_text_width"], L["max_lines"]), top=caption_y, center_x=caption_x)
        full = self._mode == "full"
        self.title_item.setVisible(not full)
        self.time_item.setVisible(show_time and not full)
        self.caption_item.setVisible(bool(clip) and not full)
        self.square.setVisible(not full)
        self.full_item.setVisible(full)

    def _apply_text_font(self, item: QGraphicsSimpleTextItem, path: Path, size: int) -> None:
        if path not in self._font_cache:
            family = load_font_family(path)
            raw = QRawFont(str(path), 16)
            font = QFont(family if raw.isValid() else self.font_family)
            if raw.isValid():
                font.setStyleName(raw.styleName())
                font.setWeight(QFont.Weight(raw.weight()))
                font.setStyle(raw.style())
            self._font_cache[path] = font
        font = QFont(self._font_cache[path])
        font.setPixelSize(size)
        item.setFont(font)

    def _place_text(self, item: QGraphicsSimpleTextItem, text: str, top: float | None = None, center: float | None = None, center_x: float | None = None) -> None:
        item.setText(text)
        r = item.boundingRect()
        y = center - r.height() / 2 if center is not None else float(top or 0)
        center_x = self.output_layout["canvas_w"] / 2 if center_x is None else center_x
        item.setPos(center_x - r.width() / 2, y)

    def sync_from_clip(self) -> None:
        """클립 값을 컨트롤과 배치에 반영한다 (clip_changed를 내지 않음)."""
        clip = self.clip
        if clip is None:
            return
        self._sync_style_controls()
        for w in (self.zoom_slider, self.pre_spin, self.post_spin, self.caption_edit):
            w.blockSignals(True)
        self.zoom_slider.setValue(int(round(clip.zoom * 100)))
        self.zoom_label.setText(f"{clip.zoom:.2f}×")
        self.pre_spin.setValue(-clip.effective_pre(self.settings))
        self.post_spin.setValue(clip.effective_post(self.settings))
        self.caption_edit.setText(clip.caption)
        for w in (self.zoom_slider, self.pre_spin, self.post_spin, self.caption_edit):
            w.blockSignals(False)
        self.relayout()
        self._update_seek_range()

    # --- 재생 ---
    def _window_ms(self) -> tuple[int, int]:
        if self._mode == "full":
            return 0, self._full_duration_ms
        clip = self.clip
        if clip is None or clip.preview_start is None:
            return 0, 0
        s = self.settings
        return (clip.start(s) - clip.preview_start) * 1000, (clip.end(s) - clip.preview_start) * 1000

    def _on_duration(self, ms: int) -> None:
        if self._mode == "full":
            self._full_duration_ms = max(0, ms)
            self._update_seek_range()

    def _on_media_status(self, status) -> None:
        if self._mode == "full" and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_btn.setText("▶ 재생")
            self.bgm_player.pause()

    def _update_seek_range(self) -> None:
        start, end = self._window_ms()
        self.seek_slider.blockSignals(True)
        self.seek_slider.setRange(0, max(0, end - start))
        self.seek_slider.blockSignals(False)

    def _on_seek(self, value: int) -> None:
        start, end = self._window_ms()
        if end:
            self.player.setPosition(start + value)
            self._sync_bgm(start + value)

    def _seek_by(self, delta_s: int) -> None:
        start, end = self._window_ms()
        if end:
            target = seek_target(self.player.position(), delta_s, start, end)
            self.player.setPosition(target)
            self._sync_bgm(target)

    def _on_position(self, ms: int) -> None:
        start, end = self._window_ms()
        if self._mode == "clip" and end and (ms >= end or ms < start - 500):
            self.player.setPosition(start)
            return
        if not self.seek_slider.isSliderDown():
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(min(max(0, ms - start), max(0, end - start)))
            self.seek_slider.blockSignals(False)
        self.pos_label.setText(f"{format_time(max(0, ms - start) // 1000)} / {format_time(max(0, end - start) // 1000)}")
        if self._mode == "full":
            self._sync_bgm(ms)

    def _toggle_play(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶ 재생")
            self.bgm_player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState and self._loaded_path is not None:
            self.player.play()
            self.playback_started.emit()
            self.play_btn.setText("⏸ 일시정지")
            self._sync_bgm(self.player.position())
        elif self._mode == "full":
            self.player.setPosition(0)
            self.player.play()
            self.playback_started.emit()
            self.play_btn.setText("⏸ 일시정지")
        else:
            self.refresh_media()

    def _on_native_size(self, size: QSizeF) -> None:
        if size.width() > 0 and size.height() > 0:
            self._source_size = (int(size.width()), int(size.height()))
            self.relayout()

    # --- 편집 핸들러 ---
    def _emit(self) -> None:
        if self.clip is not None:
            self.clip_changed.emit(self.clip)

    def _on_drag(self, dx: float, dy: float) -> None:
        clip = self.clip
        if clip is None or self._mode == "full":
            return
        g = self._frame_geometry(clip.zoom, clip.pan_x, clip.pan_y)
        clip.pan_x, clip.pan_y = pan_after_drag(clip.pan_x, clip.pan_y, dx, dy, g, self.output_layout["frame_w"], self.output_layout["frame_h"])
        self.relayout()
        self._emit()

    def _hit_text(self, pos: QPointF) -> str | None:
        if self.title_item.isVisible() and self.title_item.sceneBoundingRect().contains(pos):
            return "title"
        if self.caption_item.isVisible() and self.caption_item.sceneBoundingRect().contains(pos):
            return "caption"
        return None

    def _on_text_drag(self, kind: str, dx: float, dy: float) -> None:
        self._caption_drag_clip = self.clip if kind == "caption" and self._caption_clip_mode() else None
        target = self._caption_drag_clip if self._caption_drag_clip is not None else self.settings
        effective = target.caption_settings(self.settings) if self._caption_drag_clip is not None else self.settings
        for axis, delta, maximum in (("x", dx, self.output_layout["canvas_w"]), ("y", dy, self.output_layout["canvas_h"])):
            field = f"{kind}_{axis}"
            value = int(round(min(max(getattr(effective, field) + delta, 0), maximum)))
            setattr(target, field, value)
        self._sync_style_controls()
        self.relayout()

    def _on_text_drag_end(self) -> None:
        clip = self._caption_drag_clip
        self._caption_drag_clip = None
        if clip is not None:
            self.clip_changed.emit(clip)
        else:
            self.style_changed.emit()

    def _on_zoom(self, value: int) -> None:
        snapped = int(round(value / 5)) * 5
        if snapped != value:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(snapped)
            self.zoom_slider.blockSignals(False)
        if self.clip is None:
            return
        self.clip.zoom = snapped / 100
        self.zoom_label.setText(f"{self.clip.zoom:.2f}×")
        self.relayout()
        self._emit()

    def _on_pre(self, value: int) -> None:
        if self.clip is None:
            return
        # Stored pre remains a lead-in duration for existing projects.
        self.clip.pre = -value
        self._update_seek_range()
        self._emit()

    def _on_post(self, value: int) -> None:
        if self.clip is None:
            return
        self.clip.post = value
        self._update_seek_range()
        self._emit()

    def _on_caption(self, text: str) -> None:
        if self.clip is None:
            return
        self.clip.caption = text.strip()
        self.relayout()
        self._emit()

    def _reset(self) -> None:
        clip = self.clip
        if clip is None:
            return
        clip.pre = clip.post = None
        for name in CAPTION_STYLE_FIELDS:
            setattr(clip, name, None)
        clip.zoom, clip.pan_x, clip.pan_y = 1.0, 0.5, 0.5
        self.sync_from_clip()
        self._emit()

    def _on_title_y(self, value: int) -> None:
        self.settings.title_y = value
        self.relayout()
        self.style_changed.emit()

    def _on_caption_y(self, value: int) -> None:
        self._on_text_style("caption_y", value)

    def _pick_color(self, current: str) -> str | None:
        c = QColorDialog.getColor(QColor(current), self, "색상 선택")
        return c.name() if c.isValid() else None

    def _on_title_color(self) -> None:
        c = self._pick_color(self.settings.title_color)
        if c is not None:
            self._set_title_color(c)

    def _on_caption_color(self) -> None:
        caption = self.clip.caption_settings(self.settings) if self._caption_clip_mode() else self.settings
        c = self._pick_color(caption.caption_color)
        if c is not None:
            self._set_caption_color(c)

    def _set_title_color(self, color: str) -> None:
        self.settings.title_color = color
        self._paint_color_button(self.title_color_btn, color)
        self.relayout()
        self.style_changed.emit()

    def _set_caption_color(self, color: str) -> None:
        self._on_text_style("caption_color", color)
