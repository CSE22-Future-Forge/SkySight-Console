"""Victim detail (right panel, spec 4.3): images, position, receipts, history and the
coordinator actions allowed from the current status (driven by the state machine)."""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget, QButtonGroup, QDialogButtonBox, QFormLayout,
    QLineEdit,
)

from ground_station.core import geo
from ground_station.core import state_machine as sm
from ground_station.core.services import ServiceError
from ground_station.ui import theme

IMAGE_SIZES = (("crop", "Person"), ("frame", "Full frame"), ("thermal", "Thermal"))
ACTION_LABEL = {
    "verify": "Verify - alert teams",
    "dismiss": "Dismiss (false alarm)",
    "restore": "Restore",
    "release": "Release to open",
    "assign": "Assign to team",
    "reassign": "Reassign team",
    "mark_completed": "Mark rescued",
    "reopen": "Reopen",
}
ACTION_VARIANT = {"verify": "primary", "dismiss": "danger", "mark_completed": "ok"}


def clock(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S") if ms else "-"


class _CompleteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mark rescued")
        form = QFormLayout(self)
        self.people = QSpinBox()
        self.people.setRange(0, 500)
        self.people.setValue(1)
        self.note = QLineEdit()
        form.addRow("People rescued", self.people)
        form.addRow("Note", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class DetailPanel(QWidget):
    #: Emitted after a coordinator action changed something (main window refreshes).
    changed = Signal()
    zoomRequested = Signal(float, float)

    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.m = manifest
        self.victim_id: str | None = None
        self._image_size = "crop"
        self._uav_pose = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 8, 4)
        lay.setSpacing(10)

        self.empty = QLabel("Select a victim on the map or in the manifest.")
        self.empty.setProperty("role", "muted")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)

        self.content = QWidget()
        c = QVBoxLayout(self.content)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(10)
        lay.addWidget(self.content)
        lay.addStretch(1)

        head = QHBoxLayout()
        self.code = QLabel()
        self.code.setProperty("role", "code")
        head.addWidget(self.code)
        self.chip = QLabel()
        head.addWidget(self.chip, 0, Qt.AlignVCenter)
        head.addStretch(1)
        self.btn_zoom = QPushButton("Zoom")
        self.btn_zoom.clicked.connect(self._zoom)
        head.addWidget(self.btn_zoom)
        c.addLayout(head)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(200)
        self.image.setStyleSheet(f"background: #000; border: 1px solid {theme.BORDER}; border-radius: 6px;")
        self.image.setCursor(Qt.PointingHandCursor)
        self.image.mousePressEvent = lambda _e: self._open_full_image()
        c.addWidget(self.image)

        size_row = QHBoxLayout()
        self.size_group = QButtonGroup(self)
        self.size_buttons = {}
        for key, text in IMAGE_SIZES:
            b = QPushButton(text)
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self._set_image_size(k))
            self.size_group.addButton(b)
            self.size_buttons[key] = b
            size_row.addWidget(b)
        self.size_buttons["crop"].setChecked(True)
        c.addLayout(size_row)

        card = QFrame()
        card.setProperty("role", "card")
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(5)
        self.fields = {}
        rows = (("Position", "pos"), ("Error", "err"), ("Confidence", "conf"), ("Sightings", "seen"),
                ("Modalities", "mod"), ("First seen", "first"), ("Last seen", "last"), ("Team", "team"),
                ("People", "people"), ("Team note", "tnote"), ("Note", "cnote"), ("Alerts", "receipts"))
        for r, (label, key) in enumerate(rows):
            name = QLabel(label)
            name.setProperty("role", "dim")
            value = QLabel("-")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name, r, 0, Qt.AlignTop)
            grid.addWidget(value, r, 1)
            self.fields[key] = value
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy the coordinates")
        self.btn_copy.clicked.connect(self._copy_position)
        grid.addWidget(self.btn_copy, 0, 2)
        grid.setColumnStretch(1, 1)
        c.addWidget(card)

        dist_title = QLabel("DISTANCE")
        dist_title.setProperty("role", "h2")
        c.addWidget(dist_title)
        self.distances = QLabel("-")
        self.distances.setProperty("role", "mono")
        self.distances.setWordWrap(True)
        c.addWidget(self.distances)

        act_title = QLabel("ACTIONS")
        act_title.setProperty("role", "h2")
        c.addWidget(act_title)
        self.team_combo = QComboBox()
        c.addWidget(self.team_combo)
        self.actions_box = QVBoxLayout()
        self.actions_box.setSpacing(6)
        c.addLayout(self.actions_box)
        self.btn_note = QPushButton("Add note")
        self.btn_note.clicked.connect(self._add_note)
        c.addWidget(self.btn_note)

        hist_title = QLabel("HISTORY")
        hist_title.setProperty("role", "h2")
        c.addWidget(hist_title)
        self.history = QListWidget()
        self.history.setMinimumHeight(140)
        c.addWidget(self.history)

        self.content.hide()

    # --------------------------------------------------------------- show ---
    def show_victim(self, victim_id: str | None) -> None:
        self.victim_id = victim_id
        self.refresh()

    def set_uav_pose(self, pose) -> None:
        self._uav_pose = pose

    def refresh(self) -> None:
        v = self.m.victims.get(self.victim_id) if self.victim_id else None
        if v is None:
            self.victim_id = None
            self.content.hide()
            self.empty.show()
            return
        self.empty.hide()
        self.content.show()
        teams = {t["id"]: t for t in self.m.teams.list()}
        color = theme.STATUS_COLOR.get(v["status"], theme.TEXT)
        self.code.setText(v["display_code"])
        self.chip.setText(theme.STATUS_LABEL.get(v["status"], v["status"]))
        self.chip.setStyleSheet(theme.chip_style(color))

        team = teams.get(v["assigned_team_id"])
        f = self.fields
        f["pos"].setText(f"{v['lat']:.6f}, {v['lon']:.6f}")
        f["err"].setText(f"±{v['position_error_m']:.1f} m" if v["position_error_m"] else "-")
        f["conf"].setText(f"{v['max_confidence'] * 100:.0f}%" if v["max_confidence"] is not None else "-")
        f["seen"].setText(str(v["sighting_count"]) + (" (manual)" if v["source"] == "manual" else ""))
        f["mod"].setText({"both": "RGB + thermal", "rgb": "RGB", "thermal": "Thermal"}.get(v["modalities"], "-"))
        f["first"].setText(clock(v["first_detected_at"]))
        f["last"].setText(clock(v["last_seen_at"]))
        f["team"].setText(f"{team['name']} ({team['call_sign']})" if team and team["call_sign"]
                          else (team["name"] if team else "-"))
        f["people"].setText(str(v["people_count"]) if v["people_count"] is not None else "-")
        f["tnote"].setText(v["team_note"] or "-")
        f["cnote"].setText(v["coordinator_note"] or "-")
        r = self.m.victims.receipts(v["id"])
        f["receipts"].setText(f"{r['delivered']} of {r['devices']} delivered, {r['opened']} opened"
                              if v["status"] != "pending" else "not sent (pending verification)")

        self._show_distances(v, teams.values())
        self._show_image(v)
        self._build_actions(v, teams)
        self._show_history(v)

    def _show_distances(self, v, teams) -> None:
        lines = []
        now = int(time.time() * 1000)
        for t in teams:
            if t["last_lat"] is None:
                continue
            d = geo.haversine_m(t["last_lat"], t["last_lon"], v["lat"], v["lon"])
            b = geo.bearing_deg(t["last_lat"], t["last_lon"], v["lat"], v["lon"])
            stale = self.m.teams.is_stale(t, now)
            lines.append(f"{(t['call_sign'] or t['name']):<8} {d:>7.0f} m  {b:03.0f}°{'  (stale)' if stale else ''}")
        pose = self._uav_pose
        if pose and pose.get("lat") is not None:
            d = geo.haversine_m(pose["lat"], pose["lon"], v["lat"], v["lon"])
            b = geo.bearing_deg(pose["lat"], pose["lon"], v["lat"], v["lon"])
            lines.append(f"{'UAV':<8} {d:>7.0f} m  {b:03.0f}°")
        self.distances.setText("\n".join(lines) or "No team or UAV position yet")

    def _show_image(self, v) -> None:
        for key, button in self.size_buttons.items():
            button.setEnabled(self._image_path(v["id"], key) is not None)
        path = self._image_path(v["id"], self._image_size) or self._image_path(v["id"], "crop")
        if path is None:
            self.image.setPixmap(QPixmap())
            self.image.setText("No image (manual entry)")
            return
        pix = QPixmap(str(path))
        self.image.setPixmap(pix.scaled(self.image.width() - 4 if self.image.width() > 50 else 340, 260,
                                        Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _image_path(self, victim_id, size):
        try:
            return self.m.victims.image_path(victim_id, size)
        except ServiceError:
            return None

    def _build_actions(self, v, teams) -> None:
        while self.actions_box.count():
            w = self.actions_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.team_combo.clear()
        actions = sm.coordinator_actions(v["status"])
        needs_team = any(a in ("assign", "reassign") for a, _ in actions)
        self.team_combo.setVisible(needs_team)
        if needs_team:
            for t in teams.values():
                if t["is_active"] and t["id"] != v["assigned_team_id"]:
                    self.team_combo.addItem(f"{t['name']} ({t['call_sign'] or '-'})", t["id"])
        for action, _to in actions:
            b = QPushButton(ACTION_LABEL.get(action, action))
            if action in ACTION_VARIANT:
                b.setProperty("variant", ACTION_VARIANT[action])
            if action in ("assign", "reassign") and self.team_combo.count() == 0:
                b.setEnabled(False)
                b.setToolTip("No other active team")
            b.clicked.connect(lambda _c=False, a=action: self._run(a))
            self.actions_box.addWidget(b)

    def _show_history(self, v) -> None:
        self.history.clear()
        for h in self.m.victims.history(v["id"]):
            who = h["team_name"] or h["actor_type"]
            arrow = f"{h['from_status'] or 'new'} → {h['to_status']}"
            text = f"{clock(h['server_ts'])}  {arrow}  ({who})"
            if not h["applied"]:
                text += f"  REJECTED: {h['reject_reason']}"
            if h["note"]:
                text += f"\n      “{h['note']}”"
            self.history.addItem(text)
        self.history.scrollToBottom()

    # ------------------------------------------------------------ actions ---
    def _run(self, action: str) -> None:
        vid = self.victim_id
        svc = self.m.victims
        try:
            if action == "verify":
                svc.verify(vid)
            elif action == "dismiss":
                if QMessageBox.question(self, "Dismiss", "Dismiss as a false alarm? Phones will drop it.") \
                        != QMessageBox.Yes:
                    return
                note, ok = QInputDialog.getText(self, "Dismiss", "Reason (optional):")
                if not ok:
                    return
                svc.dismiss(vid, note=note or None)
            elif action == "restore":
                svc.restore(vid)
            elif action == "release":
                svc.release_coordinator(vid)
            elif action in ("assign", "reassign"):
                team_id = self.team_combo.currentData()
                if team_id:
                    svc.assign(vid, team_id)
            elif action == "mark_completed":
                dlg = _CompleteDialog(self)
                if dlg.exec() != QDialog.Accepted:
                    return
                svc.mark_completed(vid, people_count=dlg.people.value(), note=dlg.note.text() or None)
            elif action == "reopen":
                svc.reopen(vid)
        except ServiceError as exc:
            QMessageBox.warning(self, "Not applied", exc.message)
        self.refresh()
        self.changed.emit()

    def _add_note(self) -> None:
        v = self.m.victims.get(self.victim_id) if self.victim_id else None
        if v is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Coordinator note", f"Note for {v['display_code']}:",
                                                 v["coordinator_note"] or "")
        if ok:
            self.m.victims.add_note(v["id"], text.strip())
            self.refresh()
            self.changed.emit()

    def _copy_position(self) -> None:
        QGuiApplication.clipboard().setText(self.fields["pos"].text())

    def _zoom(self) -> None:
        v = self.m.victims.get(self.victim_id) if self.victim_id else None
        if v:
            self.zoomRequested.emit(v["lat"], v["lon"])

    def _set_image_size(self, size: str) -> None:
        self._image_size = size
        v = self.m.victims.get(self.victim_id) if self.victim_id else None
        if v:
            self._show_image(v)

    def _open_full_image(self) -> None:
        if not self.victim_id:
            return
        path = self._image_path(self.victim_id, "frame") or self._image_path(self.victim_id, self._image_size)
        if path is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.code.text())
        lay = QVBoxLayout(dlg)
        label = QLabel()
        pix = QPixmap(str(path))
        screen = self.screen().availableGeometry()
        label.setPixmap(pix.scaled(int(screen.width() * 0.8), int(screen.height() * 0.8), Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))
        lay.addWidget(label)
        dlg.exec()
