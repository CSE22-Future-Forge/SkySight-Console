"""Offline map actions: Import a ready .pmtiles file, or Download & build one (needs internet once).

Both run off the GUI thread (ground_station.map_files has no Qt) and report back through
signals, which Qt queues onto the GUI thread.
"""

from __future__ import annotations

import html
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from ground_station import map_files
from ground_station.ui import theme

LOG_MAX_LINES = 5000
MB = 1024 * 1024


def map_status(manifest) -> tuple[Path, bool]:
    path = manifest.paths.map_file(manifest.settings)
    return path, path.is_file()


def describe_map(manifest) -> str:
    path, ok = map_status(manifest)
    if not ok:
        return "No offline map installed."
    return f"Installed: {path} ({path.stat().st_size / MB:.0f} MB)"


class _Worker(QObject):
    """Runs one job on a plain thread; every report crosses to the GUI thread as a queued signal."""

    log = Signal(str)
    stage = Signal(str)
    progress = Signal(object, object)  # done, total (may exceed int32)
    finished = Signal(object, str)     # result path or None, error message

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self._job = job
        self._cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="map-job", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        try:
            path = self._job(self.log.emit, self.stage.emit, self.progress.emit, self._cancel.is_set)
            self.finished.emit(path, "")
        except map_files.MapError as exc:
            self.finished.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 - shown to the coordinator, never crash the GUI
            self.finished.emit(None, f"{type(exc).__name__}: {exc}")


def _set_progress(bar: QProgressBar, done, total) -> None:
    if not total:
        bar.setRange(0, 0)  # busy
        return
    bar.setRange(0, 1000)
    bar.setValue(int(1000 * done / total))
    bar.setFormat(f"{done / MB:,.0f} / {total / MB:,.0f} MB")


class MapJobDialog(QDialog):
    """Progress + log for a map job. ``installed`` fires once a new map is in place."""

    installed = Signal(str)

    def __init__(self, title: str, intro: str, start_label: str, job, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 420)
        self._job = job
        self._worker: _Worker | None = None
        lay = QVBoxLayout(self)
        text = QLabel(intro)
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        lay.addWidget(text)
        self.stage = QLabel()
        self.stage.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_MAX_LINES)
        self.log.setProperty("role", "mono")
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_start = QPushButton(start_label)
        self.btn_start.setProperty("variant", "primary")
        self.btn_start.clicked.connect(self.start)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self._close_or_cancel)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_close)
        lay.addLayout(row)

    def start(self) -> None:
        self.btn_start.setEnabled(False)
        self.btn_close.setText("Cancel")
        self.bar.show()
        self.bar.setRange(0, 0)
        self._worker = _Worker(self._job, self)
        self._worker.log.connect(self.log.appendPlainText)
        self._worker.stage.connect(self._on_stage)
        self._worker.progress.connect(lambda d, t: _set_progress(self.bar, d, t))
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_stage(self, text: str) -> None:
        self.stage.setText(text)
        self.log.appendPlainText(f"== {text}")

    def _on_finished(self, path, error: str) -> None:
        self._worker = None
        self.btn_close.setText("Close")
        self.bar.hide()
        if error:
            self.stage.setText(f"<span style='color:{theme.DANGER}'>Stopped: {html.escape(error)}</span>")
            self.log.appendPlainText(f"[error] {error}")
            self.btn_start.setText("Try again (resumes)")
            self.btn_start.setEnabled(True)
            return
        self.stage.setText(f"<span style='color:{theme.OK}'>Map installed: {html.escape(str(path))}</span>")
        self.installed.emit(str(path))

    def _close_or_cancel(self) -> None:
        if self._worker is None:
            self.accept()
            return
        self.stage.setText("Cancelling...")
        self._worker.cancel()

    def reject(self) -> None:  # Esc / window close while running = cancel, not hide
        if self._worker is not None:
            self._close_or_cancel()
            return
        super().reject()


def open_build_dialog(manifest, parent=None) -> bool:
    """Download & build the map. Returns True if a new map was installed."""
    dest = map_files.install_target(manifest.paths, manifest.settings)
    intro = ("<b>One-time setup that needs internet.</b> This downloads about 1.7 GB of OpenStreetMap and "
             "coastline data, then builds the Sri Lanka map with Planetiler (Java 21 or newer, about 4 GB of free "
             "disk, roughly 3 minutes after the downloads). Downloads resume if the connection drops.<br><br>"
             f"The map is saved to <code>{html.escape(str(dest))}</code>. Once it is built the ground station needs no internet.")

    def job(log, stage, progress, cancelled):
        return map_files.build_map(manifest.paths, dest, log, progress, cancelled, stage)

    dlg = MapJobDialog("Download offline map", intro, "Start download", job, parent)
    done = []
    dlg.installed.connect(done.append)
    dlg.exec()
    return bool(done)


def import_map(manifest, parent=None) -> bool:
    """Pick a .pmtiles file and install it. Returns True if a new map was installed."""
    name, _ = QFileDialog.getOpenFileName(parent, "Import offline map", str(Path.home()),
                                          "PMTiles map (*.pmtiles);;All files (*)")
    if not name:
        return False
    src = Path(name)
    try:
        map_files.check_pmtiles(src)
    except map_files.MapError as exc:
        QMessageBox.warning(parent, "Import offline map", str(exc))
        return False
    dest = map_files.install_target(manifest.paths, manifest.settings)
    if dest.is_file() and src.resolve() != dest.resolve():
        if QMessageBox.question(parent, "Import offline map",
                                f"Replace the installed map with {src.name}?\nPaired phones download the new "
                                "map the next time they pair.") != QMessageBox.Yes:
            return False

    def job(log, stage, progress, cancelled):
        stage(f"Copying {src.name}")
        return map_files.install_map(src, dest, progress, cancelled)

    dlg = MapJobDialog("Import offline map", f"Copying <code>{html.escape(str(src))}</code><br>to <code>{html.escape(str(dest))}</code>", "Import",
                       job, parent)
    done = []
    dlg.installed.connect(done.append)
    dlg.installed.connect(lambda _p: dlg.accept())
    dlg.log.hide()
    dlg.setMinimumSize(520, 0)
    dlg.btn_start.hide()
    dlg.start()
    dlg.exec()
    return bool(done)


class MapButtons(QWidget):
    """Import + Download buttons; ``installed`` fires after either one puts a new map in place."""

    installed = Signal()

    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.m = manifest
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn_import = QPushButton("Import map file...")
        self.btn_import.setToolTip("Install a srilanka.pmtiles built on another machine (USB stick, shared folder)")
        self.btn_import.clicked.connect(self._import)
        self.btn_download = QPushButton("Download map...")
        self.btn_download.setToolTip("Download OpenStreetMap data and build the map here (needs internet once)")
        self.btn_download.clicked.connect(self._download)
        lay.addWidget(self.btn_import)
        lay.addWidget(self.btn_download)

    def _import(self) -> None:
        if import_map(self.m, self.window()):
            self.installed.emit()

    def _download(self) -> None:
        if open_build_dialog(self.m, self.window()):
            self.installed.emit()


class MapMissingStrip(QFrame):
    """Shown above the map while no offline map is installed."""

    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.setObjectName("mapMissing")
        self.setStyleSheet(f"#mapMissing {{ border: 1px solid {theme.WARN}; border-radius: 6px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        text = QLabel("<b>No offline map installed.</b> Markers still work, but there is no background map.")
        text.setWordWrap(True)
        lay.addWidget(text, 1)
        self.buttons = MapButtons(manifest, self)
        lay.addWidget(self.buttons)
