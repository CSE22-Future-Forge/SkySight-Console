"""Tab 1 — visible-spectrum detection only.

Single RGB payload, no fusion. This is the daylight / high-detail view where
an operator wants maximum colour fidelity for identification.
"""

from __future__ import annotations

from pathlib import Path

import cv2
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QPushButton,
    QSpinBox,
)

from core import detect, imaging
from core.sources import StreamManager

from . import theme
from .stream_tab import StreamTab
from .widgets import Pill, labeled_row


class CameraTab(StreamTab):
    #: Pick either model at runtime to compare results side by side. The custom
    #: QS-YOLOv8s runs SAHI-style sliding-window inference (shorter side scaled
    #: to 768, two 768 px windows); YOLO26 runs single-pass.
    DETECTOR_CHOICES = (
        (
            (
                "Custom QS-YOLOv8s (SAHI)",
                "ultralytics",
                {
                    "weights": detect.CUSTOM_PERSON_WEIGHTS,
                    "sliced": True,
                    "imgsz": 768,
                },
            ),
            ("YOLO26", "ultralytics", {}),
        )
        if detect.CUSTOM_PERSON_WEIGHTS.is_file()
        else (("YOLO26", "ultralytics", {}),)
    )

    def __init__(self, database, parent=None):
        self._last_rgb_bgr = None
        self._pending_bgr = None
        self._source_label = "RGB"
        self.video_path = None
        super().__init__(database, parent)

        # Created after StreamTab.__init__ so the QObject base of this widget is
        # already initialised before it is used as a parent.
        self.rgb = StreamManager("rgb", self)
        self.rgb.frameReady.connect(self._on_frame)
        self.rgb.failed.connect(self._on_failed)
        self.rgb.statusChanged.connect(self._on_status)
        self.rgb.statsChanged.connect(self._on_stats)

        self._on_detector_changed(0)
        self.refresh_db_chip()
        self._update_capture_button()

    # ------------------------------------------------------------- controls ---
    def _populate_sensor_card(self, card):
        self.chk_rgb = QCheckBox("Enable RGB payload")
        self.chk_rgb.setChecked(True)
        self.chk_rgb.toggled.connect(self._on_rgb_toggle)

        self.spin_rgb_id = QSpinBox()
        self.spin_rgb_id.setRange(0, 20)
        self.spin_rgb_id.setValue(0)
        self.spin_rgb_id.setToolTip(
            "OpenCV capture index. Probe with the bundled scanner or set it manually."
        )

        card.add(labeled_row("Camera", self.spin_rgb_id))
        card.add(self.chk_rgb)

        self.btn_scan = QPushButton("SCAN DEVICES")
        self.btn_scan.setProperty("variant", "ghost")
        self.btn_scan.clicked.connect(self.scan_devices)
        self.btn_scan.setToolTip("Probe capture indices 0-9 and report which ones open.")
        card.add(self.btn_scan)

        self.btn_browse = QPushButton("BROWSE VIDEO")
        self.btn_browse.setProperty("variant", "primary")
        self.btn_browse.setToolTip(
            "Pick a video file. It opens immediately and loops at the end."
        )
        self.btn_browse.clicked.connect(self.browse_video)
        card.add(self.btn_browse)

        self.lbl_video = QLabel("No video selected")
        self.lbl_video.setWordWrap(True)
        self.lbl_video.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        card.add(self.lbl_video)

        self.btn_start = QPushButton("START FEED")
        self.btn_start.setProperty("variant", "ghost")
        self.btn_start.clicked.connect(self.start_feed)
        card.add(self.btn_start)

        self.btn_stop = QPushButton("STOP FEED")
        self.btn_stop.setProperty("variant", "live")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_feed)
        card.add(self.btn_stop)

        self.source_pill = Pill("IDLE", theme.TEXT_MUTED)
        card.add(labeled_row("Source", self.source_pill))

        self.scan_result = QLabel("")
        self.scan_result.setWordWrap(True)
        self.scan_result.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        card.add(self.scan_result)

        card.finish_header()

    # --------------------------------------------------------------- feeds ---
    def browse_video(self):
        """Let the operator pick an RGB video; open it immediately, on loop."""
        root = Path(__file__).resolve().parent.parent
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select RGB video",
            str(root),
            "Video files (*.mp4 *.avi *.mov *.mkv *.m4v *.wmv *.webm);;All files (*.*)",
        )
        if not path:
            return
        self.video_path = str(path)
        self.lbl_video.setText(f"Video: {Path(path).name}")
        self.banner.hide_message()
        if not self.chk_rgb.isChecked():
            self.chk_rgb.setChecked(True)
        # Restart cleanly so the chosen file takes over from any live feed.
        if self.rgb.is_running:
            self.rgb.close()
        self.start_feed()

    def start_feed(self):
        if not self.chk_rgb.isChecked():
            self.banner.show_message("Enable the RGB payload before starting a feed.", "warn")
            return
        self.banner.hide_message()
        if self.video_path and Path(self.video_path).is_file():
            self.rgb.open(video_path=self.video_path)
        else:
            # No file chosen: fall back to the bundled demo, then the camera.
            root = Path(__file__).resolve().parent.parent
            v = root / "rgb_demo.mp4"
            if v.exists():
                self.rgb.open(video_path=str(v))
            else:
                self.rgb.open(self.spin_rgb_id.value())
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def stop_feed(self):
        self.rgb.close()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_rgb_toggle(self, checked):
        if not checked:
            self.stop_feed()
            self._last_rgb_bgr = None
            self.canvas.clear("RGB DISABLED", "Turn the RGB payload back on to resume")
        elif self.btn_start.isEnabled() and not self.rgb.is_running:
            self.start_feed()

    def shutdown(self):
        self._render_timer.stop()
        self.rgb.close()
        self.pipeline.close()

    def _on_detector_changed(self, index):
        super()._on_detector_changed(index)
        if self.pipeline.detector is not None:
            self.pipeline.detector.source = "rgb"

    # ----------------------------------------------------------- frame cycle ---
    def _on_frame(self, frame):
        """Payload slot — buffer the frame; the render clock composes it.

        Deliberately trivial: this runs on the UI thread for every delivered
        frame, and the capture thread is released the moment it returns.
        """
        self._last_rgb_bgr = frame.bgr
        # Detection runs on the (possibly downscaled) frame; crops use the
        # full-resolution copy so stored thumbnails keep the original detail.
        self._capture_proc = frame.bgr
        self._capture_raw = frame.crop_bgr
        self._pending_bgr = frame.bgr
        if isinstance(frame.camera_id, int):
            self._source_label = f"RGB CAM {frame.camera_id:02d}"
        else:
            self._source_label = f"RGB {Path(str(frame.camera_id)).name}"
        self._mark_dirty()

    def _render_frame(self):
        frame_bgr = self._pending_bgr
        if frame_bgr is None:
            return
        # Tone-correct first: the detector is fed the corrected frame, so
        # brightness/contrast/exposure change what the model can find.
        frame_bgr = self._apply_tone(frame_bgr)
        tracks = self._consume_frame(frame_bgr)

        # Annotate in a scratch buffer: in-place drawing must never touch the
        # array the detector worker is still reading.
        view = self._render_buffer_for(frame_bgr)
        imaging.draw_tracks(
            view,
            tracks,
            show_ids=not self._hide_labels,
            show_score=not self._hide_labels,
            copy=False,
        )
        imaging.stamp(
            view,
            [
                self._source_label,
                imaging.time_label(),
            ],
            corner="tl",
            copy=False,
        )
        self.canvas.set_frame(imaging.to_qimage(view, copy=False))
        self._update_track_table(tracks)

    def _on_failed(self, message):
        self.canvas.clear("CAMERA ERROR", message[:64], theme.DANGER)
        self.banner.show_message(message, "danger")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.source_pill.set_state("ERROR", theme.DANGER)

    def _on_status(self, status):
        mapping = {
            "connecting": ("OPENING", theme.WARN),
            "running": ("LIVE", theme.OK),
            "stopped": ("IDLE", theme.TEXT_MUTED),
        }
        text, color = mapping.get(status, (status.upper(), theme.TEXT_DIM))
        self.source_pill.set_state(text, color)

    def _on_stats(self, stats):
        self._update_stats(stats.get("fps", 0.0), (stats.get("width", 0), stats.get("height", 0)))
        self._update_infer_chip()

    # -------------------------------------------------------------- capture ---
    def _capture_image(self):
        return self._capture_raw if self._capture_raw is not None else self._last_rgb_bgr

    def _recompose(self):
        """Redraw from the last buffered frame (used on tab switches)."""
        if self._last_rgb_bgr is None:
            if not self.chk_rgb.isChecked():
                self.canvas.clear("RGB DISABLED", "Turn the RGB payload back on to resume")
            elif not self.rgb.is_running:
                self.canvas.clear("NO SIGNAL", "Start the feed to begin")
            return
        # Re-render the buffered frame directly instead of routing it through
        # _on_frame: that would overwrite _capture_raw (the full-resolution crop
        # source) with the downscaled detection frame.
        self._pending_bgr = self._last_rgb_bgr
        self._dirty = False
        self._render_frame()

    def _active_camera_id(self):
        return self.spin_rgb_id.value()

    # ---------------------------------------------------------------- utils ---
    def scan_devices(self):
        found = []
        for index in range(10):
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                ok, frame = cap.read()
                shape = f"{frame.shape[1]}x{frame.shape[0]}" if ok and frame is not None else "no frame"
                found.append((index, shape))
            cap.release()

        if found:
            listing = ", ".join(f"{i} ({s})" for i, s in found)
            self.scan_result.setText(f"Open indices: {listing}")
            self.source_pill.set_state("PROBED", theme.OK)
        else:
            self.scan_result.setText("No open capture indices found in 0-9.")
            self.source_pill.set_state("NONE", theme.WARN)