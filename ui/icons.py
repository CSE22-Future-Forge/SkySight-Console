"""Raster glyphs for Qt stylesheet sub-controls.

Qt stylesheets can only paint ``::indicator`` and ``::*-arrow`` sub-controls from
a real image referenced with ``url()``. Two common CSS tricks fail here:

* ``image: none`` on ``::indicator:checked`` draws no tick at all.
* the zero-size bordered-triangle trick (``width: 0; height: 0`` plus transparent
  left/right borders) renders as an empty button box for spin box arrows.

So the tick, radio dot and arrows are drawn with QPainter, cached on disk and
referenced by absolute path from :func:`ui.theme.stylesheet`.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

# Drawn oversized and scaled down by Qt for smooth edges on high-DPI screens.
SCALE = 3

DARK = "#12091f"

# name -> (css width, css height, kind, colour)
SPECS = {
    "check": (15, 15, "check", DARK),
    "radio": (15, 15, "radio", "#a78bfa"),
    "arrow_up": (9, 6, "triangle_up", "#93a3b8"),
    "arrow_up_hover": (9, 6, "triangle_up", DARK),
    "arrow_down": (9, 6, "triangle_down", "#93a3b8"),
    "arrow_down_hover": (9, 6, "triangle_down", DARK),
    "combo_down": (10, 6, "triangle_down", "#93a3b8"),
    "combo_down_hover": (10, 6, "triangle_down", "#e6edf3"),
}

_paths: dict[str, str] = {}


def _cache_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    path = os.path.join(base, "sar_vision_console", "icons")
    os.makedirs(path, exist_ok=True)
    return path


def _draw_check(painter, w, h, colour):
    pen = QPen(QColor(colour))
    pen.setWidthF(w * 0.15)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(w * 0.23, h * 0.53))
    path.lineTo(QPointF(w * 0.42, h * 0.72))
    path.lineTo(QPointF(w * 0.78, h * 0.29))
    painter.drawPath(path)


def _draw_radio(painter, w, h, colour):
    inset = w * 0.29
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(colour)))
    painter.drawEllipse(QRectF(inset, inset, w - 2 * inset, h - 2 * inset))


def _draw_triangle(painter, w, h, colour, up):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(colour)))
    if up:
        points = [
            QPointF(w * 0.50, h * 0.16),
            QPointF(w * 0.96, h * 0.80),
            QPointF(w * 0.04, h * 0.80),
        ]
    else:
        points = [
            QPointF(w * 0.50, h * 0.84),
            QPointF(w * 0.96, h * 0.20),
            QPointF(w * 0.04, h * 0.20),
        ]
    painter.drawPolygon(QPolygonF(points))


def _render(name, css_w, css_h, kind, colour):
    image = QImage(css_w * SCALE, css_h * SCALE, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    w, h = image.width(), image.height()
    if kind == "check":
        _draw_check(painter, w, h, colour)
    elif kind == "radio":
        _draw_radio(painter, w, h, colour)
    else:
        _draw_triangle(painter, w, h, colour, up=(kind == "triangle_up"))
    painter.end()
    return image


def _url(path: str) -> str:
    """Qt stylesheet URLs need forward slashes and forward-slash absolute paths."""
    return "url(" + path.replace("\\", "/") + ")"


def ensure_all() -> dict[str, str]:
    """Render any missing glyph and return ``name -> css url(...)``."""
    directory = _cache_dir()
    for name, (css_w, css_h, kind, colour) in SPECS.items():
        # The spec is part of the filename so editing a size or colour
        # regenerates the glyph instead of reusing a stale cached file.
        stamp = f"{css_w}x{css_h}-{colour.lstrip('#')}"
        target = os.path.join(directory, f"{name}-{stamp}.png")
        if not os.path.exists(target):
            image = _render(name, css_w, css_h, kind, colour)
            image.save(target, "PNG")
        _paths[name] = _url(target)
    return _paths
