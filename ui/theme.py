"""Central visual language for the SkySight console.

All colours live here so widgets, overlays and the stylesheet stay consistent.
The palette is a low-glare dark scheme (operators fly at night) with three
semantic accents: RGB = sky blue, THERMAL = ember orange, FUSION = violet.
"""

from . import icons as icons_mod

APP_NAME = "SKYSIGHT CONSOLE"
APP_SUBTITLE = "Human Search & Rescue — UAV"

# ---------------------------------------------------------------- palette ---
BG = "#0b0f14"
BG_DEEP = "#070a0e"
PANEL = "#121a22"
PANEL_ALT = "#0f161d"
ELEV = "#1a232e"
ELEV_HI = "#223040"
BORDER = "#24313f"
BORDER_SOFT = "#1b2531"

TEXT = "#e6edf3"
TEXT_DIM = "#93a3b8"
TEXT_MUTED = "#61748a"

RGB_C = "#38bdf8"
THERMAL_C = "#fb923c"
FUSION_C = "#a78bfa"

OK = "#34d399"
WARN = "#fbbf24"
DANGER = "#f43f5e"

# Distinct hues for track IDs, cycled in order.
TRACK_COLORS = [
    (56, 189, 248),
    (251, 146, 60),
    (167, 139, 250),
    (52, 211, 153),
    (244, 114, 182),
    (250, 204, 21),
    (96, 165, 250),
    (45, 212, 191),
]


def track_color(track_id):
    return TRACK_COLORS[track_id % len(TRACK_COLORS)]

def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def stylesheet():
    icons = icons_mod.ensure_all()
    return f"""
* {{
    outline: none;
}}

QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background: {BG_DEEP};
}}

QToolTip {{
    background: {ELEV};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 5px 8px;
    border-radius: 4px;
}}

/* ------------------------------------------------------------- typography */
QLabel[role="title"] {{
    font-size: 17px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel[role="subtitle"] {{
    font-size: 11px;
    color: {TEXT_MUTED};
    letter-spacing: 1px;
}}
QLabel[role="section"] {{
    font-size: 10px;
    font-weight: 700;
    color: {TEXT_MUTED};
    letter-spacing: 1.4px;
}}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="mono"] {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    color: {TEXT_DIM};
}}
QLabel[role="ok"] {{ color: {OK}; font-weight: 600; }}
QLabel[role="warn"] {{ color: {WARN}; font-weight: 600; }}
QLabel[role="danger"] {{ color: {DANGER}; font-weight: 600; }}

/* ---------------------------------------------------------------- surfaces */
QFrame[role="card"] {{
    background: {PANEL};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
}}
QFrame[role="rail"] {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
}}
QFrame[role="sep"] {{
    background: {BORDER_SOFT};
    max-height: 1px;
    border: none;
}}
QFrame[role="vsep"] {{
    background: {BORDER_SOFT};
    max-width: 1px;
    border: none;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    margin-top: 16px;
    padding: 12px 10px 10px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: {TEXT_MUTED};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
}}

/* ------------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    background: {BG};
    top: -1px;
}}
QTabBar {{
    qproperty-drawBase: 0;
    background: transparent;
}}
QTabBar::tab {{
    background: {PANEL_ALT};
    color: {TEXT_MUTED};
    border: 1px solid {BORDER_SOFT};
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 9px 22px;
    margin-right: 4px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.1px;
}}
QTabBar::tab:hover {{
    background: {ELEV};
    color: {TEXT_DIM};
}}
QTabBar::tab:selected {{
    background: {PANEL};
    color: {TEXT};
    border-top: 2px solid {FUSION_C};
}}
QTabBar::tab:disabled {{
    color: #3c4a58;
}}

/* --------------------------------------------------------------- controls */
QPushButton {{
    background: {ELEV};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 15px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.6px;
}}
QPushButton:hover {{
    background: {ELEV_HI};
    border-color: #33455a;
}}
QPushButton:pressed {{ background: #16202b; }}
QPushButton:disabled {{
    background: #141b23;
    color: #46586b;
    border-color: {BORDER_SOFT};
}}

QPushButton[variant="primary"] {{
    background: {FUSION_C};
    color: #12091f;
    border: none;
}}
QPushButton[variant="primary"]:hover {{ background: #b9a2fb; }}
QPushButton[variant="primary"]:pressed {{ background: #9179e6; }}
QPushButton[variant="primary"]:disabled {{
    background: #2b2540;
    color: #5f5580;
}}

QPushButton[variant="live"] {{
    background: {ELEV};
    color: {DANGER};
    border: 1px solid #4a2333;
}}
QPushButton[variant="live"]:hover {{ background: #2a1620; }}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_DIM};
}}
QPushButton[variant="ghost"]:hover {{
    background: {ELEV};
    color: {TEXT};
}}

QPushButton[variant="danger"] {{
    background: transparent;
    border: 1px solid #4a2333;
    color: {DANGER};
}}
QPushButton[variant="danger"]:hover {{ background: #2a1620; }}

QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {TEXT_DIM};
    background: transparent;
}}
QCheckBox:hover, QRadioButton:hover {{ color: {TEXT}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid #35485c;
    border-radius: 3px;
    background: {ELEV};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {FUSION_C};
}}
QCheckBox::indicator:checked {{
    background: {FUSION_C};
    border-color: {FUSION_C};
    image: {icons["check"]};
}}
QRadioButton::indicator:checked {{
    background: {ELEV};
    border-color: {FUSION_C};
    image: {icons["radio"]};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: #141b23;
    border-color: #253140;
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: #46586b; }}

QComboBox {{
    background: {ELEV};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT};
    min-height: 18px;
}}
QComboBox:hover {{ border-color: #33455a; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    image: {icons["combo_down"]};
    width: 10px;
    height: 6px;
    border: none;
    margin-right: 8px;
}}
QComboBox::down-arrow:hover {{ image: {icons["combo_down_hover"]}; }}
QComboBox QAbstractItemView {{
    background: {ELEV};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ELEV_HI};
    selection-color: {TEXT};
    outline: none;
    padding: 4px;
}}

QSpinBox, QDoubleSpinBox {{
    background: {ELEV};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
    color: {TEXT};
    min-height: 18px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #33455a; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {FUSION_C}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: {ELEV_HI};
    border: none;
    width: 16px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {FUSION_C};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: {icons["arrow_up"]};
    width: 9px;
    height: 6px;
    border: none;
}}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{
    image: {icons["arrow_up_hover"]};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: {icons["arrow_down"]};
    width: 9px;
    height: 6px;
    border: none;
}}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{
    image: {icons["arrow_down_hover"]};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: #141b23;
    color: #46586b;
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {FUSION_C};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    border: 2px solid {FUSION_C};
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::groove:horizontal:disabled {{ background: #1c242e; }}
QSlider::sub-page:horizontal:disabled {{ background: #3a3550; }}
QSlider::handle:horizontal:disabled {{
    background: #5f6b7a;
    border-color: #3a3550;
}}

/* ------------------------------------------------------------------ table */
QTableWidget, QTableView {{
    background: {PANEL};
    alternate-background-color: {PANEL_ALT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    gridline-color: {BORDER_SOFT};
    selection-background-color: {ELEV_HI};
    selection-color: {TEXT};
    font-size: 12px;
}}
QTableWidget::item, QTableView::item {{ padding: 5px 7px; }}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {ELEV_HI};
    color: {TEXT};
}}
QHeaderView::section {{
    background: {PANEL_ALT};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER_SOFT};
    padding: 7px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{
    background: {PANEL_ALT};
    border: none;
    border-bottom: 1px solid {BORDER};
}}

/* ------------------------------------------------------------------- list */
QListWidget {{
    background: {PANEL};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    outline: none;
}}
QListWidget::item {{
    border-radius: 6px;
    margin: 4px;
    padding: 4px;
}}
QListWidget::item:hover {{ background: {ELEV}; }}
QListWidget::item:selected {{ background: {ELEV_HI}; }}

/* --------------------------------------------------------------- scrollbar */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #33455a; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: #33455a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* -------------------------------------------------------------- statusbar */
QStatusBar {{
    background: {PANEL_ALT};
    border-top: 1px solid {BORDER_SOFT};
    color: {TEXT_DIM};
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{
    background: transparent;
    color: {TEXT_MUTED};
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    padding: 0 2px;
}}

/* ----------------------------------------------------------------- banner */
QFrame[role="banner"] {{
    background: #1d2430;
    border: 1px solid {BORDER};
    border-left: 3px solid {FUSION_C};
    border-radius: 6px;
}}
QFrame[role="banner_warn"] {{
    background: #241d0f;
    border: 1px solid #4a3a17;
    border-left: 3px solid {WARN};
    border-radius: 6px;
}}
QFrame[role="banner_danger"] {{
    background: #26131b;
    border: 1px solid #4a2333;
    border-left: 3px solid {DANGER};
    border-radius: 6px;
}}

QProgressBar {{
    background: {ELEV};
    border: 1px solid {BORDER_SOFT};
    border-radius: 5px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {FUSION_C};
    border-radius: 4px;
}}

QMenu {{
    background: {ELEV};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ELEV_HI}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 4px 8px; }}

QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""