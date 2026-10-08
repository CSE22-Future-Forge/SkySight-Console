"""Coordinator console (spec 4.3): manifest | map | detail/teams, plus the top bar.

The GUI calls the service layer directly. It never polls for changes: the event
bus (any thread) emits a Qt signal, which Qt queues onto the GUI thread, and a
short single-shot timer coalesces bursts into one refresh of whatever changed.
"""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ground_station import VERSION
from ground_station.api.server import server_url
from ground_station.core import events as ev
from ground_station.core.services import ServiceError
from ground_station.ui import theme
from ground_station.ui.detail_panel import DetailPanel
from ground_station.ui.manifest_panel import ManifestPanel
from ground_station.ui.map_view import MapView
from ground_station.ui.pairing_dialog import PairingDialog
from ground_station.ui.settings_dialog import SettingsDialog
from ground_station.ui.teams_panel import TeamsPanel

REFRESH_COALESCE_MS = 120
SLOW_TICK_MS = 5000
BATTERY_WARN_PCT = 20

VICTIM_EVENTS = {ev.VICTIM_CREATED, ev.VICTIM_UPDATED, ev.VICTIM_HIDDEN, ev.ASSIGNMENT_CHANGED}
TEAM_EVENTS = {ev.TEAM_LOCATION, ev.TEAM_CHANGED, ev.DEVICE_ONLINE, ev.DEVICE_OFFLINE, ev.DEVICE_REVOKED}


class BusSignals(QObject):
    """Bridges the event bus (any thread) onto the GUI thread."""

    arrived = Signal(object)

    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self._bus = bus
        bus.subscribe(self._on_events)

    def _on_events(self, events):
        self.arrived.emit(events)  # queued: this object lives on the GUI thread

    def detach(self):
        self._bus.unsubscribe(self._on_events)


def laptop_battery() -> tuple[int | None, bool]:
    """(percent, on_battery) from Windows; (None, False) when unknown or on a desktop."""
    if sys.platform != "win32":
        return None, False

    class _Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    st = _Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return None, False
    pct = st.BatteryLifePercent & 0xFF
    if pct == 255 or st.BatteryFlag & 0x80:  # unknown / no battery
        return None, False
    return pct, st.ACLineStatus == 0


class MissionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start mission")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Kalutara flood - day 2")
        self.area = QLineEdit()
        self.mode = QComboBox()
        self.mode.addItem("Auto - alert teams immediately", "auto")
        self.mode.addItem("Verify - coordinator confirms each detection first", "verify")
        form.addRow("Name", self.name)
        form.addRow("Area", self.area)
        form.addRow("Alerts", self.mode)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self):
        if self.name.text().strip():
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self, manifest, server, parent=None):
        super().__init__(parent)
        self.m = manifest
        self.server = server
        self.setWindowTitle(f"{theme.APP_NAME} - ground station")
        self.resize(1560, 940)
        self.setMinimumSize(1180, 700)
        self.selected_id: str | None = None
        self._dirty: set[str] = set()
        self._near_rescuer = 0

        self.map = MapView(f"http://127.0.0.1:{server.port}")
        self.manifest_panel = ManifestPanel()
        self.detail = DetailPanel(manifest)
        self.teams_panel = TeamsPanel(manifest, hub_ids=lambda: server.hub.connected_device_ids())

        self.side_tabs = QTabWidget()
        self.side_tabs.addTab(self.detail, "Victim")
        self.side_tabs.addTab(self.teams_panel, "Teams")

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self.manifest_panel)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.map)
        split.addWidget(self.side_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 800, 390])
        split.setChildrenCollapsible(False)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 10, 12, 6)
        lay.setSpacing(10)
        lay.addWidget(self._build_top_bar())
        lay.addWidget(split, 1)
        self.setCentralWidget(root)
        self.statusBar().addPermanentWidget(QLabel(f"v{VERSION}  |  offline LAN only"))

        # wiring
        self.manifest_panel.victimSelected.connect(lambda vid: self.select_victim(vid, fly=True))
        self.manifest_panel.filtersChanged.connect(lambda: self._mark("victims"))
        self.map.victimClicked.connect(lambda vid: self.select_victim(vid, fly=False))
        self.map.addVictimRequested.connect(self._add_manual_victim)
        self.detail.changed.connect(lambda: self._mark("victims", "teams"))
        self.detail.zoomRequested.connect(lambda lat, lon: self.map.fly_to(lat, lon, 17))
        self.teams_panel.changed.connect(lambda: self._mark("teams", "victims"))
        self.teams_panel.pairRequested.connect(self.open_pairing)

        self.bus_signals = BusSignals(manifest.bus, self)
        self.bus_signals.arrived.connect(self._on_events)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)
        self._slow = QTimer(self)
        self._slow.timeout.connect(self._slow_tick)
        self._slow.start(SLOW_TICK_MS)

        self._mark("victims", "teams", "uav", "mission")
        self._first_fit = True

    # ---------------------------------------------------------------- top bar ---
    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        title = QLabel("SAR MANIFEST")
        title.setProperty("role", "title")
        lay.addWidget(title)

        self.mission_label = QLabel()
        self.mission_label.setProperty("role", "dim")
        lay.addWidget(self.mission_label)
        self.btn_mission = QPushButton()
        self.btn_mission.clicked.connect(self._toggle_mission)
        lay.addWidget(self.btn_mission)

        self.mode_group = QButtonGroup(self)
        self.btn_auto = QPushButton("Auto alerts")
        self.btn_verify = QPushButton("Verify first")
        self.btn_auto.setToolTip("New detections alert every team immediately")
        self.btn_verify.setToolTip("New detections wait (purple) until you verify them")
        for b, mode in ((self.btn_auto, "auto"), (self.btn_verify, "verify")):
            b.setCheckable(True)
            self.mode_group.addButton(b)
            b.clicked.connect(lambda _c=False, md=mode: self._set_alert_mode(md))
            lay.addWidget(b)

        lay.addStretch(1)
        self.battery = QLabel()
        lay.addWidget(self.battery)
        self.server_label = QLabel()
        self.server_label.setProperty("role", "mono")
        self.server_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.server_label)
        self.phones = QLabel()
        lay.addWidget(self.phones)
        pair = QPushButton("Pair phone")
        pair.setProperty("variant", "primary")
        pair.clicked.connect(lambda: self.open_pairing(""))
        lay.addWidget(pair)
        settings = QPushButton("Settings")
        settings.clicked.connect(self._open_settings)
        lay.addWidget(settings)
        return bar

    # ---------------------------------------------------------------- refresh ---
    def _mark(self, *parts) -> None:
        self._dirty.update(parts)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(REFRESH_COALESCE_MS)

    def _on_events(self, events) -> None:
        for e in events:
            if e.type in VICTIM_EVENTS:
                self._mark("victims")
                if e.type == ev.VICTIM_CREATED and e.scope_team_id is None:
                    self._announce(e)
            elif e.type in TEAM_EVENTS:
                self._mark("teams")
            elif e.type == ev.UAV_POSE:
                self._mark("uav")
            elif e.type == ev.MISSION_CHANGED:
                self._mark("mission", "victims")
            elif e.type == ev.ALERT_RECEIPT and e.entity_id == self.selected_id:
                self._mark("detail")
            elif e.type == ev.DETECTION_NEAR_RESCUER:
                self._near_rescuer += 1
                team = self.m.teams.get(e.data.get("team_id"))
                self.statusBar().showMessage(
                    f"Detection {e.data.get('distance_m')} m from team {team['name'] if team else '?'} "
                    f"treated as rescuer - no alert ({self._near_rescuer} so far)", 8000)

    def _announce(self, e) -> None:
        code = e.data.get("display_code", "?")
        if e.laptop_only:
            self.statusBar().showMessage(f"New detection {code} awaiting verification", 15000)
        else:
            self.statusBar().showMessage(f"NEW VICTIM {code} - teams alerted", 15000)
        QApplication.beep()
        QApplication.alert(self, 3000)

    def _refresh(self) -> None:
        parts, self._dirty = self._dirty, set()
        if "mission" in parts:
            self._refresh_mission()
        teams = self.m.teams.list()
        teams_by_id = {t["id"]: t for t in teams}
        if "victims" in parts:
            mission = self.m.missions.active()
            victims = self.m.victims.list(mission["id"]) if mission else []
            self.manifest_panel.set_victims(victims, teams_by_id)
            hidden = self.manifest_panel.hidden_statuses()
            shown = [v for v in victims if v["status"] not in hidden]
            self.map.set_victims(shown, teams_by_id)
            if self._first_fit and shown:
                self._first_fit = False
                self.map.fit(shown)
            parts.add("detail")
        if "teams" in parts:
            trails = {t["id"]: [(p["lat"], p["lon"]) for p in self.m.teams.trail(t["id"])] for t in teams}
            self.map.set_teams([t for t in teams if t["is_active"]], trails, self.m.teams.is_stale)
            self.teams_panel.refresh()
            self._refresh_phones()
            parts.add("detail")
        if "uav" in parts:
            self.map.set_uav(self.m.telemetry.latest)
            self.detail.set_uav_pose(self.m.telemetry.latest)
        if "detail" in parts:
            self.detail.refresh()

    def _refresh_mission(self) -> None:
        mission = self.m.missions.active()
        if mission:
            self.mission_label.setText(f"Mission: <b>{mission['name']}</b>"
                                       + (f" · {mission['area_name']}" if mission["area_name"] else ""))
            self.btn_mission.setText("End mission")
            self.btn_mission.setProperty("variant", "danger")
            (self.btn_verify if mission["alert_mode"] == "verify" else self.btn_auto).setChecked(True)
        else:
            self.mission_label.setText("<span style='color:#fb8c00'>No mission running - detections are refused</span>")
            self.btn_mission.setText("Start mission")
            self.btn_mission.setProperty("variant", "primary")
        self.btn_auto.setEnabled(mission is not None)
        self.btn_verify.setEnabled(mission is not None)
        self.btn_mission.style().unpolish(self.btn_mission)
        self.btn_mission.style().polish(self.btn_mission)
        self._refresh_server()

    def _refresh_server(self) -> None:
        if self.server.error or not self.server.is_alive():
            self.server_label.setText("SERVER DOWN")
            self.server_label.setStyleSheet(f"color: {theme.DANGER}; font-weight: 700;")
        else:
            self.server_label.setText(server_url(self.m))
            self.server_label.setStyleSheet("")

    def _refresh_phones(self) -> None:
        live = len(self.server.hub.connected_device_ids())
        total = sum(1 for d in self.m.devices.list() if not d["revoked"])
        color = theme.OK if live else theme.TEXT_MUTED
        self.phones.setText(f"<span style='color:{color}'>●</span> {live}/{total} phones live")

    def _slow_tick(self) -> None:
        """Ages, staleness and battery move with time, not with events."""
        self.manifest_panel.refresh_ages()
        self._mark("teams")
        self._refresh_server()
        pct, on_battery = laptop_battery()
        if pct is None:
            self.battery.setText("")
        elif pct <= BATTERY_WARN_PCT and on_battery:
            self.battery.setText(f"⚠ LAPTOP BATTERY {pct}%")
            self.battery.setStyleSheet(f"color: white; background: {theme.DANGER}; border-radius: 6px; "
                                       "padding: 3px 8px; font-weight: 700;")
        else:
            self.battery.setText(f"Battery {pct}%{'' if on_battery else ' ⚡'}")
            self.battery.setStyleSheet(f"color: {theme.TEXT_DIM};")

    # -------------------------------------------------------------- selection ---
    def select_victim(self, victim_id: str, fly: bool) -> None:
        self.selected_id = victim_id
        self.manifest_panel.select(victim_id)
        self.map.select(victim_id)
        self.detail.show_victim(victim_id)
        self.side_tabs.setCurrentWidget(self.detail)
        if fly:
            v = self.m.victims.get(victim_id)
            if v:
                self.map.fly_to(v["lat"], v["lon"], 16)

    # --------------------------------------------------------------- actions ---
    def _toggle_mission(self) -> None:
        mission = self.m.missions.active()
        if mission:
            if QMessageBox.question(self, "End mission",
                                    f"End '{mission['name']}'? New detections will be refused until a mission "
                                    "is started.") == QMessageBox.Yes:
                self.m.missions.stop()
                self._offer_archive(mission)
        else:
            dlg = MissionDialog(self)
            if dlg.exec() == QDialog.Accepted:
                self.m.missions.start(dlg.name.text().strip(), dlg.area.text().strip() or None,
                                      dlg.mode.currentData())
                self._first_fit = True
        self._mark("mission", "victims")

    def _offer_archive(self, mission) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Archive mission")
        box.setText(f"Archive '{mission['name']}' to a zip file now?")
        box.setInformativeText("The zip holds every victim, detection, status change and image. Deleting the "
                               "images afterwards keeps photos of people off this laptop.")
        keep = box.addButton("Archive", QMessageBox.AcceptRole)
        purge = box.addButton("Archive + delete images", QMessageBox.DestructiveRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() not in (keep, purge):
            return
        try:
            path = self.m.missions.archive(mission["id"], delete_images=box.clickedButton() is purge)
        except (ServiceError, OSError) as exc:
            QMessageBox.warning(self, "Archive", str(exc))
            return
        self.statusBar().showMessage(f"Mission archived to {path}", 20000)

    def _set_alert_mode(self, mode: str) -> None:
        try:
            self.m.missions.set_alert_mode(mode)
        except ServiceError as exc:
            QMessageBox.warning(self, "Alert mode", exc.message)
        self._mark("mission")

    def _add_manual_victim(self, lat: float, lon: float) -> None:
        note, ok = QInputDialog.getText(self, "Add victim here",
                                        f"Victim at {lat:.6f}, {lon:.6f}\nNote (who reported it, what was seen):")
        if not ok:
            return
        try:
            v = self.m.victims.add_manual(lat, lon, note=note.strip() or None, position_error_m=10.0)
        except ServiceError as exc:
            QMessageBox.warning(self, "Add victim", exc.message)
            return
        self._mark("victims")
        self.select_victim(v["id"], fly=False)

    def open_pairing(self, team_id: str = "") -> None:
        if not self.m.teams.list(active_only=True):
            QMessageBox.information(self, "Pair phone", "Create a team first (Teams tab, + Team).")
            self.side_tabs.setCurrentWidget(self.teams_panel)
            return
        PairingDialog(self.m, server_url(self.m), team_id or None, self).exec()
        self._mark("teams")

    def _open_settings(self) -> None:
        if SettingsDialog(self.m, self).exec() == QDialog.Accepted:
            self._mark("victims", "teams", "mission")

    def closeEvent(self, event) -> None:
        if QMessageBox.question(self, "Close ground station",
                                "Close the ground station? Phones keep working offline and resync when it is "
                                "back.") != QMessageBox.Yes:
            event.ignore()
            return
        event.accept()


def build_app(argv=None) -> QApplication:
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)  # QtWebEngine requirement
    import PySide6.QtWebEngineWidgets  # noqa: F401  (must load before QApplication exists)

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(theme.APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(theme.stylesheet())
    return app


def dispose(window: "MainWindow") -> None:
    """Destroy the window (and its web page) while QApplication is still alive.

    Left to interpreter shutdown, QtWebEngine tears down after the application
    object and crashes the process on exit.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    window.bus_signals.detach()
    window.map.dispose()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()


def run_gui(manifest, server) -> int:
    app = build_app()
    window = MainWindow(manifest, server)
    window.show()
    code = app.exec()
    dispose(window)
    return code
