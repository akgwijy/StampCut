"""최종 결과와 같은 9:16 미리보기. 정방형 안 영상을 드래그·줌으로 조절한다."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QColorDialog,
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

from stampcut.core.models import Clip, Settings
from stampcut.core.renderer import LAYOUT, TIME_GAP, ZOOM_MAX, ZOOM_MIN, SquareGeometry, compute_square_geometry
from stampcut.core.textwrap_kr import wrap
from stampcut.core.timestamps import format_time

_S = LAYOUT["square"]


def pan_after_drag(pan_x: float, pan_y: float, dx: float, dy: float, g: SquareGeometry, square: int = _S) -> tuple[float, float]:
    """영상의 정방형 내 위치 = (square - sw) * pan 이므로, 위치 변화량을 pan으로 되돌린다."""
    denom_x, denom_y = square - g.sw, square - g.sh
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
        self._on_text_drag = on_text_drag or (lambda kind, dy: None)
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
                self._on_text_drag(self._target, d.y())
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

    def __init__(self, settings: Settings, font_family: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.font_family = font_family
        self.clip: Clip | None = None
        self.title = ""
        self._source_size = (1920, 1080)
        self._loaded_path: Path | None = None

        L = LAYOUT
        self._shut_down = False
        self.scene = QGraphicsScene(0, 0, L["canvas_w"], L["canvas_h"], self)
        self.bg_item = self.scene.addRect(QRectF(0, 0, L["canvas_w"], L["canvas_h"]), QPen(Qt.NoPen), QBrush(QColor(settings.background_color)))
        self.square = QGraphicsRectItem(0, 0, _S, _S)
        self.square.setPen(QPen(Qt.NoPen))
        self.square.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.square.setPos(0, L["square_y"])
        self.scene.addItem(self.square)
        self._apply_background()
        self.video_item = QGraphicsVideoItem(self.square)
        self.video_item.nativeSizeChanged.connect(self._on_native_size)

        self.title_item = self._text_item(L["title_font"], self.settings.title_color)
        self.time_item = self._text_item(L["time_font"], L["time_color"])
        self.caption_item = self._text_item(L["caption_font"], self.settings.caption_color, border=L["caption_border"])

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_item)
        self.player.positionChanged.connect(self._on_position)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)

        self.view = _DragView(self.scene, self._on_drag, self._hit_text, self._on_text_drag, self._on_text_drag_end)
        self.play_btn = QPushButton("▶ 재생")
        self.play_btn.clicked.connect(self._toggle_play)
        self.pos_label = QLabel("0:00 / 0:00")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
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
        self.pre_spin.setRange(0, 120)
        self.pre_spin.setSuffix("초")
        self.pre_spin.setKeyboardTracking(False)  # 타이핑 중간값마다 미리보기를 다시 받지 않도록
        self.pre_spin.valueChanged.connect(self._on_pre)
        self.post_spin = QSpinBox()
        self.post_spin.setRange(0, 120)
        self.post_spin.setSuffix("초")
        self.post_spin.setKeyboardTracking(False)
        self.post_spin.valueChanged.connect(self._on_post)
        self.caption_edit = QLineEdit()
        self.caption_edit.textChanged.connect(self._on_caption)
        self.title_color_btn = self._color_button(self.settings.title_color, self._on_title_color)
        self.title_y_spin = self._y_spin(self.settings.title_y, self._on_title_y)
        self.caption_color_btn = self._color_button(self.settings.caption_color, self._on_caption_color)
        self.caption_y_spin = self._y_spin(self.settings.caption_y, self._on_caption_y)
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
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Y"))
        trow.addWidget(self.title_y_spin)
        trow.addWidget(self.title_color_btn)
        trow.addStretch(1)
        controls.addRow("타이틀", trow)
        crow = QHBoxLayout()
        crow.addWidget(self.caption_edit, 1)
        crow.addWidget(self.caption_y_spin)
        crow.addWidget(self.caption_color_btn)
        controls.addRow("자막", crow)
        controls.addRow(self.reset_btn)

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
        spin.setRange(0, LAYOUT["canvas_h"])
        spin.setKeyboardTracking(False)
        spin.setValue(value)
        spin.valueChanged.connect(handler)
        return spin

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

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    # --- 외부 API ---
    def set_settings(self, settings: Settings) -> None:
        self.settings = settings
        self._apply_background()
        self._sync_style_controls()
        self.relayout()

    def _sync_style_controls(self) -> None:
        for w in (self.title_y_spin, self.caption_y_spin):
            w.blockSignals(True)
        self.title_y_spin.setValue(self.settings.title_y)
        self.caption_y_spin.setValue(self.settings.caption_y)
        for w in (self.title_y_spin, self.caption_y_spin):
            w.blockSignals(False)
        self._paint_color_button(self.title_color_btn, self.settings.title_color)
        self._paint_color_button(self.caption_color_btn, self.settings.caption_color)

    def set_title(self, title: str) -> None:
        self.title = title
        self.relayout()

    def set_clip(self, clip: Clip | None) -> None:
        self.player.stop()
        self.clip = clip
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
        if self._shut_down:
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
        self.play_btn.setText("⏸ 일시정지")

    # --- 배치 ---
    def relayout(self) -> None:
        L = LAYOUT
        clip = self.clip
        zoom, pan_x, pan_y = (clip.zoom, clip.pan_x, clip.pan_y) if clip else (1.0, 0.5, 0.5)
        w, h = self._source_size
        g = compute_square_geometry(w, h, zoom, pan_x, pan_y, _S)
        x, y, vw, vh = video_item_placement(g, _S)
        self.video_item.setSize(QSizeF(vw, vh))
        self.video_item.setPos(x, y)
        s = self.settings
        title_y = min(max(s.title_y, 0), L["canvas_h"])
        caption_y = min(max(s.caption_y, 0), L["canvas_h"])
        self.title_item.setBrush(QBrush(QColor(s.title_color)))
        self.caption_item.setBrush(QBrush(QColor(s.caption_color)))
        self._place_text(self.title_item, wrap(self.title, L["title_font"], L["max_text_width"], L["max_lines"]), center=title_y)
        show_time = bool(clip) and s.show_time_in_caption
        self.time_item.setVisible(show_time)
        if clip:
            self._place_text(self.time_item, format_time(clip.t), top=caption_y - TIME_GAP)
            self._place_text(self.caption_item, wrap(clip.caption, L["caption_font"], L["max_text_width"], L["max_lines"]), top=caption_y)
        self.caption_item.setVisible(bool(clip))

    def _place_text(self, item: QGraphicsSimpleTextItem, text: str, top: float | None = None, center: float | None = None) -> None:
        item.setText(text)
        r = item.boundingRect()
        y = center - r.height() / 2 if center is not None else float(top or 0)
        item.setPos((LAYOUT["canvas_w"] - r.width()) / 2, y)

    def sync_from_clip(self) -> None:
        """클립 값을 컨트롤과 배치에 반영한다 (clip_changed를 내지 않음)."""
        clip = self.clip
        if clip is None:
            return
        for w in (self.zoom_slider, self.pre_spin, self.post_spin, self.caption_edit):
            w.blockSignals(True)
        self.zoom_slider.setValue(int(round(clip.zoom * 100)))
        self.zoom_label.setText(f"{clip.zoom:.2f}×")
        self.pre_spin.setValue(clip.effective_pre(self.settings))
        self.post_spin.setValue(clip.effective_post(self.settings))
        self.caption_edit.setText(clip.caption)
        for w in (self.zoom_slider, self.pre_spin, self.post_spin, self.caption_edit):
            w.blockSignals(False)
        self.relayout()
        self._update_seek_range()

    # --- 재생 ---
    def _window_ms(self) -> tuple[int, int]:
        clip = self.clip
        if clip is None or clip.preview_start is None:
            return 0, 0
        s = self.settings
        return (clip.start(s) - clip.preview_start) * 1000, (clip.end(s) - clip.preview_start) * 1000

    def _update_seek_range(self) -> None:
        start, end = self._window_ms()
        self.seek_slider.blockSignals(True)
        self.seek_slider.setRange(0, max(0, end - start))
        self.seek_slider.blockSignals(False)

    def _on_seek(self, value: int) -> None:
        start, end = self._window_ms()
        if end:
            self.player.setPosition(start + value)

    def _seek_by(self, delta_s: int) -> None:
        start, end = self._window_ms()
        if end:
            self.player.setPosition(seek_target(self.player.position(), delta_s, start, end))

    def _on_position(self, ms: int) -> None:
        start, end = self._window_ms()
        if end and (ms >= end or ms < start - 500):
            self.player.setPosition(start)
            return
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(min(max(0, ms - start), max(0, end - start)))
        self.seek_slider.blockSignals(False)
        self.pos_label.setText(f"{format_time(max(0, ms - start) // 1000)} / {format_time(max(0, end - start) // 1000)}")

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶ 재생")
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState and self._loaded_path is not None:
            self.player.play()
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
        if clip is None:
            return
        w, h = self._source_size
        g = compute_square_geometry(w, h, clip.zoom, clip.pan_x, clip.pan_y, _S)
        clip.pan_x, clip.pan_y = pan_after_drag(clip.pan_x, clip.pan_y, dx, dy, g, _S)
        self.relayout()
        self._emit()

    def _hit_text(self, pos: QPointF) -> str | None:
        if self.title_item.isVisible() and self.title_item.sceneBoundingRect().contains(pos):
            return "title"
        if self.caption_item.isVisible() and self.caption_item.sceneBoundingRect().contains(pos):
            return "caption"
        return None

    def _on_text_drag(self, kind: str, dy: float) -> None:
        L = LAYOUT
        if kind == "title":
            self.settings.title_y = int(round(min(max(self.settings.title_y + dy, 0), L["canvas_h"])))
        else:
            self.settings.caption_y = int(round(min(max(self.settings.caption_y + dy, 0), L["canvas_h"])))
        self._sync_style_controls()
        self.relayout()

    def _on_text_drag_end(self) -> None:
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
        self.clip.pre = value
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
        clip.zoom, clip.pan_x, clip.pan_y = 1.0, 0.5, 0.5
        self.sync_from_clip()
        self._emit()

    def _on_title_y(self, value: int) -> None:
        self.settings.title_y = value
        self.relayout()
        self.style_changed.emit()

    def _on_caption_y(self, value: int) -> None:
        self.settings.caption_y = value
        self.relayout()
        self.style_changed.emit()

    def _pick_color(self, current: str) -> str | None:
        c = QColorDialog.getColor(QColor(current), self, "색상 선택")
        return c.name() if c.isValid() else None

    def _on_title_color(self) -> None:
        c = self._pick_color(self.settings.title_color)
        if c is not None:
            self._set_title_color(c)

    def _on_caption_color(self) -> None:
        c = self._pick_color(self.settings.caption_color)
        if c is not None:
            self._set_caption_color(c)

    def _set_title_color(self, color: str) -> None:
        self.settings.title_color = color
        self._paint_color_button(self.title_color_btn, color)
        self.relayout()
        self.style_changed.emit()

    def _set_caption_color(self, color: str) -> None:
        self.settings.caption_color = color
        self._paint_color_button(self.caption_color_btn, color)
        self.relayout()
        self.style_changed.emit()
