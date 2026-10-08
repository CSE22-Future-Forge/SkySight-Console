"""Capture review window.

Opened by CAPTURE FINDINGS. Freezes the confirmed tracks into a grid of
candidate people, lets the operator select/deselect each one, surfaces likely
duplicates from the existing database, and files the selection with its feature
vector.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.database import NewPersonDraft
from core.features import compute_embedding

from . import theme
from .widgets import Banner, Card, Pill, StatChip

SOURCE_META = {
    "rgb": ("RGB", theme.RGB_C),
    "thermal": ("THERMAL", theme.THERMAL_C),
    "fusion": ("FUSED", theme.FUSION_C),
}


class CandidateTile(QFrame):
    """One person in the review grid: crop, identity, score, match hint."""

    def __init__(self, finding, candidates, parent=None):
        super().__init__(parent)
        self.finding = finding
        self.candidates = candidates
        self.setProperty("role", "card")
        self.setFixedWidth(238)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.best = candidates[0] if candidates else None
        self.is_dup = bool(self.best and self.best.is_duplicate)
        accent = theme.WARN if self.is_dup else theme.BORDER_SOFT
        self.setStyleSheet(
            f"QFrame{{background:{theme.PANEL};border:1px solid {theme.BORDER_SOFT};"
            f"border-left:3px solid {accent};border-radius:8px;}}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(9, 9, 9, 9)
        lay.setSpacing(7)

        self.chk = QCheckBox(f"P-{finding.track_id:03d}")
        self.chk.setChecked(True)
        self.chk.setStyleSheet(f"font-weight:700; color:{theme.TEXT}; font-size:12px;")
        head = QHBoxLayout()
        head.setSpacing(7)
        head.addWidget(self.chk)
        head.addStretch(1)
        source_text, source_color = SOURCE_META.get(
            finding.source, (finding.source.upper(), theme.TEXT_DIM)
        )
        head.addWidget(Pill(source_text, source_color))
        lay.addLayout(head)

        self.image = QLabel()
        self.image.setFixedSize(218, 168)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setStyleSheet(
            f"background:{theme.BG_DEEP};border:1px solid {theme.BORDER_SOFT};"
            "border-radius:5px;"
        )
        self._set_crop(finding.crop_bgr)
        lay.addWidget(self.image)

        meta = QHBoxLayout()
        meta.setSpacing(6)
        meta.addWidget(self._meta_chip("CONF", f"{finding.score * 100:.0f}%"))
        meta.addStretch(1)
        lay.addLayout(meta)

        if self.best and self.best.score >= 0.65:
            warn = self.is_dup
            self.match = QLabel(
                f"{self.best.label}  {self.best.person.person_id}  "
                f"({self.best.score * 100:.0f}%)"
            )
            self.match.setWordWrap(True)
            self.match.setStyleSheet(
                f"color:{theme.WARN if warn else theme.TEXT_MUTED}; font-size:10px;"
                f"font-weight:{'700' if warn else '400'};"
            )
        else:
            self.match = QLabel("No similar person on file")
            self.match.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        lay.addWidget(self.match)

    def _meta_chip(self, caption, value, color=theme.TEXT):
        chip = QLabel(f"{caption}  {value}")
        chip.setStyleSheet(
            f"color:{color};font-family:'Cascadia Mono','Consolas',monospace;"
            "font-size:10px;font-weight:600;"
        )
        return chip

    def _set_crop(self, crop_bgr):
        from core import imaging

        qimage = imaging.to_qimage(crop_bgr)
        if qimage.isNull():
            self.image.setText("no crop")
            self.image.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
            return
        pixmap = QPixmap.fromImage(qimage).scaled(
            QSize(218, 168), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image.setPixmap(pixmap)

    @property
    def selected(self):
        return self.chk.isChecked()

    def set_selected(self, value):
        self.chk.setChecked(value)


class ReviewDialog(QDialog):
    """Select which captured people go into the database."""

    def __init__(self, findings, database, parent=None):
        super().__init__(parent)
        self.db = database
        self.findings = list(findings)
        self.tiles: list[CandidateTile] = []
        self.filed_ids: list[str] = []

        self.setWindowTitle("Capture review — select people")
        self.setModal(True)
        self.resize(1080, 720)
        self.setStyleSheet(f"QDialog{{background:{theme.BG_DEEP};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("CAPTURE REVIEW")
        title.setProperty("role", "title")
        header.addWidget(title)
        subtitle = QLabel("select or deselect each person before filing to the database")
        subtitle.setProperty("role", "dim")
        header.addWidget(subtitle)
        header.addStretch(1)
        root.addLayout(header)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_candidates = StatChip("Candidates", str(len(self.findings)))
        self.chip_selected = StatChip("Selected", str(len(self.findings)), theme.OK)
        self.chip_db = StatChip("Database", f"{self.db.count()} people")
        self.chip_dups = StatChip("Possible duplicates", "0", theme.WARN)
        for chip in (self.chip_candidates, self.chip_selected, self.chip_db, self.chip_dups):
            chips.addWidget(chip)
        chips.addStretch(1)
        root.addLayout(chips)

        self.banner = Banner("")
        root.addWidget(self.banner)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        self.btn_all = QPushButton("SELECT ALL")
        self.btn_all.setProperty("variant", "ghost")
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none = QPushButton("DESELECT ALL")
        self.btn_none.setProperty("variant", "ghost")
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.btn_dupes = QPushButton("SELECT DUPLICATE CANDIDATES")
        self.btn_dupes.setProperty("variant", "ghost")
        self.btn_dupes.setToolTip(
            "Tick only the people that look like someone already in the database, "
            "so you can review the near matches together."
        )
        self.btn_dupes.clicked.connect(self._select_duplicates)
        for btn in (self.btn_all, self.btn_none, self.btn_dupes):
            controls.addWidget(btn)
        controls.addStretch(1)
        self.hint = QLabel("Tick a person to file them")
        self.hint.setProperty("role", "dim")
        controls.addWidget(self.hint)
        root.addLayout(controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        grid_host = QWidget()
        self.grid = QGridLayout(grid_host)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(grid_host)
        root.addWidget(scroll, 1)

        self._populate_grid()

        footer = QHBoxLayout()
        footer.setSpacing(9)
        self.btn_file = QPushButton("ADD TO DATABASE")
        self.btn_file.setProperty("variant", "primary")
        self.btn_file.clicked.connect(self.file_selection)
        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setProperty("variant", "ghost")
        self.btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.btn_file)
        footer.addWidget(self.btn_cancel)
        footer.addStretch(1)
        root.addLayout(footer)

        self._refresh_counts()

    # ------------------------------------------------------------------ grid ---
    def _populate_grid(self):
        columns = 4
        for index, finding in enumerate(self.findings):
            candidates = self.db.match_candidates(
                compute_embedding(finding.crop_bgr), limit=2
            )
            tile = CandidateTile(finding, candidates)
            tile.chk.toggled.connect(self._refresh_counts)
            self.tiles.append(tile)
            self.grid.addWidget(tile, index // columns, index % columns)
        for col in range(columns):
            self.grid.setColumnStretch(col, 1)

    def _set_all(self, value):
        for tile in self.tiles:
            tile.set_selected(value)
        self._refresh_counts()

    def _select_duplicates(self):
        for tile in self.tiles:
            tile.set_selected(tile.is_dup)
        self._refresh_counts()

    def selected_findings(self):
        return [tile.finding for tile in self.tiles if tile.selected]

    def _refresh_counts(self):
        chosen = self.selected_findings()
        dupes = sum(1 for t in self.tiles if t.selected and t.is_dup)
        self.chip_selected.set_value(str(len(chosen)), theme.OK if chosen else theme.TEXT_MUTED)
        self.chip_dups.set_value(str(dupes), theme.WARN if dupes else theme.TEXT_MUTED)
        self.btn_file.setText(
            f"ADD {len(chosen)} TO DATABASE" if chosen else "ADD TO DATABASE"
        )
        self.btn_file.setEnabled(bool(chosen))
        self.hint.setText(
            f"{len(chosen)} of {len(self.tiles)} selected" if chosen else "Tick a person to file them"
        )

    # ------------------------------------------------------------- filing ---
    def file_selection(self):
        chosen = self.selected_findings()
        if not chosen:
            return

        dupes = [t for t in self.tiles if t.selected and t.is_dup]
        if dupes:
            names = ", ".join(
                f"{t.finding.track_id:03d}~{t.best.person.person_id}" for t in dupes
            )
            answer = QMessageBox.question(
                self,
                "Possible duplicates",
                f"{len(dupes)} of the selected people closely match someone already "
                f"in the database:\n\n{names}\n\nFile them anyway as new records?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        drafts = [f.to_draft() for f in chosen]
        created = self.db.add_drafts(drafts)
        self.filed_ids = [p.person_id for p in created]

        self.banner.show_message(
            f"Filed {len(created)} "
            f"{'person' if len(created) == 1 else 'people'} to the database: "
            f"{', '.join(self.filed_ids)}.",
            "info",
        )
        QMessageBox.information(
            self,
            "Added to database",
            f"{len(created)} record(s) written:\n"
            + "\n".join(f"  {p.person_id}  ({p.capture_count} capture)" for p in created)
            + f"\n\nStorage: {self.db.path}"
            + "\nThey are being sent to the SAR Manifest ground station.",
        )
        self.accept()

    def filed_person_ids(self):
        return list(self.filed_ids)