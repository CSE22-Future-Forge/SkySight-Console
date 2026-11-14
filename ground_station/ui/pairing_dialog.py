"""Pair a phone: one-time QR code {server_url, pairing_token, ssid} (spec 3 + 10)."""

from __future__ import annotations

import json
import time

import qrcode
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ground_station.config import SSID

QR_PIXELS = 300


def qr_pixmap(text: str, size: int = QR_PIXELS) -> QPixmap:
    """Render a QR code without Pillow: draw the module matrix straight into a QImage."""
    # 4-module quiet zone (QR spec minimum) and an integer scale, so every module is
    # the same number of pixels: uneven modules make phone scanners miss.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    img = QImage(n, n, QImage.Format_RGB32)
    black, white = QColor("black").rgb(), QColor("white").rgb()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            img.setPixel(x, y, black if dark else white)
    scale = max(1, size // n)
    return QPixmap.fromImage(img.scaled(n * scale, n * scale, Qt.IgnoreAspectRatio, Qt.FastTransformation))


class PairingDialog(QDialog):
    def __init__(self, manifest, server_url: str, team_id: str | None = None, parent=None):
        super().__init__(parent)
        self.m = manifest
        self.server_url = server_url
        self.setWindowTitle("Pair a rescue phone")
        self.setMinimumWidth(380)
        self.expires_at = 0

        lay = QVBoxLayout(self)
        steps = QLabel(f"1. Join the phone to Wi-Fi <b>{SSID}</b>.<br>2. Open the SAR app and scan this code.<br>"
                       "3. Keep the phone on Wi-Fi until the map download finishes.")
        steps.setWordWrap(True)
        lay.addWidget(steps)

        row = QHBoxLayout()
        row.addWidget(QLabel("Team"))
        self.team = QComboBox()
        self.team.addItem("Phone chooses", "")
        for t in manifest.teams.list(active_only=True):
            self.team.addItem(t["name"], t["id"])
        if team_id:
            idx = self.team.findData(team_id)
            if idx >= 0:
                self.team.setCurrentIndex(idx)
        self.team.currentIndexChanged.connect(self.regenerate)
        row.addWidget(self.team, 1)
        lay.addLayout(row)

        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setMinimumSize(QR_PIXELS + 10, QR_PIXELS + 10)
        self.qr.setStyleSheet("background: white; border-radius: 6px;")
        lay.addWidget(self.qr)

        self.details = QLabel()
        self.details.setProperty("role", "mono")
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.setWordWrap(True)
        lay.addWidget(self.details)
        self.countdown = QLabel()
        self.countdown.setProperty("role", "dim")
        lay.addWidget(self.countdown)

        buttons = QHBoxLayout()
        self.btn_new = QPushButton("New code")
        self.btn_new.clicked.connect(self.regenerate)
        close = QPushButton("Done")
        close.setProperty("variant", "primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_new)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.regenerate()

    def regenerate(self, *_):
        pairing = self.m.devices.create_pairing(team_id=self.team.currentData() or None, server_url=self.server_url)
        self.payload = pairing["qr_payload"]
        self.expires_at = pairing["expires_at"]
        self.qr.setPixmap(qr_pixmap(json.dumps(self.payload, separators=(",", ":"))))
        self.details.setText(f"Server  {self.server_url}\nCode    {pairing['pairing_token']}")
        self._tick()

    def _tick(self):
        left = int(self.expires_at / 1000 - time.time())
        if left <= 0:
            self.countdown.setText("Code expired - press New code.")
            self.qr.setEnabled(False)
        else:
            self.countdown.setText(f"One-time code, valid for {left // 60}:{left % 60:02d}")
            self.qr.setEnabled(True)
