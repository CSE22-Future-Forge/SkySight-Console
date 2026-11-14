"""Coordinator console look: dark chrome around a light map, status colours shared with map.js."""

APP_NAME = "SAR Manifest"

BG = "#0f1722"
PANEL = "#162131"
PANEL_2 = "#1d2a3d"
BORDER = "#2a3a52"
TEXT = "#e6edf5"
TEXT_DIM = "#a9b7c9"
TEXT_MUTED = "#6d7f95"
ACCENT = "#3d8bfd"
OK = "#43a047"
WARN = "#fb8c00"
DANGER = "#e53935"

# Must match STATUS_COLOR in web/map.js.
STATUS_COLOR = {
    "open": "#e53935",
    "ongoing": "#fb8c00",
    "completed": "#43a047",
    "dismissed": "#9e9e9e",
    "pending": "#ab47bc",
}
STATUS_LABEL = {
    "pending": "PENDING",
    "open": "OPEN",
    "ongoing": "ONGOING",
    "completed": "RESCUED",
    "dismissed": "DISMISSED",
}
# Manifest sort order: what needs the coordinator first.
STATUS_PRIORITY = {"pending": 0, "open": 1, "ongoing": 2, "completed": 3, "dismissed": 4}
STALE_COLOR = "#9e9e9e"


def stylesheet() -> str:
    return f"""
    QWidget {{ background: {BG}; color: {TEXT}; font-family: "Segoe UI"; font-size: 10pt; }}
    QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
    QLabel[role="title"] {{ font-size: 15pt; font-weight: 600; letter-spacing: 1px; }}
    QLabel[role="h2"] {{ font-size: 9pt; font-weight: 600; color: {TEXT_DIM}; letter-spacing: 1px; }}
    QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
    QLabel[role="muted"] {{ color: {TEXT_MUTED}; }}
    QLabel[role="mono"] {{ font-family: Consolas; }}
    QLabel[role="code"] {{ font-size: 18pt; font-weight: 700; }}
    QFrame[role="card"] {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QFrame[role="card"] QLabel, QFrame[role="card"] QCheckBox {{ background: transparent; }}
    QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}
    QPushButton:hover {{ border-color: {ACCENT}; }}
    QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: {PANEL_2}; }}
    QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
    QPushButton[variant="danger"] {{ background: #5c1f22; border-color: {DANGER}; color: #ffd6d6; }}
    QPushButton[variant="ok"] {{ background: #1f4d25; border-color: {OK}; color: #dcf5de; }}
    QPushButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
        background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px; }}
    QComboBox QAbstractItemView {{ background: {PANEL_2}; selection-background-color: {ACCENT}; }}
    QTableView, QListWidget {{ background: {PANEL}; alternate-background-color: #192638; border: 1px solid {BORDER};
        gridline-color: {BORDER}; selection-background-color: #24456f; selection-color: {TEXT}; }}
    QHeaderView::section {{ background: {PANEL_2}; color: {TEXT_DIM}; border: 0; border-right: 1px solid {BORDER};
        padding: 5px; font-weight: 600; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; }}
    QTabBar::tab {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 6px 16px; margin-right: 2px;
        border-top-left-radius: 6px; border-top-right-radius: 6px; color: {TEXT_DIM}; }}
    QTabBar::tab:selected {{ background: {PANEL_2}; color: {TEXT}; }}
    QSplitter::handle {{ background: {BORDER}; }}
    QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
    QScrollArea {{ border: 0; }}
    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {TEXT_MUTED}; border-radius: 3px;
        background: {PANEL_2}; }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    """


def chip_style(color: str) -> str:
    """Rounded status chip: tinted background, coloured text."""
    return (f"background: {color}33; color: {color}; border: 1px solid {color}; border-radius: 9px; "
            f"padding: 1px 9px; font-weight: 700; font-size: 8.5pt;")
