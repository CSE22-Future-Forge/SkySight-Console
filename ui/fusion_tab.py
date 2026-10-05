"""Tab 2 — RGB + thermal fusion.

The operational view. Both payloads are independently switchable: clearing the
RGB payload releases the camera entirely and leaves a thermal-only display,
which is the night-ops requirement (no visible light, no RGB bandwidth).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

from core import imaging
from core.sources import StreamManager

from . import theme
from .stream_tab import Finding, StreamTab
from .widgets import Card, Pill, StatChip, hline, labeled_row


class FusionTab(StreamTab):
    #: The fusion-model entry runs the isolated learned pipeline: the fusion
    #: network combines the RGB and thermal payloads, then the fused-image model
    #: detects on the result. The YOLO26 entry detects on the composited view.
    DETECTOR_CHOICES = (
        (
            "Fusion Model",
            "fusion",
            {"size": (640, 512), "imgsz": 640},
        ),
        ("YOLO26", "ultralytics", {}),
    )

    def __init__(self, database, parent=None):
        self._rgb_bgr = None
        self._thermal_raw = None
        self._simulated_thermal = False
        self._rgb_video_path = None
        self._thermal_video_path = None
        self._suppress_thermal_autoopen = False

        super().__init__(database, parent)

        # Created after StreamTab.__init__ so the QObject base of this widget is
        # already initialised before it is used as a parent.
        self.rgb = StreamManager("rgb", self)
        self.thermal = StreamManager("thermal", self)

        self.rgb.frameReady.connect(self._on_rgb_frame)
        self.rgb.failed.connect(lambda m: self._on_failed("RGB", m))
        self.rgb.statusChanged.connect(lambda s: self._on_status("rgb", s))
        self.rgb.statsChanged.connect(self._on_rgb_stats)

        self.thermal.frameReady.connect(self._on_thermal_frame)
        self.thermal.failed.connect(lambda m: self._on_failed("THERMAL", m))
        self.thermal.statusChanged.connect(lambda s: self._on_status("thermal", s))
        self.thermal.statsChanged.connect(self._on_thermal_stats)

        self._on_detector_changed(0)
        self._add_thermal_chips()
        self._sync_fusion_controls()
        self.refresh_db_chip()
        self._update_capture_button()

    def _add_thermal_chips(self):
        """Thermal telemetry gets its own chips so RGB/thermal FPS stay separate."""
        self.chip_thermal_fps = StatChip("Thermal FPS", "--", theme.THERMAL_C)
        self.chip_thermal_res = StatChip("Thermal res", "--", theme.THERMAL_C)
        index = max(0, self.chip_row.count() - 1)
        self.chip_row.insertWidget(index, self.chip_thermal_fps)
        self.chip_row.insertWidget(index + 1, self.chip_thermal_res)

    def _on_detector_changed(self, index):
        super()._on_detector_changed(index)
        self._sync_detector_source()

    def _sync_detector_source(self):
        """Attribute detections to whichever payloads are actually feeding it."""
        if self.pipeline.detector is None:
            return
        rgb_live = self.chk_rgb.isChecked() and self.chk_rgb_visible.isChecked()
        if not self.chk_thermal.isChecked() or self._rgb_bgr is None:
            self.pipeline.detector.source = "rgb" if rgb_live else "thermal"
        else:
            self.pipeline.detector.source = "fusion" if rgb_live else "thermal"

    # ------------------------------------------------------------- controls ---
    def _populate_sensor_card(self, card):
        self.chk_rgb = QCheckBox("RGB payload")
        self.chk_rgb.setChecked(True)
        self.chk_rgb.setToolTip(
            "Clear this to shut the RGB camera off completely. The view drops to "
            "thermal-only."
        )
        self.chk_rgb.toggled.connect(self._on_rgb_toggle)

        self.spin_rgb_id = QSpinBox()
        self.spin_rgb_id.setRange(0, 20)
        self.spin_rgb_id.setValue(0)

        rgb_box = QLabel()
        rgb_box.setStyleSheet(f"color:{theme.RGB_C}; font-size:10px;")
        rgb_box.setText("VISIBLE SPECTRUM")
        card.add(rgb_box)
        card.add(self.chk_rgb)
        card.add(labeled_row("Camera", self.spin_rgb_id))

        self.btn_browse = QPushButton("BROWSE RGB VIDEO")
        self.btn_browse.setProperty("variant", "primary")
        self.btn_browse.setToolTip(
            "Pick the RGB video. A thermal clip with the same name plus "
            "'_thermal' is loaded automatically when present."
        )
        self.btn_browse.clicked.connect(self.browse_video)
        card.add(self.btn_browse)

        self.lbl_video = QLabel("No video selected")
        self.lbl_video.setWordWrap(True)
        self.lbl_video.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        card.add(self.lbl_video)

        self.btn_rgb = QPushButton("OPEN RGB")
        self.btn_rgb.setProperty("variant", "ghost")
        self.btn_rgb.clicked.connect(self.toggle_rgb)
        card.add(self.btn_rgb)

        card.add(hline())

        self.chk_thermal = QCheckBox("Thermal payload")
        self.chk_thermal.setChecked(True)
        self.chk_thermal.toggled.connect(self._on_thermal_toggle)

        self.chk_sim_thermal = QCheckBox("Simulate thermal from RGB")
        self.chk_sim_thermal.setChecked(True)
        self.chk_sim_thermal.setToolTip(
            "Derive a thermal-like image from the RGB frame so fusion can be "
            "tested without a radiometric camera."
        )
        self.chk_sim_thermal.toggled.connect(self._on_sim_thermal_toggle)

        self.spin_thermal_id = QSpinBox()
        self.spin_thermal_id.setRange(0, 20)
        self.spin_thermal_id.setValue(1)
        self.spin_thermal_id.setEnabled(False)

        therm_box = QLabel()
        therm_box.setStyleSheet(f"color:{theme.THERMAL_C}; font-size:10px;")
        therm_box.setText("LONG-WAVE INFRARED")
        card.add(therm_box)
        card.add(self.chk_thermal)
        card.add(self.chk_sim_thermal)
        card.add(labeled_row("Camera", self.spin_thermal_id))

        self.btn_thermal = QPushButton("OPEN THERMAL")
        self.btn_thermal.setProperty("variant", "ghost")
        self.btn_thermal.setEnabled(False)
        self.btn_thermal.clicked.connect(self.toggle_thermal)
        card.add(self.btn_thermal)

        card.finish_header()

    def _populate_action_card(self, card):
        super()._populate_action_card(card)
        self.btn_capture.setText("CAPTURE FUSED FINDINGS")

    # -------------------------------------------------------- fusion config ---
    def _build_fusion_panel(self):
        """Inserted into the rail between the sensor and detector cards."""
        self.fusion_card = Card("Fusion")
        self.fusion_card.finish_header()

        self.combo_mode = QComboBox()
        for mode in imaging.FUSION_MODES:
            self.combo_mode.addItem(mode)
        self.combo_mode.currentTextChanged.connect(self._sync_fusion_controls)
        self.fusion_card.add(labeled_row("Composite", self.combo_mode))

        self.chk_rgb_visible = QCheckBox("Show RGB layer")
        self.chk_rgb_visible.setChecked(True)
        self.chk_rgb_visible.toggled.connect(self._on_rgb_visible_toggle)
        self.fusion_card.add(self.chk_rgb_visible)

        alpha_row = QWidget()
        alpha_lay = QHBoxLayout(alpha_row)
        alpha_lay.setContentsMargins(0, 0, 0, 0)
        alpha_lay.setSpacing(8)
        self.slider_alpha = QSlider(Qt.Horizontal)
        self.slider_alpha.setRange(0, 100)
        self.slider_alpha.setValue(50)
        self.slider_alpha.setToolTip(
            "0 = pure thermal, 100 = pure RGB. Applies to the overlay blend."
        )
        self.slider_alpha.valueChanged.connect(self._on_alpha_changed)
        self.label_alpha = QLabel("50/50")
        self.label_alpha.setFixedWidth(62)
        self.label_alpha.setProperty("role", "mono")
        alpha_lay.addWidget(self.slider_alpha, 1)
        alpha_lay.addWidget(self.label_alpha)
        self.row_alpha = labeled_row("RGB mix", alpha_row)
        self.fusion_card.add(self.row_alpha)

        self.combo_colormap = QComboBox()
        for name in imaging.COLORMAPS:
            self.combo_colormap.addItem(name)
        self.combo_colormap.setCurrentText("Inferno")
        self.fusion_card.add(labeled_row("LUT", self.combo_colormap))

        span_row = QWidget()
        span_lay = QHBoxLayout(span_row)
        span_lay.setContentsMargins(0, 0, 0, 0)
        span_lay.setSpacing(6)
        self.spin_temp_lo = QDoubleSpinBox()
        self.spin_temp_lo.setRange(-40.0, 200.0)
        self.spin_temp_lo.setDecimals(0)
        self.spin_temp_lo.setValue(15.0)
        self.spin_temp_lo.setSuffix(" C")
        self.spin_temp_hi = QDoubleSpinBox()
        self.spin_temp_hi.setRange(-40.0, 200.0)
        self.spin_temp_hi.setDecimals(0)
        self.spin_temp_hi.setValue(55.0)
        self.spin_temp_hi.setSuffix(" C")
        span_lay.addWidget(self.spin_temp_lo, 1)
        span_lay.addWidget(self.spin_temp_hi, 1)
        self.row_span = labeled_row("Temp span", span_row)
        self.row_span.setToolTip(
            "Maps 8-bit thermal values onto this estimated range. Radiometric "
            "calibration replaces this estimate."
        )
        self.fusion_card.add(self.row_span)

        self.pill_rgb = Pill("RGB OFF", theme.RGB_C)
        self.pill_thermal = Pill("THERMAL", theme.THERMAL_C)
        self.pill_mode = Pill("OVERLAY", theme.FUSION_C)
        pills = QWidget()
        pills_lay = QHBoxLayout(pills)
        pills_lay.setContentsMargins(0, 0, 0, 0)
        pills_lay.setSpacing(6)
        pills_lay.addWidget(self.pill_rgb)
        pills_lay.addWidget(self.pill_thermal)
        pills_lay.addWidget(self.pill_mode)
        pills_lay.addStretch(1)
        self.fusion_card.add(pills)
        return self.fusion_card

    def _build_rail(self):
        rail = super()._build_rail()
        self.rail_layout = rail.layout()
        self.rail_layout.insertWidget(1, self._build_fusion_panel())
        return rail

    # --------------------------------------------------------------- feeds ---
    @staticmethod
    def _sibling_thermal(path):
        """Find the thermal clip matching an RGB clip: ``<stem>_thermal.<ext>``."""
        p = Path(path)
        candidate = p.with_name(f"{p.stem}_thermal{p.suffix}")
        return candidate if candidate.is_file() else None

    def browse_video(self):
        """Pick an RGB video; auto-load its ``_thermal`` sibling if it exists."""
        root = Path(__file__).resolve().parent.parent
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select RGB video",
            str(root),
            "Video files (*.mp4 *.avi *.mov *.mkv *.m4v *.wmv *.webm);;All files (*.*)",
        )
        if not path:
            return
        self._rgb_video_path = str(path)
        thermal = self._sibling_thermal(path)
        self._thermal_video_path = str(thermal) if thermal else None

        if thermal:
            self.lbl_video.setText(
                f"RGB: {Path(path).name}  |  Thermal: {thermal.name}"
            )
        else:
            self.lbl_video.setText(f"RGB: {Path(path).name}  |  Thermal: simulated")
        self.banner.hide_message()

        # Restart both payloads cleanly for the new clip.
        if self.rgb.is_running:
            self.rgb.close()
        if self.thermal.is_running:
            self.thermal.close()

        if not self.chk_rgb.isChecked():
            self.chk_rgb.setChecked(True)
        self.rgb.open(video_path=self._rgb_video_path)
        self.btn_rgb.setText("CLOSE RGB")
        self.spin_rgb_id.setEnabled(False)

        if self._thermal_video_path:
            # Real thermal clip: switch the payload onto it.
            self._suppress_thermal_autoopen = True
            self.chk_sim_thermal.setChecked(False)
            self._suppress_thermal_autoopen = False
            self._simulated_thermal = False
            if self.thermal.is_running:
                self.thermal.close()
            self.thermal.open(video_path=self._thermal_video_path)
            self.btn_thermal.setText("CLOSE THERMAL")
            self.banner.show_message("Loaded RGB and matching _thermal video.")
        else:
            # No thermal clip: keep fusing from an RGB-derived thermal image.
            self._suppress_thermal_autoopen = True
            self.chk_sim_thermal.setChecked(True)
            self._suppress_thermal_autoopen = False
            self._simulated_thermal = True
            self.banner.show_message(
                "No matching <name>_thermal video found — simulating thermal from RGB.",
                "warn",
            )
        self._sync_fusion_controls()
        self._recompose()

    def toggle_rgb(self):
        if self.rgb.is_running:
            self.rgb.close()
            self.btn_rgb.setText("OPEN RGB")
            self.spin_rgb_id.setEnabled(True)
        else:
            self.btn_rgb.setText("CLOSE RGB")
            self.spin_rgb_id.setEnabled(False)
            if self._rgb_video_path and Path(self._rgb_video_path).is_file():
                self.rgb.open(video_path=self._rgb_video_path)
                return
            root = Path(__file__).resolve().parent.parent
            v = root / "rgb_fusion.mp4"
            if not v.exists():
                v = root / "rgb_demo.mp4"
            if v.exists():
                self.rgb.open(video_path=str(v))
            else:
                self.rgb.open(self.spin_rgb_id.value())

    def toggle_thermal(self):
        if self.thermal.is_running:
            self.thermal.close()
            self.btn_thermal.setText("OPEN THERMAL")
        else:
            if self._thermal_video_path and Path(self._thermal_video_path).is_file():
                self.thermal.open(video_path=self._thermal_video_path)
                self.btn_thermal.setText("CLOSE THERMAL")
                return
            root = Path(__file__).resolve().parent.parent
            v = root / "thermal_fusion.mp4"
            if not v.exists():
                v = root / "thermal_demo.mp4"
            if v.exists():
                self.thermal.open(video_path=str(v))
            else:
                self.thermal.open(self.spin_thermal_id.value())
            self.btn_thermal.setText("CLOSE THERMAL")

    def _on_rgb_toggle(self, checked):
        if not checked:
            # Kill the RGB payload outright: close the device, drop the buffer.
            if self.rgb.is_running:
                self.rgb.close()
                self.btn_rgb.setText("OPEN RGB")
            self._rgb_bgr = None
            self.pill_rgb.set_state("RGB OFF", theme.TEXT_MUTED)
            self._sync_fusion_controls()
            self._recompose()
        elif not self.rgb.is_running:
            self.toggle_rgb()

    def _on_rgb_visible_toggle(self, _checked):
        self._sync_fusion_controls()
        self._recompose()

    def _on_thermal_toggle(self, checked):
        if not checked:
            if self.thermal.is_running:
                self.thermal.close()
                self.btn_thermal.setText("OPEN THERMAL")
            self._thermal_raw = None
            self._sync_fusion_controls()
            self._recompose()
        elif not self.chk_sim_thermal.isChecked() and not self.thermal.is_running:
            self.toggle_thermal()

    def _on_sim_thermal_toggle(self, checked):
        self._simulated_thermal = checked
        self.spin_thermal_id.setEnabled(not checked)
        self.btn_thermal.setEnabled(not checked)
        if checked:
            if self.thermal.is_running:
                self.thermal.close()
                self.btn_thermal.setText("OPEN THERMAL")
            self.pill_thermal.set_state("THERM SIM", theme.THERMAL_C)
        else:
            self.pill_thermal.set_state("THERMAL", theme.THERMAL_C)
            # When browse_video drives this change it opens the thermal clip
            # itself, so don't let the toggle grab the default file underneath.
            if self.chk_thermal.isChecked() and not self._suppress_thermal_autoopen:
                self.toggle_thermal()
        self._sync_fusion_controls()
        self._recompose()

    def _on_alpha_changed(self, value):
        self.label_alpha.setText(f"{value}/{(100 - value):02d}")
        self._recompose()

    def _sync_fusion_controls(self, *_args):
        rgb_on = self.chk_rgb.isChecked()
        therm_on = self.chk_thermal.isChecked()

        overlay = self.combo_mode.currentText() == "Overlay blend"
        self.slider_alpha.setEnabled(rgb_on and therm_on and overlay)
        self.row_alpha.setEnabled(rgb_on and therm_on and overlay)
        self.chk_rgb_visible.setEnabled(rgb_on)
        self.combo_colormap.setEnabled(therm_on)
        self.row_span.setEnabled(therm_on)

        if not rgb_on:
            self.pill_rgb.set_state("RGB OFF", theme.TEXT_MUTED)
        elif not self.chk_rgb_visible.isChecked():
            self.pill_rgb.set_state("RGB HIDDEN", theme.WARN)
        else:
            self.pill_rgb.set_state("RGB", theme.RGB_C)

        if not therm_on:
            self.pill_thermal.set_state("THERM OFF", theme.TEXT_MUTED)
        elif self._simulated_thermal:
            self.pill_thermal.set_state("THERM SIM", theme.THERMAL_C)
        else:
            self.pill_thermal.set_state("THERMAL", theme.THERMAL_C)

        self.pill_mode.set_state(self.combo_mode.currentText().upper(), theme.FUSION_C)

    def shutdown(self):
        self.rgb.close()
        self.thermal.close()
        self.pipeline.close()

    # ----------------------------------------------------------- frame cycle ---
    def _on_rgb_frame(self, frame):
        self._rgb_bgr = frame.bgr
        if self._simulated_thermal and self._thermal_raw is None:
            self._thermal_raw = imaging.simulate_thermal(frame.bgr)
        self._pipeline_cycle(frame.bgr)

    def _on_thermal_frame(self, frame):
        self._thermal_raw = frame.bgr
        self._pipeline_cycle(self._rgb_bgr)

    def _pipeline_cycle(self, rgb_bgr):
        if rgb_bgr is None and self._thermal_raw is None:
            self._update_track_table([])
            return

        self._sync_detector_source()

        detector = self.pipeline.detector
        if detector is not None and hasattr(detector, "set_thermal"):
            # The learned-fusion detector needs both payloads; the RGB frame is
            # handed to it via infer(), the thermal frame via this setter.
            detector.set_thermal(self._thermal_raw)

        if rgb_bgr is None:
            rgb_bgr = imaging.thermal_to_bgr(
                self._thermal_raw, self.combo_colormap.currentText()
            )

        self._annotate_temps()
        tracks = self._consume_frame(rgb_bgr)
        sh, sw = rgb_bgr.shape[:2]
        mode = self.combo_mode.currentText()
        use_rgb = self.chk_rgb.isChecked() and self.chk_rgb_visible.isChecked()
        use_thermal = self.chk_thermal.isChecked()
        # Compose at the detector frame's own resolution so the track boxes
        # share that coordinate space; the canvas scales the finished frame to
        # the widget. (Composing at a fixed size scaled the boxes wrongly.)
        view = imaging.fuse(
            self._rgb_bgr,
            self._thermal_raw,
            mode=mode,
            alpha=self.slider_alpha.value() / 100.0,
            use_rgb=use_rgb,
            use_thermal=use_thermal,
            colormap=self.combo_colormap.currentText(),
            size=(sw, sh),
        )
        sx, sy, ox, oy = self._view_transform(mode, use_rgb, use_thermal, (sw, sh))
        self._last_view_transform = (sx, sy, ox, oy)
        view = imaging.draw_tracks(view, tracks, scale=(sx, sy), offset=(ox, oy))
        view = imaging.stamp(
            view,
            [
                f"{imaging.time_label()}",
                self._status_line(),
            ],
        )
        self.canvas.set_frame(imaging.to_qimage(view))
        self._update_track_table(tracks)
        self._update_infer_chip()

    def _view_transform(self, mode, use_rgb, use_thermal, view_size):
        """Map detector-frame coords onto the composed view for the current mode.

        The view is composed at the detector frame's size, so the RGB frame maps
        one-to-one for the RGB, overlay and difference modes. Thermal-only and
        side-by-side change the geometry, so their boxes are scaled and offset
        to land on the right part of the composite.
        """
        vw, vh = view_size
        rgb = self._rgb_bgr
        thermal = self._thermal_raw
        rgb_used = rgb if (use_rgb and rgb is not None) else None
        therm_used = thermal if use_thermal else None

        if rgb_used is None:
            # The view is the thermal payload alone; boxes are in that space.
            if therm_used is None:
                return 1.0, 1.0, 0.0, 0.0
            th, tw = therm_used.shape[:2]
            if (tw, th) == (vw, vh):
                return 1.0, 1.0, 0.0, 0.0
            s, ox, oy = imaging.fit_scale_offset(tw, th, vw, vh)
            return s, s, ox, oy

        if therm_used is None:
            return 1.0, 1.0, 0.0, 0.0

        th, tw = therm_used.shape[:2]
        # fuse() resizes the thermal layer to the RGB height before composing.
        tw_disp = tw * (vh / float(th)) if th else tw
        if mode == "Side by side":
            s, ox, oy = imaging.fit_scale_offset(vw + tw_disp, vh, vw, vh)
            return s, s, ox, oy
        if mode == "Thermal only":
            # Boxes are in RGB-frame coords; with a shared field of view the RGB
            # fraction maps onto the thermal display, which is then letterboxed.
            s, ox, oy = imaging.fit_scale_offset(tw_disp, vh, vw, vh)
            return s * tw_disp / float(vw or 1), s, ox, oy
        # Overlay blend / RGB only / Difference render the RGB frame one-to-one.
        return 1.0, 1.0, 0.0, 0.0

    def _view_to_frame(self, x, y):
        """Undo the composite transform so clicks hit the right detector box."""
        sx, sy, ox, oy = getattr(self, "_last_view_transform", (1.0, 1.0, 0.0, 0.0))
        if not sx or not sy:
            return x, y
        return (x - ox) / sx, (y - oy) / sy

    def _status_line(self):
        parts = []
        parts.append("RGB ON" if self.chk_rgb.isChecked() else "RGB OFF")
        if self.chk_thermal.isChecked():
            parts.append("SIM THERM" if self._simulated_thermal else "THERM")
        else:
            parts.append("THERM OFF")
        return "  |  ".join(parts)

    def _on_failed(self, which, message):
        self.banner.show_message(f"{which} payload: {message}", "danger")
        if which == "RGB":
            self.btn_rgb.setText("OPEN RGB")
            self.pill_rgb.set_state("RGB ERR", theme.DANGER)
        else:
            self.btn_thermal.setText("OPEN THERMAL")
            self.pill_thermal.set_state("THERM ERR", theme.DANGER)

    def _on_status(self, which, status):
        if which == "rgb":
            if status == "connecting":
                self.pill_rgb.set_state("OPENING", theme.WARN)
            elif status == "stopped":
                self.btn_rgb.setText("OPEN RGB")
                if self.chk_rgb.isChecked():
                    self.pill_rgb.set_state("RGB OFF", theme.TEXT_MUTED)
        else:
            if status == "connecting":
                self.pill_thermal.set_state("OPENING", theme.WARN)
            elif status == "stopped":
                self.btn_thermal.setText("OPEN THERMAL")

    def _on_rgb_stats(self, stats):
        self._update_stats(stats.get("fps", 0.0), (stats.get("width", 0), stats.get("height", 0)))

    def _on_thermal_stats(self, stats):
        self.chip_thermal_fps.set_value(f"{stats.get('fps', 0.0):4.1f}")
        self.chip_thermal_res.set_value(
            f"{stats.get('width', 0)}x{stats.get('height', 0)}"
        )

    # -------------------------------------------------------------- capture ---
    def _capture_image(self):
        """Crops come from whichever payload the track is attributed to."""
        return self._rgb_bgr if self._rgb_bgr is not None else self._thermal_raw

    def _active_camera_id(self):
        if self.chk_rgb.isChecked():
            return self.spin_rgb_id.value()
        return self.spin_thermal_id.value()

    def build_findings(self, track_ids=None):
        findings = []
        rgb = self._rgb_bgr
        thermal = self._thermal_raw
        gray_thermal = imaging.normalize_thermal(thermal) if thermal is not None else None
        lo = self.spin_temp_lo.value()
        hi = max(self.spin_temp_hi.value(), lo + 1.0)

        for track in self.current_tracks():
            if track_ids and track.track_id not in track_ids:
                continue

            if track.source == "thermal" and gray_thermal is not None:
                crop = imaging.fit_to_crop(
                    imaging.thermal_to_bgr(gray_thermal, self.combo_colormap.currentText()),
                    track.box,
                )
                temp = imaging.estimate_temp_c(
                    imaging.fit_to_crop(gray_thermal, track.box, min_side=8), lo, hi
                )
                source = "thermal"
            else:
                base = rgb if rgb is not None else imaging.thermal_to_bgr(gray_thermal)
                if base is None:
                    continue
                crop = imaging.fit_to_crop(base, track.box)
                temp = (
                    imaging.estimate_temp_c(
                        imaging.fit_to_crop(gray_thermal, track.box, min_side=8), lo, hi
                    )
                    if gray_thermal is not None
                    else None
                )
                source = track.source

            if crop is None:
                continue
            findings.append(
                Finding(
                    track_id=track.track_id,
                    crop_bgr=crop,
                    score=track.score,
                    source=source,
                    temp_c=temp,
                    camera_id=self._active_camera_id(),
                )
            )
        return findings

    def _annotate_temps(self):
        """Attach an estimated temperature to each track from the thermal ROI."""
        if not self.chk_thermal.isChecked() or self._thermal_raw is None:
            for track in self.tracker.tracks:
                track.temp_c = None
            return
        gray = imaging.normalize_thermal(self._thermal_raw)
        if gray is None:
            return
        lo = self.spin_temp_lo.value()
        hi = max(self.spin_temp_hi.value(), lo + 1.0)
        for track in self.tracker.tracks:
            roi = imaging.fit_to_crop(gray, track.box, min_side=8)
            track.temp_c = imaging.estimate_temp_c(roi, lo, hi)

    def _recompose(self):
        """Redraw the last known state after a control change with no new frame."""
        if self._rgb_bgr is None and self._thermal_raw is None:
            if not self.chk_rgb.isChecked():
                self.canvas.clear("THERMAL ONLY", "Open a thermal payload to continue", theme.THERMAL_C)
            elif not self.chk_thermal.isChecked():
                self.canvas.clear("RGB ONLY", "Thermal payload is off")
            else:
                self.canvas.clear("NO SIGNAL", "Open a payload to begin")
            return
        self._pipeline_cycle(self._rgb_bgr)