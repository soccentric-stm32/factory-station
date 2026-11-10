"""Bench colour names and SVG icon set; colours and style sheet are ``soc_theme``."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from hostcore import soc_theme


def _palette(t: soc_theme.Tokens) -> dict[str, str]:
    """The bench's colour names, filled from the shared SoCcentric tokens."""
    return {
        "bg": t.window, "surface": t.surface, "surface_alt": t.surface_alt,
        "border": t.border, "text": t.text, "muted": t.text_secondary,
        "accent": t.accent, "accent_dark": t.accent_pressed,
        "idle": t.text_tertiary, "load": t.accent_hover, "running": t.accent,
        "action": t.warning, "pass": t.success, "pass_bg": t.success_soft,
        "fail": t.danger, "fail_bg": t.danger_soft, "warn": t.warning,
        "warn_bg": t.warning_soft, "error": t.text_secondary,
        "sidebar": t.sidebar, "sidebar_border": t.hairline,
        "sidebar_hover": t.surface_alt, "sidebar_selected": t.accent_soft,
        "sidebar_selected_text": t.accent, "shadow": "rgba(0, 0, 0, 0.10)",
    }


# Mutated in place by apply_theme() so every module sees the active scheme.
COLORS: dict[str, str] = _palette(soc_theme.LIGHT)

_STATE_KEYS = {
    "IDLE": "idle", "LOAD": "load", "RUNNING": "running", "ACTION": "action",
    "PASS": "pass", "FAIL": "fail", "ERROR": "error",
}
STATE_COLORS: dict[str, str] = {k: COLORS[v] for k, v in _STATE_KEYS.items()}

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
    "chart": '<rect x="3" y="12" width="4" height="8" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="10" y="7" width="4" height="13" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/><rect x="17" y="3" width="4" height="17" rx="1" fill="none" stroke="{c}" stroke-width="1.8"/>',
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
    """Apply the shared SoCcentric theme to a QApplication."""
    if not hasattr(app, "setStyleSheet"):
        return
    tokens = soc_theme.apply(app)
    COLORS.update(_palette(tokens))
    STATE_COLORS.update({k: COLORS[v] for k, v in _STATE_KEYS.items()})


def color_for_state(state: str) -> QColor:
    return QColor(STATE_COLORS.get(state, COLORS["text"]))
