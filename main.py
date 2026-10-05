"""SkySight Console — entry point.

Human search & rescue console for UAV platforms using visible (RGB) and
long-wave infrared (thermal) payloads.

Operator flow
-------------
1. ``RGB DETECTION``   — visible-spectrum only, maximum identification detail.
2. ``RGB + THERMAL``   — fused composite; RGB can be switched off entirely for
   night operations, and thermal-only review keeps working.
3. Confirmed people are tracked, then frozen with CAPTURE FINDINGS into a
   review window where each person can be selected or deselected before being
   filed to the database with a feature vector.
4. The database screens new captures against existing records so the same
   person is not filed twice.

Model weights plug in through the Detector selector. The custom Ultralytics
model is wired but stays inert until torch + ultralytics are installed.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.database import PersonDatabase
from ui import theme
from ui.camera_tab import CameraTab
from ui.database_dialog import DatabaseDialog
from ui.fusion_tab import FusionTab
from ui.review_dialog import ReviewDialog
from ui.widgets import Banner, Pill, StatusDot

APP_ROOT = "data"


class MainWindow(QMainWindow):
    def __init__(self, database):
        super().__init__()
        self.db = database
        self.setWindowTitle(f"{theme.APP_NAME} — {theme.APP_SUBTITLE}")
        self.resize(1480, 940)
        self.setMinimumSize(1120, 720)

        self.camera_tab = CameraTab(self.db)
        self.fusion_tab = FusionTab(self.db)

        for tab in (self.camera_tab, self.fusion_tab):
            tab.captureRequested.connect(self.open_review)
            tab.databaseRequested.connect(self.open_database)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._wrap(self.camera_tab), "RGB Camera")
        self.tabs.addTab(self._wrap(self.fusion_tab), "RGB + Thermal")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 0)
        layout.setSpacing(9)
        layout.addWidget(self._build_header())
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        self._build_status_bar()
        self._refresh_global_status()

    # -------------------------------------------------------------- chrome ---
    def _wrap(self, tab):
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.addWidget(tab)
        return host

    def _build_header(self):
        header = QWidget()
        lay = QHBoxLayout(header)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(12)

        dot = StatusDot(theme.OK, 10)
        self.header_dot = dot
        lay.addWidget(dot, 0, Qt.AlignVCenter)

        titles = QWidget()
        title_lay = QHBoxLayout(titles)
        title_lay.setContentsMargins(0, 0, 0, 0)
        title_lay.setSpacing(10)
        name = QLabel(theme.APP_NAME)
        name.setProperty("role", "title")
        title_lay.addWidget(name)
        subtitle = QLabel(theme.APP_SUBTITLE)
        subtitle.setProperty("role", "subtitle")
        title_lay.addWidget(subtitle)
        lay.addWidget(titles, 0, Qt.AlignVCenter)
        lay.addStretch(1)

        self.pill_mode = Pill("READY", theme.OK)
        lay.addWidget(self.pill_mode, 0, Qt.AlignVCenter)

        self.pill_db = Pill("DB 0", theme.FUSION_C)
        self.pill_db.setToolTip("People currently filed in the person database.")
        lay.addWidget(self.pill_db, 0, Qt.AlignVCenter)

        self.banner = Banner("")
        self.banner.setMaximumWidth(430)
        lay.addWidget(self.banner)
        return header

    def _build_status_bar(self):
        bar = self.statusBar()

        self.status_feed = QLabel("FEED: IDLE")
        self.status_mode = QLabel("VIEW: RGB")
        self.status_tracks = QLabel("TRACKS: 0")
        self.status_db = QLabel("DB: 0 PEOPLE / 0 CAPTURES")
        self.status_alerts = QLabel("ALERTS: 0")

        for widget in (
            self.status_feed,
            self.status_mode,
            self.status_tracks,
            self.status_db,
            self.status_alerts,
        ):
            bar.addWidget(widget)
        bar.addPermanentWidget(QLabel("v0.1  |  local-only, no uplink"))
        self._refresh_global_status()

    # -------------------------------------------------------------- refresh ---
    def current_tab(self):
        return self.fusion_tab if self.tabs.currentIndex() == 1 else self.camera_tab

    def _on_tab_changed(self, _index):
        mode = "RGB + Thermal" if self.tabs.currentIndex() == 1 else "RGB"
        self.status_mode.setText(f"VIEW: {mode}")
        self.current_tab()._recompose()
        self._refresh_global_status()

    def _refresh_global_status(self):
        self.pill_db.set_state(
            f"DB {self.db.count()}",
            theme.FUSION_C if self.db.count() else theme.TEXT_MUTED,
        )
        self.status_db.setText(
            f"DB: {self.db.count()} PEOPLE / {self.db.capture_count()} CAPTURES"
        )

        tab = self.current_tab()
        self.pill_mode.set_state("ACTIVE", theme.OK)
        self._refresh_model_banner(tab)

    def _refresh_model_banner(self, tab):
        """Report the live state of the selected backend instead of a static notice."""
        detector = tab.pipeline.detector
        if detector is None or not getattr(detector, "requires_model", False):
            self.banner.hide_message()
            return
        if not detector.available():
            self.banner.show_message(
                "Ultralytics backend selected but torch + ultralytics are not "
                "installed. Falling back to the bundled detector.",
                "warn",
            )
            return
        loaded = getattr(detector, "loaded_weights", None)
        if loaded:
            where = "GPU" if detector.device == 0 else "CPU"
            self.banner.show_message(
                f"{loaded} loaded - running on {where}."
            )
        else:
            self.banner.show_message(
                "Ultralytics ready - weights load on the first frame."
            )

    def _tick(self):
        tab = self.current_tab()
        confirmed = len(tab.current_tracks())
        self.status_tracks.setText(f"TRACKS: {confirmed} CONFIRMED")

        running = tab.rgb.is_running
        thermal = getattr(tab, "thermal", None)
        if thermal is not None and thermal.is_running:
            running = True
        self.status_feed.setText("FEED: LIVE" if running else "FEED: IDLE")
        self.header_dot.set_color(theme.OK if running else theme.TEXT_MUTED)
        self._refresh_global_status()

    # -------------------------------------------------------------- dialogs ---
    def open_review(self, findings):
        if not findings:
            return
        dialog = ReviewDialog(findings, self.db, self)
        dialog.exec()
        self._refresh_global_status()
        self.current_tab().refresh_db_chip()

    def open_database(self):
        dialog = DatabaseDialog(self.db, self)
        dialog.exec()
        self._refresh_global_status()
        for tab in (self.camera_tab, self.fusion_tab):
            tab.refresh_db_chip()

    # -------------------------------------------------------------- shutdown ---
    def closeEvent(self, event):
        self.camera_tab.shutdown()
        self.fusion_tab.shutdown()
        event.accept()


def build_app(argv=None):
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(theme.APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(theme.stylesheet())
    return app


def main():
    app = build_app()
    database = PersonDatabase(APP_ROOT)
    window = MainWindow(database)

    from PySide6.QtCore import QTimer

    timer = QTimer(window)
    timer.timeout.connect(window._tick)
    timer.start(500)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()