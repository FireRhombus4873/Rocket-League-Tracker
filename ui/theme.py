"""
Design tokens + the main-window stylesheet — a calm, modern dark theme.

Depth comes from low-contrast elevation (surfaces + soft shadows) rather than
hard 1px borders, and colour is used sparingly as an accent. Everything that
needs a colour, font, or the app-wide stylesheet imports it from here so the
palette lives in exactly one place.
"""

# Surfaces
BG_DARK   = "#0e1015"   # app background (soft near-black)
BG_CARD   = "#171b23"   # elevated card surface
BG_TABLE  = "#12151c"   # recessed / inset surface (tables, wells)
BG_ALT    = "#1b202a"   # subtle alternating fill / row stripe
BG_HOVER  = "#232a35"   # hover state

# Text
TEXT      = "#e8ebf1"
SUBTEXT   = "#8a92a2"
FAINT     = "#5b6373"   # tertiary / captions

# Accents (used sparingly)
ACCENT    = "#f2555f"   # Rocket League red — brand cue
ACCENT2   = "#4f9dea"   # calm blue
WIN_CLR   = "#41c46b"
LOSS_CLR  = "#ef5f6b"

# Lines
BORDER      = "#242a35"  # subtle hairline
BORDER_SOFT = "#1c222c"  # barely-there separators
SELECT_BG   = "#1e3a5f"  # row selection

# Type — clean system sans, no monospace
FONT_UI   = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
FONT_HEAD = '"Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", sans-serif'


# Applied to the QMainWindow; child dialogs set their own scoped sheets.
APP_STYLESHEET = f"""
    QMainWindow, QWidget {{
        background-color: {BG_DARK};
        color: {TEXT};
        font-family: {FONT_UI};
        font-size: 13px;
    }}
    QTableWidget {{
        background-color: transparent;
        border: none;
        gridline-color: transparent;
        color: {TEXT};
        outline: none;
    }}
    QTableWidget::item {{ padding: 9px 12px; border: none; }}
    QTableWidget::item:selected {{
        background-color: {SELECT_BG};
    }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{
        background-color: transparent;
        color: {FAINT};
        border: none;
        border-bottom: 1px solid {BORDER_SOFT};
        padding: 8px 12px;
        font-size: 10px;
        font-weight: bold;
        letter-spacing: 0.5px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px 0 2px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 4px;
        min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 0 2px 0 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER};
        border-radius: 4px;
        min-width: 32px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {FAINT}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0; height: 0; background: transparent; border: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QLabel {{ background: transparent; }}
    QToolTip {{
        background-color: {BG_CARD};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 8px;
    }}
    QStatusBar {{ color: {FAINT}; font-size: 11px; }}
    QStatusBar::item {{ border: none; }}
    QTabWidget::pane {{
        border: none;
        border-top: 1px solid {BORDER_SOFT};
        top: -1px;
        background: transparent;
    }}
    QTabBar {{ qproperty-drawBase: 0; background: transparent; }}
    QTabBar::tab {{
        background: transparent;
        color: {SUBTEXT};
        padding: 11px 22px;
        margin-right: 4px;
        border: none;
        border-bottom: 2px solid transparent;
        font-size: 12px;
        font-weight: bold;
        letter-spacing: 1.5px;
    }}
    QTabBar::tab:selected {{
        color: {TEXT};
        border-bottom: 2px solid {ACCENT};
    }}
    QTabBar::tab:hover:!selected {{ color: {TEXT}; }}
"""
