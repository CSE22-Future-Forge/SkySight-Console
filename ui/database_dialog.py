"""Database browser.

Shows every filed person, their capture thumbnails and stored feature vectors,
and runs the duplicate scan that surfaces near-identical records inside the
roster — the "don't file the same person twice" safety net.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.features import EMBEDDING_DIM, cosine_similarity

from . import theme
from .widgets import Banner, Card, Pill, StatChip, qt_color

COLUMNS = ("PERSON ID", "FILED", "CAPTURES", "SOURCES", "PEAK TEMP", "BEST MATCH", "STATUS")


class DatabaseDialog(QDialog):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self._flagged = set()

        self.setWindowTitle("Person database")
        self.resize(1120, 700)
        self.setStyleSheet(f"QDialog{{background:{theme.BG_DEEP};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("PERSON DATABASE")
        title.setProperty("role", "title")
        header.addWidget(title)
        subtitle = QLabel("stored appearances with feature vectors for duplicate screening")
        subtitle.setProperty("role", "dim")
        header.addWidget(subtitle)
        header.addStretch(1)
        root.addLayout(header)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_people = StatChip("People", str(self.db.count()))
        self.chip_captures = StatChip("Captures", str(self.db.capture_count()))
        self.chip_dupes = StatChip("Duplicate pairs", "0", theme.WARN)
        self.chip_dim = StatChip("Feature vector", f"{EMBEDDING_DIM}-d", theme.TEXT_MUTED)
        for chip in (self.chip_people, self.chip_captures, self.chip_dupes, self.chip_dim):
            chips.addWidget(chip)
        chips.addStretch(1)
        root.addLayout(chips)

        self.banner = Banner("")
        root.addWidget(self.banner)

        body = QHBoxLayout()
        body.setSpacing(11)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(7)

        self.btn_scan = QPushButton("RUN DUPLICATE SCAN")
        self.btn_scan.setProperty("variant", "primary")
        self.btn_scan.clicked.connect(self.run_scan)
        left_lay.addWidget(self.btn_scan)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_select)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.Stretch)
        self.table.setColumnWidth(0, 96)
        self.table.setColumnWidth(1, 132)
        self.table.setColumnWidth(2, 74)
        self.table.setColumnWidth(3, 108)
        self.table.setColumnWidth(4, 86)
        self.table.setColumnWidth(5, 118)
        left_lay.addWidget(self.table, 1)

        body.addWidget(left, 1)

        detail = QWidget()
        detail.setFixedWidth(320)
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.setSpacing(7)

        self.detail_card = Card("Record detail")
        self.detail_card.finish_header()
        self.detail_card.add(self._build_detail_body())
        detail_lay.addWidget(self.detail_card)

        actions = QWidget()
        actions_lay = QVBoxLayout(actions)
        actions_lay.setContentsMargins(0, 0, 0, 0)
        actions_lay.setSpacing(7)

        self.btn_add_capture = QPushButton("ADD CAPTURE TO SELECTED")
        self.btn_add_capture.setProperty("variant", "ghost")
        self.btn_add_capture.setEnabled(False)
        self.btn_add_capture.setToolTip(
            "File another crop of this person later without creating a new record."
        )
        self.btn_add_capture.clicked.connect(self._add_capture_placeholder)

        self.btn_delete = QPushButton("DELETE RECORD")
        self.btn_delete.setProperty("variant", "danger")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_selected)

        self.btn_export = QPushButton("EXPORT JSON")
        self.btn_export.setProperty("variant", "ghost")
        self.btn_export.clicked.connect(self.export_json)

        self.btn_close = QPushButton("CLOSE")
        self.btn_close.setProperty("variant", "ghost")
        self.btn_close.clicked.connect(self.accept)

        for btn in (
            self.btn_add_capture,
            self.btn_delete,
            self.btn_export,
            self.btn_close,
        ):
            actions_lay.addWidget(btn)
        actions_lay.addStretch(1)
        detail_lay.addWidget(actions)
        detail_lay.addStretch(1)

        body.addWidget(detail)
        root.addLayout(body, 1)

        footer = QLabel(f"Storage: {self.db.path}")
        footer.setProperty("role", "mono")
        footer.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        root.addWidget(footer)

        self.refresh()

    # --------------------------------------------------------------- detail ---
    def _build_detail_body(self):
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.detail_pill = Pill("NO SELECTION", theme.TEXT_MUTED)
        lay.addWidget(self.detail_pill)

        self.detail_image = QLabel("Select a person")
        self.detail_image.setAlignment(Qt.AlignCenter)
        self.detail_image.setFixedSize(288, 196)
        self.detail_image.setStyleSheet(
            f"background:{theme.BG_DEEP};border:1px solid {theme.BORDER_SOFT};"
            "border-radius:6px;color:#61748a;font-size:11px;"
        )
        lay.addWidget(self.detail_image)

        self.detail_info = QLabel("")
        self.detail_info.setWordWrap(True)
        self.detail_info.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:11px;")
        lay.addWidget(self.detail_info)

        caption = QLabel("STORED FEATURE VECTOR")
        caption.setProperty("role", "section")
        lay.addWidget(caption)

        self.vector_view = QListWidget()
        self.vector_view.setFixedHeight(112)
        self.vector_view.setStyleSheet(
            f"background:{theme.BG_DEEP};font-family:'Cascadia Mono','Consolas',monospace;"
            f"font-size:10px;color:{theme.TEXT_MUTED};"
        )
        lay.addWidget(self.vector_view)
        return host

    # ---------------------------------------------------------------- table ---
    def refresh(self):
        people = self.db.all()
        self.table.setRowCount(len(people))

        for row, person in enumerate(people):
            best = self._best_match_for(person, exclude=person.person_id)
            flagged = person.person_id in self._flagged

            id_item = QTableWidgetItem(person.person_id)
            font = id_item.font()
            font.setBold(True)
            id_item.setFont(font)
            id_item.setForeground(qt_color(theme.WARN if flagged else theme.TEXT))

            filed_item = QTableWidgetItem(person.first_seen)
            filed_item.setForeground(qt_color(theme.TEXT_DIM))

            cap_item = QTableWidgetItem(str(person.capture_count))
            cap_item.setTextAlignment(Qt.AlignCenter)

            src_item = QTableWidgetItem("/".join(s.upper() for s in person.sources) or "--")
            src_item.setTextAlignment(Qt.AlignCenter)
            src_item.setForeground(qt_color(theme.FUSION_C))

            temp = person.best_temp
            temp_item = QTableWidgetItem(f"{temp:.1f} C" if temp is not None else "--")
            temp_item.setTextAlignment(Qt.AlignCenter)
            temp_item.setForeground(
                qt_color(theme.THERMAL_C if temp is not None else theme.TEXT_MUTED)
            )

            if best and best[1] >= self.db.warn_at:
                best_text = f"{best[0].person_id} ({best[1] * 100:.0f}%)"
                best_item = QTableWidgetItem(best_text)
                best_item.setForeground(qt_color(theme.WARN))
            elif best:
                best_item = QTableWidgetItem(f"{best[0].person_id} ({best[1] * 100:.0f}%)")
                best_item.setForeground(qt_color(theme.TEXT_MUTED))
            else:
                best_item = QTableWidgetItem("--")
                best_item.setForeground(qt_color(theme.TEXT_MUTED))
            best_item.setTextAlignment(Qt.AlignCenter)

            if flagged:
                status_text, status_color = "DUPLICATE?", theme.WARN
            else:
                status_text, status_color = "UNIQUE", theme.OK
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(qt_color(status_color))

            for col, item in enumerate(
                (id_item, filed_item, cap_item, src_item, temp_item, best_item, status_item)
            ):
                if col in (1, 3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

        self.chip_people.set_value(str(self.db.count()))
        self.chip_captures.set_value(str(self.db.capture_count()))
        self.chip_dupes.set_value(
            str(len(self.db.duplicate_report())), theme.WARN if self._flagged else theme.TEXT_MUTED
        )
        self._clear_detail()

    def _best_match_for(self, person, exclude=None):
        best = None
        for other in self.db.all():
            if other.person_id == exclude:
                continue
            score = cosine_similarity(person.embedding, other.embedding)
            if best is None or score > best[1]:
                best = (other, score)
        return best

    def run_scan(self):
        pairs = self.db.duplicate_report()
        self._flagged = self.db.flagged_ids()
        self.refresh()
        if not pairs:
            self.banner.show_message(
                f"Duplicate scan complete — no pair above the "
                f"{self.db.warn_at:.0%} similarity threshold.", "info",
            )
            return

        lines = [f"{len(pairs)} possible duplicate pair(s) above {self.db.warn_at:.0%}:"]
        for a, b, score in pairs[:12]:
            lines.append(f"  {a.person_id}  ~  {b.person_id}   {score * 100:.0f}%")
        if len(pairs) > 12:
            lines.append(f"  ... and {len(pairs) - 12} more")
        self.banner.show_message("\n".join(lines).replace("\n", "  "), "warn")

    def _on_select(self):
        person = self.selected_person()
        if person is None:
            self._clear_detail()
            return

        self.btn_delete.setEnabled(True)
        self.btn_add_capture.setEnabled(True)

        flagged = person.person_id in self._flagged
        self.detail_pill.set_state(
            person.person_id, theme.WARN if flagged else theme.FUSION_C
        )

        image = self.db.load_image(person)
        if image is not None:
            from core import imaging

            pixmap = QPixmap.fromImage(imaging.to_qimage(image)).scaled(
                QSize(288, 196), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.detail_image.setPixmap(pixmap)
            self.detail_image.setText("")
        else:
            self.detail_image.setPixmap(QPixmap())
            self.detail_image.setText("no thumbnail on disk")

        best = self._best_match_for(person, exclude=person.person_id)
        best_text = (
            f"closest: {best[0].person_id} at {best[1] * 100:.0f}%"
            if best
            else "no other records to compare"
        )
        if flagged:
            best_text = "FLAGGED AS POSSIBLE DUPLICATE — " + best_text

        self.detail_info.setText(
            f"Filed: {person.first_seen}\n"
            f"Captures: {person.capture_count}\n"
            f"Sources: {', '.join(person.sources) or '--'}\n"
            f"Peak temp: "
            f"{f'{person.best_temp:.1f} C' if person.best_temp is not None else '--'}\n"
            f"{best_text}"
        )

        vec = person.embedding or []
        rows = []
        for start in range(0, len(vec), 8):
            chunk = vec[start : start + 8]
            rows.append(f"{start:4d}  " + " ".join(f"{v:+.3f}" for v in chunk))
        self.vector_view.clear()
        self.vector_view.addItems(rows or ["empty vector"])

    def _clear_detail(self):
        self.detail_pill.set_state("NO SELECTION", theme.TEXT_MUTED)
        self.detail_image.setPixmap(QPixmap())
        self.detail_image.setText("Select a person")
        self.detail_info.setText("")
        self.vector_view.clear()
        self.btn_delete.setEnabled(False)
        self.btn_add_capture.setEnabled(False)

    def selected_person(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        item = self.table.item(indexes[0].row(), 0)
        return self.db.get(item.text()) if item else None

    # -------------------------------------------------------------- actions ---
    def _add_capture_placeholder(self):
        person = self.selected_person()
        if person is None:
            return
        QMessageBox.information(
            self,
            "Add capture",
            "Attach another crop to this record from a live capture: select the "
            "person in a track table, then use CAPTURE FINDINGS and file them "
            "into this record.",
        )

    def delete_selected(self):
        person = self.selected_person()
        if person is None:
            return
        answer = QMessageBox.warning(
            self,
            "Delete record",
            f"Delete {person.person_id} and its {person.capture_count} capture(s)?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self.db.delete(person.person_id)
        self._flagged.discard(person.person_id)
        self.refresh()
        self.banner.show_message(f"Deleted {person.person_id}.", "info")

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export database", "persons_export.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            self.db.export_json(path)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        size_kb = os.path.getsize(path) / 1024.0
        self.banner.show_message(f"Exported {self.db.count()} records to {path} ({size_kb:.1f} KB).")