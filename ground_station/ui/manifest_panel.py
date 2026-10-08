"""Manifest (left panel): sortable victim list (spec 4.3). Click -> zoom map + open detail."""

from __future__ import annotations

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QTableView, \
    QVBoxLayout, QWidget

from ground_station.ui import theme

SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2
COLUMNS = ("Code", "Status", "Age", "Team", "Seen", "Conf")


def age_text(since_ms: int | None, now_ms: int | None = None) -> str:
    if not since_ms:
        return "-"
    secs = max(0, ((now_ms or int(time.time() * 1000)) - since_ms) // 1000)
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h {mins % 60:02d}m"


class VictimTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.teams: dict = {}

    def set_rows(self, rows, teams_by_id) -> None:
        self.beginResetModel()
        self.rows = [dict(r) for r in rows]
        self.teams = teams_by_id
        self.endResetModel()

    def refresh_ages(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 2), self.index(len(self.rows) - 1, 2), [Qt.DisplayRole])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def _team_name(self, v):
        team = self.teams.get(v["assigned_team_id"]) if v["assigned_team_id"] else None
        return (team["call_sign"] or team["name"]) if team else ""

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()]
        col = index.column()
        if role == ID_ROLE:
            return v["id"]
        if role == Qt.DisplayRole:
            if col == 0:
                return v["display_code"] + (" ✎" if v["source"] == "manual" else "")
            if col == 1:
                return theme.STATUS_LABEL.get(v["status"], v["status"].upper())
            if col == 2:
                return age_text(v["first_detected_at"])
            if col == 3:
                return self._team_name(v)
            if col == 4:
                return str(v["sighting_count"])
            if col == 5:
                return f"{v['max_confidence'] * 100:.0f}%" if v["max_confidence"] is not None else "-"
        if role == SORT_ROLE:
            return (v["display_code"], theme.STATUS_PRIORITY.get(v["status"], 9), -v["first_detected_at"],
                    self._team_name(v), v["sighting_count"], v["max_confidence"] or 0)[col]
        if role == Qt.ForegroundRole and col == 1:
            return QBrush(QColor(theme.STATUS_COLOR.get(v["status"], theme.TEXT)))
        if role == Qt.FontRole and col in (0, 1):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and col >= 2:
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole and v["coordinator_note"]:
            return v["coordinator_note"]
        return None


class _Proxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hidden_statuses: set[str] = {"dismissed"}

    def filterAcceptsRow(self, row, parent):
        v = self.sourceModel().rows[row]
        return v["status"] not in self.hidden_statuses


class ManifestPanel(QWidget):
    victimSelected = Signal(str)
    filtersChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("MANIFEST")
        title.setProperty("role", "h2")
        head.addWidget(title)
        head.addStretch(1)
        self.counts = QLabel("")
        self.counts.setProperty("role", "dim")
        head.addWidget(self.counts)
        lay.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(12)
        self.chk_completed = QCheckBox("Rescued")
        self.chk_completed.setChecked(True)
        self.chk_dismissed = QCheckBox("Dismissed")
        for chk in (self.chk_completed, self.chk_dismissed):
            chk.toggled.connect(self._apply_filters)
            filters.addWidget(chk)
        filters.addStretch(1)
        lay.addLayout(filters)

        self.model = VictimTableModel(self)
        self.proxy = _Proxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.selectionModel().selectionChanged.connect(self._on_selection)
        lay.addWidget(self.table, 1)

        self._selected_id = ""
        self._suppress = False

    # ------------------------------------------------------------- data ---
    def hidden_statuses(self) -> set[str]:
        """Statuses filtered out of the list; the map hides the same ones."""
        return set(self.proxy.hidden_statuses)

    def set_victims(self, victims, teams_by_id) -> None:
        self._suppress = True
        self.model.set_rows(victims, teams_by_id)
        self._suppress = False
        self._reselect()
        counts = {}
        for v in victims:
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        parts = [f"{counts[s]} {theme.STATUS_LABEL[s].lower()}" for s in ("pending", "open", "ongoing", "completed")
                 if counts.get(s)]
        self.counts.setText(" · ".join(parts) or "no victims yet")

    def refresh_ages(self) -> None:
        self.model.refresh_ages()

    def select(self, victim_id: str) -> None:
        self._selected_id = victim_id or ""
        self._reselect()

    def _reselect(self) -> None:
        self._suppress = True
        try:
            self.table.clearSelection()
            if not self._selected_id:
                return
            for row in range(self.proxy.rowCount()):
                idx = self.proxy.index(row, 0)
                if idx.data(ID_ROLE) == self._selected_id:
                    self.table.selectRow(row)
                    self.table.scrollTo(idx)
                    return
        finally:
            self._suppress = False

    def _apply_filters(self) -> None:
        hidden = set()
        if not self.chk_completed.isChecked():
            hidden.add("completed")
        if not self.chk_dismissed.isChecked():
            hidden.add("dismissed")
        if hasattr(self.proxy, "beginFilterChange"):  # Qt 6.10+: invalidateFilter() is deprecated
            self.proxy.beginFilterChange()
            self.proxy.hidden_statuses = hidden
            self.proxy.endFilterChange()
        else:
            self.proxy.hidden_statuses = hidden
            self.proxy.invalidateFilter()
        self._reselect()
        self.filtersChanged.emit()

    def _on_selection(self, *_):
        if self._suppress:
            return
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self._selected_id = rows[0].data(ID_ROLE)
            self.victimSelected.emit(self._selected_id)
