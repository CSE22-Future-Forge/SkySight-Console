"""Shared machinery for the two operator tabs.

Holds the detector/tracker pipeline, the live track table, the capture flow and
the status readouts. Subclasses (:class:`~ui.camera_tab.CameraTab`,
:class:`~ui.fusion_tab.FusionTab`) only supply sensor controls and the frame
composition rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import detect, imaging
from core.database import NewPersonDraft
from core.features import compute_embedding
from core.tracker import Tracker

from . import theme
from .widgets import Banner, Card, Pill, StatChip, VideoCanvas, labeled_row, qt_color

TRACK_COLUMNS = ("ID", "SOURCE", "CONF", "AGE", "STATUS")

# How often the render clock looks for a frame to compose. Frames arriving from
# the payloads only set a flag; composition happens here. Polling faster than
# the display refreshes costs nothing (the check is a flag read) and lets the
# view keep up with whatever rate frames actually arrive at.
RENDER_INTERVAL_MS = 8

# Content width of the control rail. The rail scrolls vertically rather than
# being squeezed, so this only has to fit the widest control row.
RAIL_W = 356
SOURCE_PILL = {
    "rgb": ("RGB", theme.RGB_C),
    "thermal": ("THERMAL", theme.THERMAL_C),
    "fusion": ("COMBINED", theme.FUSION_C),
}


@dataclass
class Finding:
    """A confirmed track frozen for operator review."""

    track_id: int
    crop_bgr: np.ndarray
    score: float
    source: str
    camera_id: int | None = None
    confirmed: bool = True
    tentative: bool = False

    def to_draft(self):
        return NewPersonDraft(
            crop_bgr=self.crop_bgr,
            embedding=compute_embedding(self.crop_bgr),
            source=self.source,
            score=self.score,
            camera_id=self.camera_id,
            track_id=self.track_id,
        )


class StreamTab(QWidget):
    """Base tab: sensor rail, live canvas, track table, capture actions."""

    captureRequested = Signal(list)
    databaseRequested = Signal()

    #: Dropdown entries as ``(label, registry_key, kwargs)``. Subclasses
    #: override this to offer more than one model for side-by-side comparison.
    DETECTOR_CHOICES = (("Ultralytics (YOLO26)", "ultralytics", {}),)

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self.tracker = Tracker()
        self.pipeline = detect.DetectionPipeline(self.tracker, max_fps=20.0)
        self._pending_drafts_cache = None
        # Detection frame vs the full-resolution frame crops are taken from.
        self._capture_proc = None
        self._capture_raw = None
        #: Last values written to each track-table row, so unchanged rows are
        #: not rewritten (and therefore not repainted) every frame.
        self._row_cache = []

        # Tone controls. Populated while the rail is built, so they must exist
        # before _build_rail_scroll() runs below.
        self._tone_sliders = {}
        self._tone_labels = {}
        self._tone_defaults = {}
        self._tone_fmts = {}
        self._tone_lut = None

        self.canvas = VideoCanvas()
        self.canvas.set_badge("")
        self.canvas.picked.connect(self._on_view_pick)
        self.canvas.focusToggled.connect(self._toggle_focus)
        self.canvas.labelsHiddenChanged.connect(self._on_labels_hidden_changed)
        self._focused = False
        #: Whether to draw the id / confidence tag above each box. Toggled from
        #: the tick on the view itself.
        self._hide_labels = False

        # Scratch frame the render path annotates in, so box drawing never
        # writes into the array a detector worker may still be reading.
        self._render_buf = None

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(13)
        root.addWidget(self._build_rail_scroll(), 0)
        root.addWidget(self._build_stage(), 1)

        # The render clock. Payload slots only store a frame and mark the view
        # dirty; everything expensive happens here, once per tick, no matter how
        # many payloads delivered. That is what stops the two-payload tab from
        # composing (and repainting) twice for every RGB frame, and it decouples
        # display smoothness from the detector's speed entirely.
        self._dirty = False
        self._render_timer = QTimer(self)
        self._render_timer.setTimerType(Qt.PreciseTimer)
        self._render_timer.setInterval(RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render_tick)
        self._render_timer.start()

    # ------------------------------------------------------------ scaffold ---
    def _build_rail_scroll(self):
        """Host the control rail in a scroll area.

        The fusion rail needs more height than a short window provides. Without
        this, Qt shrinks the cards below their minimum and the controls inside
        them (spin boxes, buttons) collapse and overlap. Scrolling keeps every
        control at its natural size at any window height.
        """
        scroll = QScrollArea()
        scroll.setWidget(self._build_rail())
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Always on so the rail width does not change as content comes and goes.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll.setFixedWidth(RAIL_W + 18)
        self.rail_scroll = scroll
        return scroll

    def _build_rail(self):
        rail = QWidget()
        rail.setMinimumWidth(RAIL_W)
        self.rail = rail
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(11)

        self.sensor_card = Card("Sensor")
        lay.addWidget(self.sensor_card)
        self._populate_sensor_card(self.sensor_card)

        self.detector_card = Card("Detector")
        self._populate_detector_card(self.detector_card)
        lay.addWidget(self.detector_card)

        self.image_card = Card("Image")
        self._populate_image_card(self.image_card)
        lay.addWidget(self.image_card)

        self.action_card = Card("Operator actions")
        self._populate_action_card(self.action_card)
        lay.addWidget(self.action_card)

        lay.addStretch(1)
        return rail

    def _build_stage(self):
        stage = QWidget()
        lay = QVBoxLayout(stage)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(self.canvas, 1)

        self.banner = Banner("")
        lay.addWidget(self.banner)

        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(8)
        self.chip_fps = StatChip("Stream FPS", "--")
        self.chip_res = StatChip("Resolution", "--")
        self.chip_infer = StatChip("Inference", "--")
        self.chip_tracks = StatChip("Confirmed tracks", "0", theme.WARN)
        self.chip_db = StatChip("Database", "0 people")
        for chip in (
            self.chip_fps,
            self.chip_res,
            self.chip_infer,
            self.chip_tracks,
            self.chip_db,
        ):
            self.chip_row.addWidget(chip)
        self.chip_row.addStretch(1)
        lay.addLayout(self.chip_row)

        self.track_card = Card("Live tracks")
        self.track_card.finish_header()
        self._populate_track_card(self.track_card)
        lay.addWidget(self.track_card)
        return stage

    # ------------------------------------------------- overridable sections ---
    def _populate_sensor_card(self, card):
        pass

    def _populate_action_card(self, card):
        self.btn_capture = QPushButton("CAPTURE FINDINGS")
        self.btn_capture.setProperty("variant", "primary")
        self.btn_capture.setEnabled(False)
        self.btn_capture.clicked.connect(self.request_capture)
        self.btn_capture.setToolTip(
            "Freeze the confirmed tracks and open the review window to select "
            "which people go into the database."
        )
        card.add(self.btn_capture)

        row = QHBoxLayout()
        row.setSpacing(7)
        self.btn_clear = QPushButton("CLEAR")
        self.btn_clear.setProperty("variant", "ghost")
        self.btn_clear.clicked.connect(self.clear_tracks)
        self.btn_database = QPushButton("DATABASE")
        self.btn_database.setProperty("variant", "ghost")
        self.btn_database.clicked.connect(self.databaseRequested.emit)
        row.addWidget(self.btn_clear)
        row.addWidget(self.btn_database)
        holder = QWidget()
        holder.setLayout(row)
        card.add(holder)

    def _populate_detector_card(self, card):
        self.detector_combo = QComboBox()
        for label, key, kwargs in self.DETECTOR_CHOICES:
            try:
                cls = detect.build(key)
            except KeyError:
                continue
            available = detect.build(key).available() if cls.requires_model else True
            text = label if available else f"{label} (not installed)"
            self.detector_combo.addItem(text, (label, key, kwargs))
            row = self.detector_combo.count() - 1
            if not available:
                self.detector_combo.setItemData(row, qt_color("#6d7f95"), Qt.ForegroundRole)
                self.detector_combo.setItemData(row, False, Qt.UserRole - 1)
        self.detector_combo.setCurrentIndex(0)
        self.detector_combo.currentIndexChanged.connect(self._on_detector_changed)
        card.add(labeled_row("Model", self.detector_combo))

        conf_row = QWidget()
        conf_lay = QHBoxLayout(conf_row)
        conf_lay.setContentsMargins(0, 0, 0, 0)
        conf_lay.setSpacing(8)
        self.conf_slider = QSlider(Qt.Horizontal)
        self.conf_slider.setRange(10, 95)
        # Kept at the original 0.65. Dropping this was tried and reverted: it
        # admits roughly three times as many detections, most of them marginal
        # boxes sitting on things that are not people, which the tracker then
        # holds and coasts around. Lower it deliberately — the slider is right
        # here — when recall matters more than a clean picture, e.g. to make
        # the bundled QS-YOLOv8s fire at all (it scores people around 0.1-0.3).
        self.conf_slider.setValue(65)
        self.conf_value = QLabel("0.65")
        self.conf_value.setFixedWidth(50)
        self.conf_value.setProperty("role", "mono")
        self.conf_slider.valueChanged.connect(self._on_conf_changed)
        conf_lay.addWidget(self.conf_slider, 1)
        conf_lay.addWidget(self.conf_value)
        card.add(labeled_row("Confidence", conf_row))

    def _populate_image_card(self, card):
        """Brightness / contrast / exposure, applied before detection.

        These are not just a display filter — the corrected frame is what the
        detector is handed, so lifting a dark or flat scene genuinely changes
        what the model finds in it.
        """
        card.add(
            self._slider_row(
                card, "Brightness", imaging.TONE_BRIGHTNESS_RANGE, 0, "%+d"
            )
        )
        card.add(
            self._slider_row(
                card, "Contrast", imaging.TONE_CONTRAST_RANGE, 100, "%d%%"
            )
        )
        card.add(
            self._slider_row(
                card, "Exposure", imaging.TONE_EXPOSURE_RANGE, 100, "%d%%"
            )
        )

        self.btn_tone_reset = QPushButton("RESET IMAGE")
        self.btn_tone_reset.setProperty("variant", "ghost")
        self.btn_tone_reset.setToolTip("Return brightness, contrast and exposure to default.")
        self.btn_tone_reset.clicked.connect(self._reset_tone)
        card.add(self.btn_tone_reset)

    def _slider_row(self, card, label, limits, default, fmt):
        """One labelled slider with a live readout, wired to the tone LUT."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(limits[0], limits[1])
        slider.setValue(default)
        value = QLabel(fmt % default)
        value.setFixedWidth(50)
        value.setProperty("role", "mono")

        key = label.lower()
        self._tone_sliders[key] = slider
        self._tone_labels[key] = value
        self._tone_defaults[key] = default
        self._tone_fmts[key] = fmt
        slider.valueChanged.connect(
            lambda v, k=key, f=fmt: self._on_tone_changed(k, v, f)
        )

        lay.addWidget(slider, 1)
        lay.addWidget(value)
        return labeled_row(label, row)

    def _on_tone_changed(self, key, value, fmt):
        self._tone_labels[key].setText(fmt % value)
        self._rebuild_tone_lut()
        self._mark_dirty()

    def _reset_tone(self):
        for key, slider in self._tone_sliders.items():
            slider.blockSignals(True)
            slider.setValue(self._tone_defaults[key])
            slider.blockSignals(False)
        self._rebuild_tone_lut()
        for key, slider in self._tone_sliders.items():
            self._tone_labels[key].setText(self._tone_fmts[key] % slider.value())
        self._mark_dirty()

    def _rebuild_tone_lut(self):
        self._tone_lut = imaging.tone_lut(
            brightness=self._tone_sliders["brightness"].value(),
            contrast=self._tone_sliders["contrast"].value(),
            exposure=self._tone_sliders["exposure"].value(),
        )

    def _apply_tone(self, frame):
        """Tone-correct a frame on its way to the detector and the display."""
        if self._tone_lut is None:
            return frame
        return imaging.apply_tone(frame, self._tone_lut)

    def _populate_track_card(self, card):
        self.btn_select_all = QPushButton("SELECT ALL")
        self.btn_select_all.setProperty("variant", "ghost")
        self.btn_select_all.setEnabled(False)
        self.btn_select_all.clicked.connect(lambda: self._toggle_all_rows(True))
        self.btn_select_none = QPushButton("CLEAR")
        self.btn_select_none.setProperty("variant", "ghost")
        self.btn_select_none.setEnabled(False)
        self.btn_select_none.clicked.connect(lambda: self._toggle_all_rows(False))
        card.add_row(self.btn_select_none)
        card.add_row(self.btn_select_all)

        self.track_table = QTableWidget(0, len(TRACK_COLUMNS))
        self.track_table.setHorizontalHeaderLabels(list(TRACK_COLUMNS))
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.track_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setFixedHeight(168)
        self.track_table.itemSelectionChanged.connect(self._on_row_selection)
        header = self.track_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.track_table.setColumnWidth(0, 74)
        self.track_table.setColumnWidth(1, 92)
        card.add(self.track_table)

    # -------------------------------------------------------------- helpers ---
    def _on_conf_changed(self, value):
        conf = value / 100.0
        self.conf_value.setText(f"{conf:.2f}")
        if self.pipeline.detector is not None:
            self.pipeline.detector.conf = conf

    def _build_detector(self, entry):
        """Instantiate a dropdown entry: ``(label, key, kwargs)``."""
        _label, key, kwargs = entry
        build_kwargs = dict(kwargs)
        build_kwargs["conf"] = self.conf_slider.value() / 100.0
        return detect.build(key, **build_kwargs)

    def _on_detector_changed(self, _index):
        entry = self.detector_combo.currentData()
        if not entry:
            return
        try:
            detector = self._build_detector(entry)
        except KeyError:
            return
        if getattr(detector, "requires_model", False) and not detector.available():
            self.banner.show_message(
                "Ultralytics is not installed yet. Select another model, or "
                "install torch + ultralytics to enable detection.",
                "warn",
            )
            self.detector_combo.blockSignals(True)
            self.detector_combo.setCurrentIndex(0)
            self.detector_combo.blockSignals(False)
            entry = self.detector_combo.currentData()
            detector = self._build_detector(entry)
        self._detector_label = entry[0]
        self.pipeline.set_detector(detector)
        self._refresh_badge()

    def _refresh_badge(self):
        detector = self.pipeline.detector
        label = getattr(self, "_detector_label", None) or (
            detector.label if detector else "No detector"
        )
        if detector is not None and getattr(detector, "requires_model", False):
            self.canvas.set_badge(label, theme.WARN)
        else:
            self.canvas.set_badge(label, theme.TEXT_MUTED)

    def _toggle_all_rows(self, checked):
        self.track_table.clearSelection()
        if checked:
            self.track_table.selectAll()
        self._update_capture_button()

    def _on_row_selection(self):
        self._update_capture_button()

    def _update_capture_button(self):
        selected = len(self.track_table.selectedItems()) > 0
        rows = self.track_table.rowCount()
        self.btn_capture.setEnabled(selected or rows > 0)
        self.btn_select_all.setEnabled(rows > 0)
        self.btn_select_none.setEnabled(bool(self.track_table.selectedItems()))

    def refresh_db_chip(self):
        self.chip_db.set_value(f"{self.db.count()} people")

    def clear_tracks(self):
        self.tracker.reset()
        self.pipeline.detections = []
        self._row_cache.clear()
        self._update_track_table([])
        self._update_capture_button()

    # ------------------------------------------------------- view interaction ---
    def _view_to_frame(self, x, y):
        """Map a click on the canvas to detector-frame coordinates.

        The base tab draws the frame one-to-one, so this is the identity; the
        fusion tab overrides it to undo its composite transform.
        """
        return x, y

    def _on_view_pick(self, x, y):
        """Open the add-people window for the detected person that was clicked."""
        fx, fy = self._view_to_frame(x, y)
        hit = None
        hit_area = None
        for track in self.tracker.snapshot():
            if not track.confirmed:
                continue
            x1, y1, x2, y2 = track.box
            if x1 <= fx <= x2 and y1 <= fy <= y2:
                area = (x2 - x1) * (y2 - y1)
                if hit is None or area < hit_area:
                    hit, hit_area = track, area
        if hit is None:
            return
        findings = self.build_findings([hit.track_id])
        if findings:
            self.captureRequested.emit(findings)

    def _toggle_focus(self):
        """Hide the control rail and track table so the video fills the window."""
        self._focused = not getattr(self, "_focused", False)
        self.rail_scroll.setVisible(not self._focused)
        self.track_card.setVisible(not self._focused)
        self.canvas.set_focus_state(self._focused)

    # --------------------------------------------------------- render clock ---
    def _on_labels_hidden_changed(self, hidden):
        self._hide_labels = bool(hidden)
        # Repaint what is already on screen rather than waiting for a new frame.
        self._mark_dirty()

    def _mark_dirty(self):
        """Called by a payload slot: a newer frame is available to compose."""
        self._dirty = True

    def _render_tick(self):
        if not self._dirty:
            return
        self._dirty = False
        self._render_frame()

    def _render_frame(self):
        """Compose and display the newest frame. Implemented by each tab."""
        raise NotImplementedError

    def _render_buffer_for(self, image):
        """A reusable copy of ``image`` for the annotation overlays.

        The overlays draw in place now, and ``image`` is often the very array
        handed to a detector worker, so they get their own buffer instead.
        """
        if image is None:
            return None
        if image.ndim != 3 or image.shape[2] != 3:
            # Not the 3-channel BGR the overlays and canvas expect; hand back a
            # converted copy rather than scribbling on the caller's frame.
            return np.ascontiguousarray(image[:, :, :3]) if image.ndim == 3 else image
        h, w = image.shape[:2]
        buf = self._render_buf
        if buf is None or buf.shape[0] != h or buf.shape[1] != w:
            buf = np.empty((h, w, 3), dtype=np.uint8)
            self._render_buf = buf
        np.copyto(buf, image)
        return buf

    def _consume_frame(self, frame_bgr):
        """Advance detection + tracking; returns the full track snapshot.

        Never blocks: the pipeline submits to its worker and hands back whichever
        tracks are current, so this stays cheap even when the model is slow.
        """
        self.pipeline.step(frame_bgr)
        return self.tracker.snapshot()

    def _update_stats(self, fps=None, resolution=None):
        if fps is not None:
            self.chip_fps.set_value(f"{fps:4.1f}", theme.OK if fps >= 20 else theme.WARN)
        if resolution is not None:
            self.chip_res.set_value(f"{resolution[0]}x{resolution[1]}")

    def _update_infer_chip(self):
        detector = self.pipeline.detector
        if detector is None:
            self.chip_infer.set_value("--")
            return
        # The pipeline times every backend itself, threaded or not — a detector
        # no longer needs to report its own wall-clock cost.
        ms = self.pipeline.inference_ms
        if ms <= 0:
            self.chip_infer.set_value("idle", theme.TEXT_MUTED)
            return
        color = theme.OK if ms < 60 else (theme.WARN if ms < 200 else theme.DANGER)
        self.chip_infer.set_value(f"{ms:.0f} ms", color)

    def _update_track_table(self, tracks):
        """Refresh the live track table, touching only the cells that changed.

        Rebuilding every row (new items, fonts and colours) on every frame makes
        Qt re-emit dataChanged for the whole table and repaint it, which is pure
        overhead when the tracks have not moved. Rows are now reused and compared
        against what was last written.
        """
        confirmed = [t for t in tracks if t.confirmed]
        self.chip_tracks.set_value(
            str(len(confirmed)),
            theme.WARN if confirmed else theme.TEXT_MUTED,
        )

        table = self.track_table
        rows = len(tracks)
        if table.rowCount() != rows:
            table.setRowCount(rows)
            for row in range(rows):
                for col in range(len(TRACK_COLUMNS)):
                    if table.item(row, col) is None:
                        item = QTableWidgetItem()
                        item.setTextAlignment(Qt.AlignCenter)
                        table.setItem(row, col, item)
            del self._row_cache[rows:]

        cache = self._row_cache
        for row, track in enumerate(tracks):
            source_text, source_color = SOURCE_PILL.get(
                track.source, (track.source.upper(), theme.TEXT_DIM)
            )
            if not track.confirmed:
                state, state_color = "TENTATIVE", theme.TEXT_MUTED
            elif track.missed > 0:
                state, state_color = "COASTING", theme.WARN
            else:
                state, state_color = "TRACKING", theme.OK
            conf_color = (
                theme.OK
                if track.score >= 0.6
                else (theme.WARN if track.score >= 0.4 else theme.TEXT_MUTED)
            )

            values = (
                f"P-{track.track_id:03d}",
                source_text,
                f"{track.score * 100:.0f}%",
                f"{track.age}",
                state,
            )
            colors = (
                theme.TEXT,
                source_color,
                conf_color,
                theme.TEXT_DIM,
                state_color,
            )
            if row < len(cache) and cache[row] == (values, colors):
                continue
            self._write_row(row, values, colors, bold_first=True)
            if row < len(cache):
                cache[row] = (values, colors)
            else:
                cache.append((values, colors))

        self._update_capture_button()

    def _write_row(self, row, values, colors, bold_first=False):
        table = self.track_table
        for col, text in enumerate(values):
            item = table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, col, item)
            if item.text() != text:
                item.setText(text)
            if col == 0 and bold_first:
                font = item.font()
                if not font.bold():
                    font.setBold(True)
                    item.setFont(font)
            if colors[col] is not None:
                item.setForeground(qt_color(colors[col]))

    # -------------------------------------------------------------- capture ---
    def current_tracks(self):
        return [t for t in self.tracker.snapshot() if t.confirmed]

    def request_capture(self):
        selected = self._selected_track_ids()
        findings = self.build_findings(selected or None)
        if not findings:
            self.banner.show_message(
                "Nothing to capture — no confirmed tracks on screen.", "warn"
            )
            return
        self.captureRequested.emit(findings)

    def _selected_track_ids(self):
        ids = set()
        for index in self.track_table.selectionModel().selectedRows():
            item = self.track_table.item(index.row(), 0)
            if item:
                try:
                    ids.add(int(item.text().split("-")[-1]))
                except ValueError:
                    continue
        return ids

    @staticmethod
    def _raw_scale(proc, raw):
        """Scale from the detector frame to the full-resolution crop frame."""
        if proc is None or raw is None:
            return 1.0, 1.0
        ph, pw = proc.shape[:2]
        rh, rw = raw.shape[:2]
        if not pw or not ph:
            return 1.0, 1.0
        return rw / float(pw), rh / float(ph)

    @staticmethod
    def _scale_box(box, scale):
        sx, sy = scale
        x1, y1, x2, y2 = box
        return (x1 * sx, y1 * sy, x2 * sx, y2 * sy)

    def build_findings(self, track_ids=None):
        """Freeze tracks into reviewable findings with crops + features.

        The crop is taken from the full-resolution source frame, with the track
        box scaled up from the (possibly capped) detection frame, so the stored
        thumbnail keeps the original stream's detail.
        """
        source_image = self._capture_image()
        scale = self._raw_scale(getattr(self, "_capture_proc", None), source_image)
        findings = []
        for track in self.current_tracks():
            if track_ids and track.track_id not in track_ids:
                continue
            if source_image is None:
                continue
            crop = imaging.fit_to_crop(source_image, self._scale_box(track.box, scale))
            findings.append(
                Finding(
                    track_id=track.track_id,
                    crop_bgr=crop,
                    score=track.score,
                    source=track.source,
                    camera_id=self._active_camera_id(),
                )
            )
        return findings

    def _capture_image(self):
        raise NotImplementedError

    def _active_camera_id(self):
        return None

    def shutdown(self):
        raise NotImplementedError