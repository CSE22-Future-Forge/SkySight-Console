"""Teams panel (spec 4.3): online/offline, battery, last seen, assignment; revoke a device."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ground_station.ui import theme
from ground_station.ui.manifest_panel import age_text

TEAM_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf")


class TeamDialog(QDialog):
    def __init__(self, parent=None, team=None, default_color="#1f77b4"):
        super().__init__(parent)
        self.setWindowTitle("Edit team" if team else "New team")
        form = QFormLayout(self)
        self.name = QLineEdit(team["name"] if team else "")
        self.call_sign = QLineEdit((team["call_sign"] or "") if team else "")
        self.members = QSpinBox()
        self.members.setRange(0, 50)
        self.members.setValue((team["member_count"] or 0) if team else 4)
        self.color = (team["color_hex"] if team else default_color)
        self.btn_color = QPushButton()
        self._paint_color()
        self.btn_color.clicked.connect(self._pick_color)
        form.addRow("Name", self.name)
        form.addRow("Call sign", self.call_sign)
        form.addRow("Members", self.members)
        form.addRow("Map colour", self.btn_color)
        self.active = QCheckBox("Active (shown on the map, can be assigned and paired)")
        self.active.setChecked(bool(team["is_active"]) if team else True)
        form.addRow("", self.active)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _paint_color(self):
        self.btn_color.setText(self.color)
        self.btn_color.setStyleSheet(f"background: {self.color}; color: white; font-weight: 600;")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Team colour")
        if c.isValid():
            self.color = c.name()
            self._paint_color()

    def _accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Team", "A team needs a name.")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "call_sign": self.call_sign.text().strip() or None,
                "member_count": self.members.value() or None, "color_hex": self.color,
                "is_active": 1 if self.active.isChecked() else 0}


def _item(text, color=None, align=Qt.AlignCenter):
    it = QTableWidgetItem(text)
    it.setTextAlignment(align | Qt.AlignVCenter)
    if color:
        it.setForeground(QBrush(QColor(color)))
    return it


class TeamsPanel(QWidget):
    changed = Signal()
    pairRequested = Signal(str)  # team id

    def __init__(self, manifest, hub_ids=lambda: [], parent=None):
        super().__init__(parent)
        self.m = manifest
        self._hub_ids = hub_ids  # live WebSocket device ids from the hub
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        head = QHBoxLayout()
        t = QLabel("TEAMS")
        t.setProperty("role", "h2")
        head.addWidget(t)
        head.addStretch(1)
        self.btn_add = QPushButton("+ Team")
        self.btn_add.clicked.connect(self._add_team)
        self.btn_edit = QPushButton("Edit")
        self.btn_edit.clicked.connect(self._edit_team)
        self.btn_pair = QPushButton("Pair phone")
        self.btn_pair.clicked.connect(self._pair_selected)
        for b in (self.btn_add, self.btn_edit, self.btn_pair):
            head.addWidget(b)
        lay.addLayout(head)

        self.teams = QTableWidget(0, 5)
        self.teams.setHorizontalHeaderLabels(["Team", "Phones", "Battery", "Last fix", "Jobs"])
        self._style_table(self.teams)
        self.teams.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.teams, 3)

        d = QLabel("PHONES")
        d.setProperty("role", "h2")
        lay.addWidget(d)
        self.devices = QTableWidget(0, 5)
        self.devices.setHorizontalHeaderLabels(["Device", "Team", "Link", "Last seen", ""])
        self._style_table(self.devices)
        self.devices.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.devices, 2)

    @staticmethod
    def _style_table(table):
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    # -------------------------------------------------------------- data ---
    def refresh(self) -> None:
        now = int(time.time() * 1000)
        teams = self.m.teams.list()
        devices = self.m.devices.list()
        live = set(self._hub_ids())
        victims = self.m.victims.list(statuses=("ongoing",))
        selected = self._selected_team_id()

        self.teams.setRowCount(len(teams))
        for r, t in enumerate(teams):
            stale = self.m.teams.is_stale(t, now)
            team_devices = [d for d in devices if d["team_id"] == t["id"] and not d["revoked"]]
            online = sum(1 for d in team_devices if d["id"] in live)
            name = _item(f"● {t['name']}" + (f"  ({t['call_sign']})" if t["call_sign"] else ""),
                         t["color_hex"] if t["is_active"] else theme.TEXT_MUTED, Qt.AlignLeft)
            name.setData(Qt.UserRole, t["id"])
            if not t["is_active"]:
                name.setText(name.text() + "  (inactive)")
            self.teams.setItem(r, 0, name)
            self.teams.setItem(r, 1, _item(f"{online}/{len(team_devices)}",
                                           theme.OK if online else theme.TEXT_MUTED))
            bat = t["last_battery_pct"]
            self.teams.setItem(r, 2, _item(f"{bat}%" if bat is not None else "-",
                                           theme.DANGER if bat is not None and bat <= 20 else None))
            self.teams.setItem(r, 3, _item(age_text(t["last_fix_at"], now) + (" stale" if stale and t["last_fix_at"]
                                                                               else ""),
                                           theme.WARN if stale else theme.OK))
            jobs = [v["display_code"] for v in victims if v["assigned_team_id"] == t["id"]]
            self.teams.setItem(r, 4, _item(", ".join(jobs) or "-"))
            if t["id"] == selected:
                self.teams.selectRow(r)

        names = {t["id"]: t["name"] for t in teams}
        self.devices.setRowCount(len(devices))
        for r, d in enumerate(devices):
            if d["revoked"]:
                state, color = "revoked", theme.TEXT_MUTED
            elif d["id"] in live:
                state, color = "live", theme.OK
            else:
                state, color = "offline", theme.WARN
            self.devices.setItem(r, 0, _item(d["label"], None if not d["revoked"] else theme.TEXT_MUTED, Qt.AlignLeft))
            self.devices.setItem(r, 1, _item(names.get(d["team_id"], "?")))
            self.devices.setItem(r, 2, _item(state, color))
            self.devices.setItem(r, 3, _item(age_text(d["last_seen_at"], now)))
            if d["revoked"]:
                self.devices.removeCellWidget(r, 4)
                self.devices.setItem(r, 4, _item(""))
            else:
                b = QPushButton("Revoke")
                b.setProperty("variant", "danger")
                b.clicked.connect(lambda _c=False, did=d["id"], label=d["label"]: self._revoke(did, label))
                self.devices.setCellWidget(r, 4, b)

    def _selected_team_id(self):
        rows = self.teams.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.teams.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    # ----------------------------------------------------------- actions ---
    def _add_team(self):
        used = {t["color_hex"] for t in self.m.teams.list()}
        color = next((c for c in TEAM_COLORS if c not in used), TEAM_COLORS[0])
        dlg = TeamDialog(self, default_color=color)
        if dlg.exec() == QDialog.Accepted:
            self.m.teams.create(**dlg.values())
            self.refresh()
            self.changed.emit()

    def _edit_team(self):
        team_id = self._selected_team_id()
        team = self.m.teams.get(team_id) if team_id else None
        if team is None:
            QMessageBox.information(self, "Teams", "Select a team first.")
            return
        dlg = TeamDialog(self, team)
        if dlg.exec() == QDialog.Accepted:
            self.m.teams.update(team_id, **dlg.values())
            self.refresh()
            self.changed.emit()

    def _pair_selected(self):
        self.pairRequested.emit(self._selected_team_id() or "")

    def _revoke(self, device_id, label):
        if QMessageBox.question(self, "Revoke device",
                                f"Revoke '{label}'? It is disconnected now and must pair again.") == QMessageBox.Yes:
            self.m.devices.revoke(device_id)
            self.refresh()
            self.changed.emit()
