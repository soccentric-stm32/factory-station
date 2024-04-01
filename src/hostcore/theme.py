"""Bench colour names and icons; colours and style sheet are ``soc_theme``,
glyphs are ``soc_icons``."""

from __future__ import annotations

from PySide6.QtGui import QColor, QIcon

from hostcore import soc_icons, soc_theme


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

def icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    """A glyph of the shared SoCcentric set (``soc_icons``), tinted ``color``."""
    return soc_icons.icon(name, color or COLORS["text"], size)


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
