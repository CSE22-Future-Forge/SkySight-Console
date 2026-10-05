"""Reusable presentation widgets shared by both tabs."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

# Every 'Label [field]' row in the control rails shares this label column so the
# fields line up vertically down the whole rail. Callers should not override it.
LABEL_W = 124


def _fullscreen_icon(expand=True):
    """A corner-bracket glyph: outward for 'expand', inward for 'restore'."""
    from PySide6.QtGui import QPixmap

    size = 18
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(theme.TEXT))
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    m, ln = 4.0, 5.0
    far = size - m
    if expand:
        painter.drawLine(QPointF(m, m + ln), QPointF(m, m))
        painter.drawLine(QPointF(m, m), QPointF(m + ln, m))
        painter.drawLine(QPointF(far - ln, m), QPointF(far, m))
        painter.drawLine(QPointF(far, m), QPointF(far, m + ln))
        painter.drawLine(QPointF(m, far - ln), QPointF(m, far))
        painter.drawLine(QPointF(m, far), QPointF(m + ln, far))
        painter.drawLine(QPointF(far - ln, far), QPointF(far, far))
        painter.drawLine(QPointF(far, far), QPointF(far, far - ln))
    else:
        painter.drawLine(QPointF(m, m + ln), QPointF(m + ln, m + ln))
        painter.drawLine(QPointF(m + ln, m + ln), QPointF(m + ln, m))
        painter.drawLine(QPointF(far, m + ln), QPointF(far - ln, m + ln))
        painter.drawLine(QPointF(far - ln, m + ln), QPointF(far - ln, m))
        painter.drawLine(QPointF(m, far - ln), QPointF(m + ln, far - ln))
        painter.drawLine(QPointF(m + ln, far - ln), QPointF(m + ln, far))
        painter.drawLine(QPointF(far, far - ln), QPointF(far - ln, far - ln))
        painter.drawLine(QPointF(far - ln, far - ln), QPointF(far - ln, far))
    painter.end()
    return QIcon(pm)



class Card(QFrame):
    """Bordered surface with an optional caption row."""

    def __init__(self, title="", parent=None, role="card"):
        super().__init__(parent)
        self.setProperty("role", role)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(9)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            self.title_label = QLabel(title.upper())
            self.title_label.setProperty("role", "section")
            self.header.addWidget(self.title_label)
        self._layout.addLayout(self.header)

    def body(self):
        return self._layout

    def add(self, widget, stretch=0):
        self._layout.addWidget(widget, stretch)

    def add_row(self, widget):
        """Append to the header row (right-aligned by default)."""
        self.header.addWidget(widget)
        return widget

    def finish_header(self):
        self.header.addStretch(1)

    @property
    def title(self):
        return self.title_label.text() if hasattr(self, "title_label") else ""


class StatChip(QFrame):
    """Compact live metric: caption above, monospace value below."""

    def __init__(self, caption, value="--", color=None, parent=None):
        super().__init__(parent)
        self.setProperty("role", "rail")
        self._color = color or theme.TEXT

        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(1)

        self.caption = QLabel(caption.upper())
        self.caption.setProperty("role", "section")
        self.caption.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:9px;")

        self.value = QLabel(value)
        self.value.setStyleSheet(
            f"font-family:'Cascadia Mono','Consolas',monospace; "
            f"font-size:14px; font-weight:600; color:{self._color};"
        )

        lay.addWidget(self.caption)
        lay.addWidget(self.value)

    def set_value(self, text, color=None):
        self.value.setText(str(text))
        if color:
            self.value.setStyleSheet(
                f"font-family:'Cascadia Mono','Consolas',monospace; "
                f"font-size:14px; font-weight:600; color:{color};"
            )

    def set_caption(self, text):
        self.caption.setText(text.upper())


class VideoCanvas(QWidget):
    """Aspect-preserving image surface with an optional status overlay."""

    clicked = Signal()
    #: Click position inside the displayed frame, in frame-pixel coordinates.
    picked = Signal(float, float)
    #: The fullscreen/restore button was pressed.
    focusToggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._placeholder_text = "NO SIGNAL"
        self._placeholder_sub = "Start a feed to begin"
        self._placeholder_color = theme.TEXT_MUTED
        self._badge = ""
        self._badge_color = theme.TEXT_DIM
        self._view_rect = None
        self._view_size = (0, 0)
        self._focused = False
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        self.btn_focus = QToolButton(self)
        self.btn_focus.setAutoRaise(True)
        self.btn_focus.setCursor(Qt.PointingHandCursor)
        self.btn_focus.setIconSize(QSize(18, 18))
        self.btn_focus.setFixedSize(30, 30)
        self.btn_focus.setToolTip("Expand view")
        self.btn_focus.setIcon(_fullscreen_icon(expand=True))
        self.btn_focus.setStyleSheet(
            "QToolButton{background:rgba(8,12,17,150);"
            "border:1px solid rgba(255,255,255,28);border-radius:6px;}"
            "QToolButton:hover{background:rgba(34,48,64,220);}"
        )
        self.btn_focus.clicked.connect(self.focusToggled.emit)

    def set_focus_state(self, focused):
        self._focused = bool(focused)
        self.btn_focus.setIcon(_fullscreen_icon(expand=not self._focused))
        self.btn_focus.setToolTip("Restore view" if self._focused else "Expand view")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.btn_focus.move(self.width() - self.btn_focus.width() - 12, 12)

    def set_frame(self, qimage):
        from PySide6.QtGui import QPixmap

        if qimage is None or qimage.isNull():
            self._pixmap = None
        else:
            self._pixmap = QPixmap.fromImage(qimage)
        self.update()

    def clear(self, text="NO SIGNAL", sub="", color=theme.TEXT_MUTED):
        self._pixmap = None
        self._placeholder_text = text
        self._placeholder_sub = sub
        self._placeholder_color = color
        self.update()

    def set_badge(self, text, color=theme.TEXT_DIM):
        self._badge = text
        self._badge_color = color
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            rect = self._view_rect
            if rect is not None and not rect.isEmpty():
                point = event.position().toPoint()
                if rect.contains(point):
                    iw, ih = self._view_size
                    if iw and ih:
                        ix = (point.x() - rect.x()) * iw / rect.width()
                        iy = (point.y() - rect.y()) * ih / rect.height()
                        self.picked.emit(float(ix), float(iy))
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.BG_DEEP))

        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            self._view_rect = QRect(x, y, scaled.width(), scaled.height())
            self._view_size = (self._pixmap.width(), self._pixmap.height())
            painter.setPen(QPen(QColor(theme.BORDER_SOFT), 1))
            painter.drawRect(x - 1, y - 1, scaled.width() + 1, scaled.height() + 1)
            painter.drawPixmap(x, y, scaled)
        else:
            self._view_rect = None
            self._paint_placeholder(painter)

        if self._badge:
            self._paint_badge(painter)
        painter.end()

    def _paint_placeholder(self, painter):
        painter.setPen(QPen(QColor(theme.BORDER_SOFT), 1, Qt.DashLine))
        inset = 10
        painter.drawRect(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset)

        painter.setPen(QColor(self._placeholder_color))
        font = painter.font()
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        text_rect = self.rect().adjusted(0, -20, 0, -20)
        painter.drawText(text_rect, Qt.AlignCenter, self._placeholder_text)

        if self._placeholder_sub:
            font.setPointSize(9)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                self.rect().adjusted(0, 34, 0, 34), Qt.AlignCenter, self._placeholder_sub
            )

    def _paint_badge(self, painter):
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(self._badge) + 18
        h = metrics.height() + 8
        rect = self.rect().adjusted(12, 12, -(self.width() - 12 - w), -(self.height() - 12 - h))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 165))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor(self._badge_color))
        painter.drawText(rect, Qt.AlignCenter, self._badge)


class StatusDot(QWidget):
    """Coloured indicator used for LIVE / NO SIGNAL style readouts."""

    def __init__(self, color=theme.TEXT_MUTED, size=9, parent=None):
        super().__init__(parent)
        self._color = color
        self._size = size
        self.setFixedSize(size + 2, size + 2)

    def set_color(self, color):
        self._color = color
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(1, 1, self._size, self._size)
        painter.end()


class Banner(QFrame):
    """Inline notice strip (info / warn / danger)."""

    def __init__(self, text="", kind="info", parent=None):
        super().__init__(parent)
        self.setProperty("role", "banner" if kind == "info" else f"banner_{kind}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(8)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:11px;")
        lay.addWidget(self.label, 1)
        self.setVisible(bool(text))

    def show_message(self, text, kind="info"):
        self.setProperty("role", "banner" if kind == "info" else f"banner_{kind}")
        self.label.setText(text)
        self.setVisible(bool(text))
        self.style().unpolish(self)
        self.style().polish(self)

    def hide_message(self):
        self.label.setText("")
        self.setVisible(False)


class Pill(QLabel):
    """Small rounded tag, e.g. source or status."""

    def __init__(self, text="", color=theme.TEXT_DIM, parent=None):
        super().__init__(text, parent)
        self._color = color
        self.setAlignment(Qt.AlignCenter)
        self._restyle()

    def set_state(self, text, color):
        self.setText(text)
        self._color = color
        self._restyle()

    def _restyle(self):
        self.setStyleSheet(
            f"color:{self._color}; background:{theme.rgba(self._color, 0.13)};"
            f"border:1px solid {theme.rgba(self._color, 0.35)};"
            "border-radius:9px; padding:2px 9px; font-size:10px; font-weight:700;"
        )


def qt_color(value):
    """Coerce a palette entry into a QColor.

    Theme colours are hex strings; ``Qt.GlobalColor`` only accepts the enum, so
    anything that colours a brush, pen or table item must go through here.
    """
    if isinstance(value, QColor):
        return value
    return QColor(value)


def hline(parent=None):
    line = QFrame(parent)
    line.setProperty("role", "sep")
    line.setFixedHeight(1)
    return line


def vline(parent=None):
    line = QFrame(parent)
    line.setProperty("role", "vsep")
    line.setFixedWidth(1)
    return line


def labeled_row(text, widget, width=None):
    """A 'Label [widget]' row used throughout the control rails.

    The label sits in a fixed-width right-aligned column (see ``LABEL_W``) so
    every field starts at the same x offset regardless of caption length.
    """
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    label = QLabel(text)
    label.setProperty("role", "dim")
    label.setFixedWidth(LABEL_W if width is None else width)
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lay.addWidget(label)
    lay.addWidget(widget, 1)
    return row