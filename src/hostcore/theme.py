"""Light theme, colour tokens and SVG icon set shared by Studio and Factory."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

COLORS: dict[str, str] = {
    "bg": "#F5F5F7",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFAFC",
    "border": "#D2D2D7",
    "text": "#1D1D1F",
    "muted": "#6E6E73",
    "accent": "#0071E3",
    "accent_dark": "#0058B0",
    "idle": "#8E8E93",
    "load": "#5AC8FA",
    "running": "#0071E3",
    "action": "#FF9500",
    "pass": "#1B8A3A",
    "pass_bg": "#E7F7EC",
    "fail": "#D70015",
    "fail_bg": "#FDECEC",
    "warn": "#B25000",
    "warn_bg": "#FFF4E5",
    "error": "#6E6E73",
    "shadow": "rgba(0, 0, 0, 0.10)",
}

STATE_COLORS = {
    "IDLE": COLORS["idle"],
    "LOAD": COLORS["load"],
    "RUNNING": COLORS["running"],
    "ACTION": COLORS["action"],
    "PASS": COLORS["pass"],
    "FAIL": COLORS["fail"],
    "ERROR": COLORS["error"],
}

_ICONS: dict[str, str] = {
    "logo": '<path d="M4 7l8-4 8 4v10l-8 4-8-4z" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M12 3v18M4 7l8 4 8-4" fill="none" stroke="{c}" stroke-width="1.4"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" stroke="{c}" stroke-width="1.6"/>',
    "play": '<path d="M7 4l13 8-13 8z" fill="{c}"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2" fill="{c}"/>',
    "check": '<path d="M4 12l5 5L21 6" fill="none" stroke="{c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18" fill="none" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/>',
    "warning": '<path d="M12 3l10 18H2z" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M12 8v6M12 17.5v.5" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "board": '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/><circle cx="8" cy="9" r="1.5" fill="{c}"/><circle cx="16" cy="9" r="1.5" fill="{c}"/><path d="M8 15h8" stroke="{c}" stroke-width="1.6"/>',
    "printer": '<path d="M7 8V3h10v5" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="3" y="8" width="18" height="8" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="7" y="14" width="10" height="7" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "user": '<circle cx="12" cy="8" r="4" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "refresh": '<path d="M20 8a8 8 0 10.5 8" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M20 3v5h-5" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "settings": '<circle cx="12" cy="12" r="3" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" stroke="{c}" stroke-width="1.6"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M15.5 15.5L21 21" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "dashboard": '<rect x="3" y="3" width="8" height="8" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="13" y="3" width="8" height="5" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="13" y="10" width="8" height="11" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="3" y="13" width="8" height="8" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "history": '<path d="M3 12a9 9 0 109-9" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M3 4v8h8" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M12 7v5l3 2" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "label": '<path d="M3 7h13l5 5-5 5H3z" fill="none" stroke="{c}" stroke-width="1.8"/><circle cx="8" cy="12" r="1.5" fill="{c}"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M8 10V7a4 4 0 018 0v3" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "bolt": '<path d="M13 2L4 14h6l-1 8 9-12h-6z" fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>',
    "eye": '<path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z" fill="none" stroke="{c}" stroke-width="1.8"/><circle cx="12" cy="12" r="2.5" fill="none" stroke="{c}" stroke-width="1.8"/>',
}


def icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    color = color or COLORS["text"]
    body = _ICONS.get(name, _ICONS["chip"]).format(c=color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def state_icon(state: str, size: int = 24) -> QIcon:
    mapping = {
        "IDLE": "chip",
        "LOAD": "board",
        "RUNNING": "refresh",
        "ACTION": "warning",
        "PASS": "check",
        "FAIL": "x",
        "ERROR": "warning",
    }
    return icon(mapping.get(state, "chip"), STATE_COLORS.get(state, COLORS["text"]), size)


def apply_theme(app: object) -> None:
    """Apply the shared light stylesheet to a QApplication."""
    if not hasattr(app, "setStyleSheet"):
        return
    c = COLORS
    app.setStyleSheet(  # type: ignore[attr-defined]
        f"""
        * {{
            font-family: -apple-system, "SF Pro Text", "Segoe UI", "Ubuntu", sans-serif;
            font-size: 18px;
            color: {c["text"]};
        }}
        QMainWindow, QWidget#Root {{ background: {c["bg"]}; }}
        QFrame#Card {{
            background: {c["surface"]};
            border: 1px solid {c["border"]};
            border-radius: 14px;
        }}
        QFrame#Header {{
            background: {c["surface"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QLabel#Title {{ font-size: 26px; font-weight: 700; }}
        QLabel#Subtitle {{ color: {c["muted"]}; font-size: 15px; }}
        QLabel#Heading {{ font-size: 20px; font-weight: 700; }}
        QLabel#Metric {{ font-size: 30px; font-weight: 700; }}
        QLabel#Muted {{ color: {c["muted"]}; }}
        QPushButton {{
            background: {c["surface"]};
            border: 1px solid {c["border"]};
            border-radius: 9px;
            padding: 8px 15px;
            min-height: 26px;
        }}
        QPushButton:hover {{ background: {c["surface_alt"]}; }}
        QPushButton:pressed {{ background: #E8E8ED; }}
        QPushButton:disabled {{ color: #B0B0B5; background: #F7F7F9; }}
        QPushButton#Primary {{
            background: {c["accent"]};
            color: white;
            border: 1px solid {c["accent"]};
            font-weight: 600;
        }}
        QPushButton#Primary:hover {{ background: {c["accent_dark"]}; }}
        QPushButton#Danger {{
            background: {c["fail"]};
            color: white;
            border: 1px solid {c["fail"]};
            font-weight: 600;
        }}
        QPushButton#Success {{
            background: {c["pass"]};
            color: white;
            border: 1px solid {c["pass"]};
            font-weight: 600;
        }}
        QLineEdit, QComboBox, QSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
            background: {c["surface"]};
            border: 1px solid {c["border"]};
            border-radius: 8px;
            padding: 7px 10px;
            selection-background-color: {c["accent"]};
        }}
        QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border: 2px solid {c["accent"]};
        }}
        QTabWidget::pane {{
            border: 1px solid {c["border"]};
            border-radius: 12px;
            background: {c["surface"]};
        }}
        QTabBar::tab {{
            background: transparent;
            padding: 10px 18px;
            margin-right: 2px;
            color: {c["muted"]};
        }}
        QTabBar::tab:selected {{
            color: {c["text"]};
            font-weight: 600;
            border-bottom: 3px solid {c["accent"]};
        }}
        QTableWidget, QTreeWidget {{
            background: {c["surface"]};
            border: 1px solid {c["border"]};
            border-radius: 10px;
            gridline-color: #EDEDF0;
            alternate-background-color: {c["surface_alt"]};
        }}
        QHeaderView::section {{
            background: {c["surface_alt"]};
            border: none;
            border-bottom: 1px solid {c["border"]};
            padding: 7px;
            font-weight: 600;
            color: {c["muted"]};
        }}
        QProgressBar {{
            background: #E8E8ED;
            border: none;
            border-radius: 5px;
            height: 10px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background: {c["accent"]};
            border-radius: 5px;
        }}
        QScrollArea {{ border: none; background: transparent; }}
        QSplitter::handle {{ background: {c["border"]}; }}
        QMenuBar {{ background: {c["surface"]}; border-bottom: 1px solid {c["border"]}; }}
        QMenuBar::item {{ padding: 7px 12px; background: transparent; }}
        QMenuBar::item:selected {{ background: #EDEDF0; border-radius: 5px; }}
        QMenu {{ background: {c["surface"]}; border: 1px solid {c["border"]}; padding: 5px; }}
        QMenu::item {{ padding: 7px 24px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {c["accent"]}; color: white; }}
        QStatusBar {{ background: {c["surface"]}; border-top: 1px solid {c["border"]}; }}
        QToolTip {{
            background: {c["text"]};
            color: white;
            border: none;
            padding: 5px;
        }}
        """
    )


def color_for_state(state: str) -> QColor:
    return QColor(STATE_COLORS.get(state, COLORS["text"]))
