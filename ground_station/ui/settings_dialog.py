"""Settings dialog: the tunables in the settings table (dedup radius, staleness, ...)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout

from ground_station.config import DEFAULTS
from ground_station.ui.map_tools import MapButtons, describe_map

HELP = {
    "server_port": "HTTP port for phones (restart needed; firewall rule must match)",
    "server_host_ip": "Address put in the pairing QR (blank = auto, prefers 192.168.50.x)",
    "dedup_radius_min_m": "Smallest merge radius for repeated sightings (m)",
    "dedup_error_multiplier": "Merge radius = this x combined geolocation error",
    "dedup_window_min": "Only merge into victims seen within this many minutes",
    "rescuer_radius_m": "Ignore detections this close to a team's last fix (m)",
    "team_stale_after_s": "Team fix older than this is shown grey and not used by the rescuer filter",
    "ws_ping_interval_s": "WebSocket keepalive interval (s)",
    "ws_dead_after_s": "Drop a phone connection silent for this long (s)",
    "pairing_token_ttl_min": "Pairing QR lifetime (min)",
    "alert_repeat_s": "Phone siren repeats until the alert is opened (s)",
    "gps_max_accuracy_m": "Team fixes worse than this are left out of trails (m)",
    "map_file": "Offline map, relative to the data folder",
    "telemetry_source": "UAV telemetry: mavlink:udpin:0.0.0.0:14550, nmea:udp:10110, ... (restart needed)",
}
RESTART = {"server_port", "telemetry_source"}


class SettingsDialog(QDialog):
    mapInstalled = Signal()

    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.m = manifest
        self.setWindowTitle("Ground station settings")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for key in DEFAULTS:
            edit = QLineEdit(str(manifest.settings.get(key)))
            edit.setToolTip(HELP.get(key, ""))
            edit.setPlaceholderText(HELP.get(key, ""))
            self.edits[key] = edit
            form.addRow(key, edit)
        lay.addLayout(form)
        self.map_label = QLabel(describe_map(manifest))
        self.map_label.setWordWrap(True)
        map_buttons = MapButtons(manifest, self)
        map_buttons.installed.connect(self._map_installed)
        lay.addWidget(QLabel("<b>Offline map</b>"))
        lay.addWidget(self.map_label)
        lay.addWidget(map_buttons)
        note = QLabel(f"Data folder: {manifest.paths.root}")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _map_installed(self):
        self.map_label.setText(describe_map(self.m))
        self.mapInstalled.emit()

    def _save(self):
        changed, restart = [], False
        for key, edit in self.edits.items():
            text = edit.text().strip()
            default = DEFAULTS[key]
            try:
                if isinstance(default, int) and not isinstance(default, bool):
                    value = int(text)
                elif isinstance(default, float):
                    value = float(text)
                else:
                    value = text
            except ValueError:
                QMessageBox.warning(self, "Settings", f"{key} must be a number.")
                edit.setFocus()
                return
            if isinstance(value, (int, float)) and value < 0:
                QMessageBox.warning(self, "Settings", f"{key} cannot be negative.")
                return
            if value != self.m.settings.get(key):
                changed.append((key, value))
                restart |= key in RESTART
        for key, value in changed:
            self.m.settings.set(key, value)
        if restart:
            QMessageBox.information(self, "Settings", "Restart the ground station for port/telemetry changes.")
        self.accept()
