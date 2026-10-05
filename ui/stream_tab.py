"""Shared machinery for the two operator tabs.

Holds the detector/tracker pipeline, the live track table, the capture flow and
the status readouts. Subclasses (:class:`~ui.camera_tab.CameraTab`,
:class:`~ui.fusion_tab.FusionTab`) only supply sensor controls and the frame
composition rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, Signal
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
    temp_c: float | None = None
    camera_id: int | None = None
    confirmed: bool = True
    tentative: bool = False

    def to_draft(self):
        return NewPersonDraft(
            crop_bgr=self.crop_bgr,
            embedding=compute_embedding(self.crop_bgr),
            source=self.source,
            score=self.score,
            temp_c=self.temp_c,
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

        self.canvas = VideoCanvas()
        self.canvas.set_badge("")

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(13)
        root.addWidget(self._build_rail_scroll(), 0)
        root.addWidget(self._build_stage(), 1)

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
        self.conf_slider.setValue(65)
        self.conf_value = QLabel("0.65")
        self.conf_value.setFixedWidth(50)
        self.conf_value.setProperty("role", "mono")
        self.conf_slider.valueChanged.connect(self._on_conf_changed)
        conf_lay.addWidget(self.conf_slider, 1)
        conf_lay.addWidget(self.conf_value)
        card.add(labeled_row("Confidence", conf_row))

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
        self._refresh_track_table([])
        self._update_capture_button()

    # --------------------------------------------------------- frame cycle ---
    def _consume_frame(self, frame_bgr):
        """Run detection + tracking on a frame; returns confirmed tracks."""
        tracks = self.pipeline.step(frame_bgr)
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
        # Detectors that run off the UI thread (e.g. the fusion model) report
        # their own wall-clock time; synchronous detectors leave that at 0 and
        # rely on the pipeline's own measurement.
        ms = getattr(detector, "inference_ms", 0.0) or self.pipeline.inference_ms
        if ms <= 0:
            self.chip_infer.set_value("idle", theme.TEXT_MUTED)
            return
        color = theme.OK if ms < 60 else (theme.WARN if ms < 200 else theme.DANGER)
        self.chip_infer.set_value(f"{ms:.0f} ms", color)

    def _update_track_table(self, tracks):
        confirmed = [t for t in tracks if t.confirmed]
        self.chip_tracks.set_value(
            str(len(confirmed)),
            theme.WARN if confirmed else theme.TEXT_MUTED,
        )

        self.track_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            source_text, source_color = SOURCE_PILL.get(
                track.source, (track.source.upper(), theme.TEXT_DIM)
            )

            id_item = QTableWidgetItem(f"P-{track.track_id:03d}")
            id_item.setForeground(Qt.GlobalColor.white)
            font = id_item.font()
            font.setBold(True)
            id_item.setFont(font)

            source_item = QTableWidgetItem(source_text)
            source_item.setForeground(qt_color(source_color))

            conf_item = QTableWidgetItem(f"{track.score * 100:.0f}%")
            conf_item.setForeground(
                qt_color(
                    theme.OK
                    if track.score >= 0.6
                    else (theme.WARN if track.score >= 0.4 else theme.TEXT_MUTED)
                )
            )

            age_item = QTableWidgetItem(f"{track.age}")
            age_item.setForeground(qt_color(theme.TEXT_DIM))

            if not track.confirmed:
                state, state_color = "TENTATIVE", theme.TEXT_MUTED
            elif track.missed > 0:
                state, state_color = "COASTING", theme.WARN
            else:
                state, state_color = "TRACKING", theme.OK
            state_item = QTableWidgetItem(state)
            state_item.setForeground(qt_color(state_color))

            for col, item in enumerate(
                (id_item, source_item, conf_item, age_item, state_item)
            ):
                item.setTextAlignment(Qt.AlignCenter)
                self.track_table.setItem(row, col, item)

        self._update_capture_button()

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

    def build_findings(self, track_ids=None):
        """Freeze tracks into reviewable findings with crops + features."""
        source_image = self._capture_image()
        findings = []
        for track in self.current_tracks():
            if track_ids and track.track_id not in track_ids:
                continue
            if source_image is None:
                continue
            crop = imaging.fit_to_crop(source_image, track.box)
            findings.append(
                Finding(
                    track_id=track.track_id,
                    crop_bgr=crop,
                    score=track.score,
                    source=track.source,
                    temp_c=track.temp_c,
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