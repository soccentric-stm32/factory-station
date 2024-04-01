"""SoCcentric desktop GUI theme: tokens, fonts and the one style sheet.

This file is the design spec for every SoCcentric PySide6 app (BOOT Flasher,
Factory Data, HWVAL Factory, HWVAL Studio).  Each app is its own product and
carries its own copy of the shared UI files next to each other:

  soc_theme.py     tokens, fonts, the style sheet, light/dark and zoom
  soc_icons.py     the one icon set and the app badge
  soc_widgets.py   the app shell: sidebar header, nav item, page header,
                   cards, pills, the output panel, dialogs
  soc_terminal.py  the debug UART panel
  soc_fonts/       Inter and JetBrains Mono (SIL Open Font Licence)

The copies are byte-identical; edit one, then copy it over the others
(``scripts/check-theme-sync.sh`` in the workspace repo checks them).

Light and dark follow the system colour scheme while the app runs
(:func:`apply` with ``follow=True``).  Every rule is scoped by object name
so nothing leaks onto child widgets (QLabel is a QFrame).

Type scale (px): 22 page title, 15 app title, 13 body / controls / card
title, 12 caption / pill / mono, 11 table header / sidebar section.
Spacing grid 4/8/12/16/24.  Radii: 6 inputs, 7 buttons, 10 cards, 10 pills.

``set_scale()`` multiplies the type scale (View > Zoom, or a notch smaller
on a 1.25 desktop); the layout grid stays put.

Object names, one vocabulary for every app:

  labels   AppTitle AppSubtitle SidebarSection PageTitle PageSubtitle
           CardTitle RowLabel Key Value Hint Caption Mono StatLabel StatValue
           Pill[tone] Banner[tone] Hint[tone]
           Hero[tone] Headline Lead (large type for operator / kiosk screens)
  buttons  Primary (40 px filled) Accent (32 px filled) Secondary (40 px)
           Danger Link IconButton Flat LargeAction Large
  frames   Root Sidebar Content Page PageScroll PageScrollBody Card
           SubtleCard Hairline DeviceBox
  other    Console Progress SearchField SourceList, QCheckBox#Switch,
           QFrame#Pill[tone] (icon + text pill), QComboBox#Large

Tones are "", accent, success, warning and danger.
"""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOptionHeader


@dataclass(frozen=True)
class Tokens:
    dark: bool
    window: str
    sidebar: str
    surface: str
    surface_alt: str
    border: str
    hairline: str
    text: str
    text_secondary: str
    text_tertiary: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    on_accent: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    hover: str
    pressed: str
    selection: str
    console: str
    console_text: str


LIGHT = Tokens(
    dark=False,
    window="#f5f5f7", sidebar="#ececef", surface="#ffffff",
    surface_alt="#f9f9fb", border="#d1d1d6", hairline="#e5e5ea",
    text="#1d1d1f", text_secondary="#6e6e73", text_tertiary="#8e8e93",
    accent="#007aff", accent_hover="#0071e3", accent_pressed="#0062cc",
    accent_soft="#e5f1ff", on_accent="#ffffff",
    success="#248a3d", success_soft="#e3f6e8",
    warning="#b25000", warning_soft="#fff2e0",
    danger="#d70015", danger_soft="#ffe9e8",
    hover="rgba(0, 0, 0, 0.05)", pressed="rgba(0, 0, 0, 0.10)",
    selection="#b3d7ff", console="#fbfbfd", console_text="#1d1d1f",
)

DARK = Tokens(
    dark=True,
    window="#1e1e20", sidebar="#28282b", surface="#2c2c2e",
    surface_alt="#323235", border="#48484a", hairline="#3a3a3c",
    text="#f5f5f7", text_secondary="#a1a1a6", text_tertiary="#7c7c80",
    accent="#0a84ff", accent_hover="#409cff", accent_pressed="#0070e0",
    accent_soft="#0a2a4d", on_accent="#ffffff",
    success="#30d158", success_soft="#11331c",
    warning="#ff9f0a", warning_soft="#3d2a0a",
    danger="#ff453a", danger_soft="#401714",
    hover="rgba(255, 255, 255, 0.06)", pressed="rgba(255, 255, 255, 0.12)",
    selection="#0a4a8f", console="#1a1a1c", console_text="#e5e5ea",
)

_current = LIGHT
_scale = 1.0
_forced_dark: bool | None = None
_listeners: list[Callable[[Tokens], None]] = []
_following: set[int] = set()


def current() -> Tokens:
    return _current


def system_is_dark() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def set_dark(dark: bool) -> Tokens:
    global _current
    _current = DARK if dark else LIGHT
    return _current


def color(name: str) -> QColor:
    return QColor(getattr(_current, name))


def set_scale(scale: float) -> float:
    """Multiply every type size; 1.0 keeps the design scale."""
    global _scale
    _scale = max(0.5, min(2.0, scale))
    return _scale


def scale() -> float:
    return _scale


def px(value: float) -> int:
    """A type-scale pixel value at the current UI scale."""
    return max(8, round(value * _scale))


def on_change(callback: Callable[[Tokens], None]) -> None:
    """Call ``callback(tokens)`` after every :func:`apply` (theme or zoom)."""
    if callback not in _listeners:
        _listeners.append(callback)


# ---- fonts --------------------------------------------------------------------

_FONT_DIR = Path(__file__).resolve().with_name("soc_fonts")
_fonts_loaded = False


def load_fonts() -> None:
    """Register the bundled Inter and JetBrains Mono once per process."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    if not _FONT_DIR.is_dir():
        return
    for path in sorted(_FONT_DIR.iterdir()):
        if path.suffix.lower() in (".ttf", ".otf"):
            QFontDatabase.addApplicationFontFromData(QByteArray(path.read_bytes()))


def _family(candidates: list[str], fallback: str) -> str:
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    return fallback


def ui_font(size: float = 13) -> QFont:
    family = _family(
        ["Inter", "SF Pro Text", "Helvetica Neue", "Segoe UI", "Ubuntu",
         "Cantarell", "Noto Sans", "DejaVu Sans"], "Sans Serif")
    font = QFont(family)
    font.setPixelSize(px(size))
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def mono_font(size: float = 12) -> QFont:
    family = _family(
        ["JetBrains Mono", "SF Mono", "Menlo", "Cascadia Mono", "Consolas",
         "Ubuntu Mono", "DejaVu Sans Mono"], "Monospace")
    font = QFont(family)
    font.setPixelSize(px(size))
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# ---- generated assets (QSS url() needs files) -----------------------------

_ASSET_DIR = Path(tempfile.mkdtemp(prefix="soc-theme-"))
atexit.register(shutil.rmtree, _ASSET_DIR, ignore_errors=True)

_ARROWS = {
    "chevron-down": "M6 9l6 6 6-6",
    "chevron-up": "M6 15l6-6 6 6",
    "check": "M5 12.5l4.5 4.5L19 7.5",
}


def _switch_asset(on: bool, track: str, knob: str) -> str:
    """A switch drawn like soc_widgets.Switch, for QCheckBox#Switch."""
    path = _ASSET_DIR / f"switch-{int(on)}-{track.lstrip('#')}-{knob.lstrip('#')}.svg"
    if not path.exists():
        cx = 29 if on else 11
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="22" viewBox="0 0 40 22">'
            f'<rect x="0" y="0" width="40" height="22" rx="11" fill="{track}"/>'
            f'<circle cx="{cx}" cy="11" r="9" fill="{knob}"/></svg>', encoding="utf-8")
    return path.as_posix()


def _asset(name: str, colour: str) -> str:
    path = _ASSET_DIR / f"{name}-{colour.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            f'viewBox="0 0 24 24"><path d="{_ARROWS[name]}" fill="none" '
            f'stroke="{colour}" stroke-width="2.4" stroke-linecap="round" '
            'stroke-linejoin="round"/></svg>', encoding="utf-8")
    return path.as_posix()


def palette(t: Tokens) -> QPalette:
    """Qt palette matching the tokens, for widgets the style sheet skips."""
    p = QPalette()
    for role, value in (
            (QPalette.ColorRole.Window, t.window), (QPalette.ColorRole.WindowText, t.text),
            (QPalette.ColorRole.Base, t.surface), (QPalette.ColorRole.AlternateBase, t.surface_alt),
            (QPalette.ColorRole.Text, t.text), (QPalette.ColorRole.Button, t.surface),
            (QPalette.ColorRole.ButtonText, t.text), (QPalette.ColorRole.ToolTipBase, t.surface),
            (QPalette.ColorRole.ToolTipText, t.text), (QPalette.ColorRole.Link, t.accent),
            (QPalette.ColorRole.Highlight, t.accent),
            (QPalette.ColorRole.HighlightedText, t.on_accent),
            (QPalette.ColorRole.PlaceholderText, t.text_tertiary),
            (QPalette.ColorRole.Mid, t.border), (QPalette.ColorRole.Midlight, t.hairline)):
        p.setColor(role, QColor(value))
    disabled = QColor(t.text_tertiary)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return p


class _Style(QProxyStyle):
    """Fusion with the few rules a style sheet cannot express."""

    def drawControl(self, element, option, painter, widget=None) -> None:
        # Table and tree headers read left-aligned in every app.
        if (element == QStyle.ControlElement.CE_HeaderLabel
                and isinstance(option, QStyleOptionHeader)
                and option.orientation == Qt.Orientation.Horizontal
                and option.textAlignment & Qt.AlignmentFlag.AlignHCenter):
            option = QStyleOptionHeader(option)
            option.textAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        super().drawControl(element, option, painter, widget)

    def styleHint(self, hint, option=None, widget=None, data=None) -> int:
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        if hint == QStyle.StyleHint.SH_ItemView_ShowDecorationSelected:
            return 0  # selection covers the row, not the indent / branch area
        return super().styleHint(hint, option, widget, data)


def apply(app, dark: bool | None = None, *, follow: bool = False) -> Tokens:
    """Style, palette, font and style sheet for the whole app.

    ``dark=None`` follows the system scheme; ``follow=True`` also re-applies
    the theme whenever the system switches between light and dark.  Every
    :func:`on_change` listener runs afterwards so painted widgets and icons
    can refresh.
    """
    global _forced_dark
    if dark is not None:
        _forced_dark = dark
    load_fonts()
    t = set_dark(system_is_dark() if _forced_dark is None else _forced_dark)
    if not isinstance(app.style(), _Style):
        app.setStyle(_Style("Fusion"))
    app.setPalette(palette(t))
    app.setFont(ui_font())
    app.setStyleSheet(stylesheet(t))
    if follow and id(app) not in _following:
        _following.add(id(app))
        app.styleHints().colorSchemeChanged.connect(lambda _scheme: apply(app))
    for callback in list(_listeners):
        try:
            callback(t)
        except RuntimeError:  # its widget is gone (C++ object deleted)
            _listeners.remove(callback)
    return t


def follow_system(app) -> None:
    """Drop a forced light/dark choice and follow the system again."""
    global _forced_dark
    _forced_dark = None
    apply(app, follow=True)


_FONT_PX = re.compile(r"font-size: (\d+(?:\.\d+)?)px")


def _scaled_type(qss: str) -> str:
    """Apply the UI scale to the type sizes in the style sheet."""
    if _scale == 1.0:
        return qss
    return _FONT_PX.sub(lambda m: f"font-size: {px(float(m.group(1)))}px", qss)


def stylesheet(t: Tokens) -> str:
    qss = f"""
QMainWindow, QWidget#Root {{ background: {t.window}; color: {t.text}; }}
QWidget#Content, QWidget#Page, QScrollArea#PageScroll, QWidget#PageScrollBody,
QStackedWidget#Pages {{ background: {t.window}; border: none; }}
QWidget#Sidebar, QFrame#Sidebar {{ background: {t.sidebar}; border: none;
    border-right: 1px solid {t.hairline}; }}
QLabel {{ background: transparent; color: {t.text}; }}

QLabel#AppTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#AppSubtitle, QLabel#Caption {{ font-size: 12px; color: {t.text_secondary}; }}
QLabel#SidebarSection {{ font-size: 11px; font-weight: 600; color: {t.text_tertiary}; }}
QLabel#PageTitle {{ font-size: 22px; font-weight: 600; }}
QLabel#PageSubtitle {{ font-size: 13px; color: {t.text_secondary}; }}
QLabel#CardTitle {{ font-size: 13px; font-weight: 600; }}
QLabel#RowLabel {{ font-size: 13px; color: {t.text}; }}
QLabel#Value {{ font-size: 13px; color: {t.text}; }}
QLabel#Value[tone="success"] {{ color: {t.success}; }}
QLabel#Value[tone="warning"] {{ color: {t.warning}; }}
QLabel#Value[tone="danger"] {{ color: {t.danger}; }}
QLabel#Key {{ font-size: 13px; color: {t.text_secondary}; }}
QLabel#Hint {{ font-size: 12px; color: {t.text_secondary}; }}
QLabel#Hint[tone="accent"] {{ color: {t.accent}; }}
QLabel#Hint[tone="warning"] {{ color: {t.warning}; }}
QLabel#Hint[tone="danger"] {{ color: {t.danger}; }}
QLabel#Hint[tone="success"] {{ color: {t.success}; }}
QLabel#Mono {{ font-size: 12px; color: {t.text_secondary}; }}
QLabel#StatLabel {{ font-size: 11px; font-weight: 600; color: {t.text_tertiary}; }}
QLabel#StatValue {{ font-size: 22px; font-weight: 600; color: {t.text}; }}
QLabel#StatValue[tone="accent"] {{ color: {t.accent}; }}
QLabel#StatValue[tone="success"] {{ color: {t.success}; }}
QLabel#StatValue[tone="warning"] {{ color: {t.warning}; }}
QLabel#StatValue[tone="danger"] {{ color: {t.danger}; }}

QLabel#Hero {{ font-size: 54px; font-weight: 800; color: {t.text}; }}
QLabel#Hero[tone="success"] {{ color: {t.success}; }}
QLabel#Hero[tone="warning"] {{ color: {t.warning}; }}
QLabel#Hero[tone="danger"] {{ color: {t.danger}; }}
QLabel#Headline {{ font-size: 28px; font-weight: 700; color: {t.text}; }}
QLabel#Lead {{ font-size: 17px; color: {t.text}; }}
QLabel#Lead[tone="secondary"] {{ color: {t.text_secondary}; }}
QLabel#Lead[tone="danger"] {{ color: {t.danger}; }}

QFrame#Pill {{ border-radius: 10px; background: {t.hover}; border: none; }}
QFrame#Pill QLabel {{ font-size: 12px; font-weight: 600; color: {t.text_secondary}; }}
QFrame#Pill[tone="accent"] {{ background: {t.accent_soft}; }}
QFrame#Pill[tone="accent"] QLabel {{ color: {t.accent}; }}
QFrame#Pill[tone="success"] {{ background: {t.success_soft}; }}
QFrame#Pill[tone="success"] QLabel {{ color: {t.success}; }}
QFrame#Pill[tone="warning"] {{ background: {t.warning_soft}; }}
QFrame#Pill[tone="warning"] QLabel {{ color: {t.warning}; }}
QFrame#Pill[tone="danger"] {{ background: {t.danger_soft}; }}
QFrame#Pill[tone="danger"] QLabel {{ color: {t.danger}; }}

QLabel#Pill {{ border-radius: 10px; padding: 3px 10px; font-size: 12px;
              font-weight: 600; background: {t.hover}; color: {t.text_secondary}; }}
QLabel#Pill[tone="accent"] {{ background: {t.accent_soft}; color: {t.accent}; }}
QLabel#Pill[tone="success"] {{ background: {t.success_soft}; color: {t.success}; }}
QLabel#Pill[tone="warning"] {{ background: {t.warning_soft}; color: {t.warning}; }}
QLabel#Pill[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger}; }}

QLabel#Banner {{ border-radius: 8px; padding: 8px 12px; font-size: 13px;
    background: {t.surface}; border: 1px solid {t.hairline}; color: {t.text}; }}
QLabel#Banner[tone="accent"] {{ background: {t.accent_soft}; border-color: {t.accent}; }}
QLabel#Banner[tone="success"] {{ background: {t.success_soft}; border-color: {t.success}; }}
QLabel#Banner[tone="warning"] {{ background: {t.warning_soft}; border-color: {t.warning}; }}
QLabel#Banner[tone="danger"] {{ background: {t.danger_soft}; border-color: {t.danger}; }}

QFrame#Card, QFrame#DeviceBox {{ background: {t.surface}; border: 1px solid {t.hairline};
              border-radius: 10px; }}
QFrame#SubtleCard {{ background: {t.surface_alt}; border: 1px solid {t.hairline};
    border-radius: 10px; }}
QFrame#Card[tone="success"], QFrame#SubtleCard[tone="success"] {{ border-color: {t.success}; }}
QFrame#Card[tone="warning"], QFrame#SubtleCard[tone="warning"] {{ border-color: {t.warning}; }}
QFrame#Card[tone="danger"], QFrame#SubtleCard[tone="danger"] {{ border-color: {t.danger}; }}
QFrame#Hairline {{ background: {t.hairline}; border: none; max-height: 1px; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 6px; padding: 0 10px; min-height: 30px; font-size: 13px;
    selection-background-color: {t.selection}; selection-color: {t.text}; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {t.text_tertiary}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {t.accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {t.text_tertiary}; background: {t.surface_alt}; border-color: {t.hairline}; }}
QLineEdit:read-only {{ background: {t.surface_alt}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
                       width: 24px; border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url(__CHEVRON__); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text};
    border: 1px solid {t.border}; border-radius: 6px; padding: 4px; outline: none;
    selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{ subcontrol-origin: border;
    width: 22px; border: none; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t.hover}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url(__CHEVRON_UP__);
    width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url(__CHEVRON__);
    width: 10px; height: 10px; }}

QPushButton {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
              border-radius: 7px; padding: 0 14px; min-height: 32px;
              font-size: 13px; font-weight: 500; }}
QPushButton:hover {{ background: {t.surface_alt}; border-color: {t.text_tertiary}; }}
QPushButton:pressed {{ background: {t.hover}; }}
QPushButton:checked {{ background: {t.accent_soft}; color: {t.accent};
    border-color: {t.accent}; }}
QPushButton:focus {{ border: 1px solid {t.accent}; }}
QPushButton:disabled {{ color: {t.text_tertiary}; background: {t.surface_alt};
                       border-color: {t.hairline}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QToolButton {{ background: transparent; color: {t.text}; border: none; border-radius: 6px;
    padding: 4px 6px; font-size: 13px; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton:pressed {{ background: {t.pressed}; }}
QToolButton:checked {{ background: {t.accent_soft}; color: {t.accent}; }}
QPushButton#Primary {{ background: {t.accent}; color: {t.on_accent};
    border: 1px solid {t.accent}; min-height: 40px; padding: 0 20px;
    font-size: 14px; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {t.accent_pressed}; }}
QPushButton#Primary:focus {{ border: 2px solid {t.accent_soft}; }}
QPushButton#Primary:disabled {{ background: {t.accent}; border-color: {t.accent};
    color: rgba(255, 255, 255, 0.55); }}
QPushButton#Secondary {{ min-height: 40px; padding: 0 16px; }}
QPushButton#Danger {{ color: {t.danger}; }}
QPushButton#Danger:hover {{ background: {t.danger_soft}; border-color: {t.danger}; }}
QPushButton#Link, QPushButton#IconButton {{ background: transparent; border: none;
    color: {t.accent}; padding: 0 6px; min-height: 26px; }}
QPushButton#Link:hover {{ color: {t.accent_hover}; }}
QPushButton#IconButton {{ min-width: 28px; max-width: 28px; padding: 0;
                         border-radius: 6px; }}
QPushButton#IconButton:hover {{ background: {t.hover}; }}
QPushButton#IconButton:pressed {{ background: {t.pressed}; }}
QPushButton#IconButton:checked {{ background: {t.accent_soft}; }}
QPushButton#Link:disabled, QPushButton#IconButton:disabled {{ color: {t.text_tertiary}; }}
QPushButton#Accent {{ background: {t.accent}; color: {t.on_accent};
    border: 1px solid {t.accent}; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton#Accent:pressed {{ background: {t.accent_pressed}; }}
QPushButton#Accent:focus {{ border: 2px solid {t.accent_soft}; }}
QPushButton#Accent:disabled {{ background: {t.accent}; border-color: {t.accent};
    color: rgba(255, 255, 255, 0.55); }}
QPushButton#LargeAction {{ background: {t.accent}; color: {t.on_accent}; border: none;
    border-radius: 12px; padding: 16px 32px; min-height: 0; font-size: 20px;
    font-weight: 600; }}
QPushButton#LargeAction:hover {{ background: {t.accent_hover}; }}
QPushButton#LargeAction:disabled {{ background: {t.accent}; color: rgba(255, 255, 255, 0.55); }}
QPushButton#Flat {{ background: transparent; border: none; padding: 0 8px;
    min-height: 28px; color: {t.text}; }}
QPushButton#Flat:hover {{ background: {t.hover}; }}
QPushButton#Flat:checked {{ background: {t.accent_soft}; color: {t.accent}; }}
QPushButton#Flat:disabled {{ background: transparent; color: {t.text_tertiary}; }}
QPushButton#Large {{ font-size: 17px; padding: 12px 24px; min-height: 0; border-radius: 10px; }}
QComboBox#Large {{ font-size: 17px; min-height: 40px; }}
QMessageBox QPushButton, QDialogButtonBox QPushButton {{ min-width: 80px; }}
QMessageBox QPushButton:default, QDialogButtonBox QPushButton:default {{
    background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; font-weight: 600; }}
QMessageBox QPushButton:default:hover, QDialogButtonBox QPushButton:default:hover {{
    background: {t.accent_hover}; border-color: {t.accent_hover}; }}

QPlainTextEdit#Console, QTextEdit#Console {{ background: {t.console}; color: {t.console_text};
    border: none; border-top: 1px solid {t.hairline};
    border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;
    padding: 8px 12px; font-size: 12px; selection-background-color: {t.selection}; }}

QSplitter::handle {{ background: transparent; }}

QProgressBar#Progress {{ background: {t.hairline}; border: none; border-radius: 2px;
                        max-height: 4px; min-height: 4px; }}
QProgressBar#Progress::chunk {{ background: {t.accent}; border-radius: 2px; }}

QMenuBar {{ background: {t.window}; color: {t.text}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}
QMenu::item:disabled {{ color: {t.text_tertiary}; }}
QMenu::separator {{ height: 1px; background: {t.hairline}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; }}

QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
           padding: 4px 8px; border-radius: 6px; }}

QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget, QListView {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.hairline};
    border-radius: 8px; gridline-color: {t.hairline};
    selection-background-color: {t.accent_soft}; selection-color: {t.text};
    alternate-background-color: {t.surface_alt}; outline: none; font-size: 12px; }}
QTableWidget#Table, QTableView#Table {{ border: none; border-radius: 0; }}
QTableView::item, QTreeView::item, QListView::item {{ padding: 4px 8px; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {t.surface_alt}; color: {t.text_secondary};
    border: none; border-bottom: 1px solid {t.hairline}; padding: 6px 8px;
    font-size: 11px; font-weight: 600; }}
QTableCornerButton::section {{ background: {t.surface_alt}; border: none; }}

QTextEdit, QPlainTextEdit, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 6px; padding: 4px 10px; font-size: 13px;
    selection-background-color: {t.selection}; selection-color: {t.text}; }}
QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{ min-height: 30px; padding: 0 10px; }}
QTextEdit:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
    border: 1px solid {t.accent}; }}
QLineEdit#SearchField {{ border-radius: 15px; background: {t.surface_alt}; padding-left: 12px; }}
QLineEdit#SearchField:focus {{ background: {t.surface}; }}

QCheckBox, QRadioButton {{ color: {t.text}; spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
    border: 1px solid {t.border}; border-radius: 4px; background: {t.surface}; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.text_tertiary}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {t.accent}; border-color: {t.accent}; image: url(__CHECK__); }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text_tertiary}; }}
QCheckBox#Switch {{ spacing: 10px; }}
QCheckBox#Switch::indicator {{ width: 40px; height: 22px; border: none; border-radius: 11px;
    background: transparent; image: url(__SWITCH_OFF__); }}
QCheckBox#Switch::indicator:checked {{ image: url(__SWITCH_ON__); }}
QCheckBox#Switch::indicator:disabled {{ image: url(__SWITCH_DISABLED__); }}

QTabWidget::pane {{ border: 1px solid {t.hairline}; border-radius: 8px;
    background: {t.surface}; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; color: {t.text_secondary};
    font-size: 13px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{ color: {t.text}; font-weight: 600;
    border-bottom: 2px solid {t.accent}; }}
QGroupBox {{ border: 1px solid {t.hairline}; border-radius: 10px;
    margin-top: 10px; padding: 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QStatusBar {{ background: {t.window}; color: {t.text_secondary};
    border-top: 1px solid {t.hairline}; font-size: 12px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: 0 8px; color: {t.text_secondary}; }}
QDialog {{ background: {t.window}; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.hairline}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.surface}; border: 1px solid {t.border};
    width: 16px; height: 16px; margin: -7px 0; border-radius: 8px; }}
QProgressBar {{ background: {t.hairline}; border: none; border-radius: 3px;
    max-height: 6px; min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
QScrollArea {{ border: none; background: transparent; }}
QToolBar {{ background: {t.window}; border: none; border-bottom: 1px solid {t.hairline};
    spacing: 8px; padding: 6px 12px; }}
QToolBar::separator {{ background: {t.hairline}; width: 1px; margin: 4px 6px; }}
QDockWidget {{ color: {t.text}; }}

QTreeWidget#SourceList, QTreeView#SourceList, QListWidget#SourceList {{
    background: transparent; border: none; border-radius: 0; font-size: 13px; outline: none;
    selection-background-color: transparent; }}
QTreeWidget#SourceList::item, QTreeView#SourceList::item, QListWidget#SourceList::item {{
    padding: 5px 6px; border-radius: 7px; color: {t.text}; }}
QTreeWidget#SourceList::item:hover, QTreeView#SourceList::item:hover,
QListWidget#SourceList::item:hover {{ background: {t.hover}; }}
QTreeWidget#SourceList::item:selected, QTreeView#SourceList::item:selected,
QListWidget#SourceList::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}
QTreeWidget#SourceList::branch, QTreeView#SourceList::branch,
QTreeWidget#SourceList::branch:selected, QTreeView#SourceList::branch:selected,
QTreeWidget#SourceList::branch:hover, QTreeView#SourceList::branch:hover {{
    background: {t.sidebar}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 3px;
                              min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text_tertiary}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 3px;
                                min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.text_tertiary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMessageBox {{ background: {t.window}; }}
QMessageBox QLabel {{ color: {t.text}; font-size: 13px; }}
"""
    return (_scaled_type(qss)
            .replace("__CHEVRON_UP__", _asset("chevron-up", t.text_secondary))
            .replace("__CHEVRON__", _asset("chevron-down", t.text_secondary))
            .replace("__CHECK__", _asset("check", t.on_accent))
            .replace("__SWITCH_OFF__", _switch_asset(False, t.border, "#ffffff"))
            .replace("__SWITCH_ON__", _switch_asset(True, t.accent, "#ffffff"))
            .replace("__SWITCH_DISABLED__", _switch_asset(False, t.hairline, t.surface_alt)))
