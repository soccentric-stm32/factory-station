"""SoCcentric native look: Windows 11 Mica, Segoe UI, Fluent controls.

Shared by every SoCcentric PySide6 app, byte-identical, next to ``soc_theme.py``.
It leaves ``soc_theme.py`` alone and layers the native Windows look on top of
it at run time:

* tokens   the Fluent greys replace the theme's (``soc_theme.LIGHT`` / ``DARK``
           are swapped before the first apply); the accent is kept
* fonts    Segoe UI Variable and Cascadia Mono replace Inter / JetBrains Mono
* styles   ``soc_theme.stylesheet`` is wrapped so every sheet it returns (apply,
           zoom, light/dark) carries the Fluent rules: 30-32 px controls, 4 px
           radii, an accent underline on focus, panels that let Mica through
* windows  one application event filter gives every top-level window the dark
           or light title bar, rounded corners and the Mica backdrop; a main
           window opts in to Mica with ``prepare_window(self)`` in its __init__
* nav      :func:`fluent_nav` turns the shared ``NavItem`` into the Windows 11
           sidebar entry (soft fill and an accent bar)

``install()`` runs once, after QApplication exists and before the first
``soc_theme.apply()``.  Everything is a no-op off Windows.  SOC_NO_MICA=1 keeps
solid backgrounds; SOC_NATIVE=0 turns the whole layer off.
"""

from __future__ import annotations

import ctypes
import dataclasses
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import soc_theme as theme

ENABLED = sys.platform == "win32" and os.environ.get("SOC_NATIVE") != "0"

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_MAINWINDOW = 2          # Mica
_DWMWCP_ROUND = 2
_MICA_MIN_BUILD = 22621         # Windows 11 22H2

_installed = False
_window_filter = None


def build() -> int:
    return sys.getwindowsversion().build if sys.platform == "win32" else 0


def mica_available() -> bool:
    return (ENABLED and build() >= _MICA_MIN_BUILD
            and os.environ.get("SOC_NO_MICA") != "1")


# ---- colours ------------------------------------------------------------------

def _tokens() -> None:
    """Swap the theme's light and dark tokens for the Fluent ones."""
    theme.LIGHT = dataclasses.replace(
        theme.LIGHT,
        window="#f3f3f3", sidebar="#eeeeee", surface="#ffffff",
        surface_alt="#f7f7f7", border="#d1d1d1", hairline="#e5e5e5",
        text="#1a1a1a", text_secondary="#5d5d5d", text_tertiary="#8a8a8a",
        hover="rgba(0, 0, 0, 0.045)", pressed="rgba(0, 0, 0, 0.075)",
        console="#fafafa", console_text="#1a1a1a",
    )
    theme.DARK = dataclasses.replace(
        theme.DARK,
        window="#202020", sidebar="#1c1c1c", surface="#2b2b2b",
        surface_alt="#323232", border="#4d4d4d", hairline="#3a3a3a",
        text="#ffffff", text_secondary="#cfcfcf", text_tertiary="#9d9d9d",
        hover="rgba(255, 255, 255, 0.06)", pressed="rgba(255, 255, 255, 0.09)",
        console="#1b1b1b", console_text="#e6e6e6",
    )


# ---- fonts --------------------------------------------------------------------

def _family(candidates: list[str], fallback: str) -> str:
    families = set(QFontDatabase.families())
    return next((name for name in candidates if name in families), fallback)


def _ui_font(size: float = 13) -> QFont:
    font = QFont(_family(["Segoe UI Variable Text", "Segoe UI Variable",
                          "Segoe UI"], "Segoe UI"))
    font.setPixelSize(theme.px(size))
    font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    return font


def _mono_font(size: float = 12) -> QFont:
    font = QFont(_family(["Cascadia Mono", "Cascadia Code", "Consolas"],
                         "Consolas"))
    font.setPixelSize(theme.px(size))
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# ---- style sheet --------------------------------------------------------------

def _stylesheet(t: theme.Tokens, mica: bool) -> str:
    glass = ""
    if mica:
        card = "rgba(255, 255, 255, 0.055)" if t.dark else "rgba(255, 255, 255, 0.72)"
        glass = f"""
QMainWindow, QWidget#Root, QMenuBar {{ background: transparent; }}
QWidget#Sidebar, QFrame#Sidebar {{ background: transparent; border: none; }}
QWidget#Content, QWidget#Page, QScrollArea#PageScroll, QWidget#PageScrollBody,
QStackedWidget#Pages {{ background: transparent; }}
QFrame#Card, QFrame#DeviceBox {{ background: {card}; }}
QPlainTextEdit#Console, QTextEdit#Console {{
    background: rgba(0, 0, 0, {0.28 if t.dark else 0.035}); }}
"""
    edge = "rgba(255, 255, 255, 0.09)" if t.dark else "rgba(0, 0, 0, 0.073)"
    edge_bottom = "rgba(255, 255, 255, 0.05)" if t.dark else "rgba(0, 0, 0, 0.16)"
    ctrl = "rgba(255, 255, 255, 0.06)" if t.dark else "rgba(255, 255, 255, 0.70)"
    ctrl_hover = "rgba(255, 255, 255, 0.085)" if t.dark else "rgba(249, 249, 249, 0.50)"
    ctrl_press = "rgba(255, 255, 255, 0.035)" if t.dark else "rgba(249, 249, 249, 0.30)"
    return glass + f"""
QPushButton {{ background: {ctrl}; border: 1px solid {edge};
    border-bottom: 1px solid {edge_bottom}; border-radius: 4px;
    min-height: 30px; padding: 0 12px; font-weight: 400; }}
QPushButton:hover {{ background: {ctrl_hover}; border-color: {edge};
    border-bottom-color: {edge_bottom}; }}
QPushButton:pressed {{ background: {ctrl_press}; color: {t.text_secondary}; }}
QPushButton:focus {{ border: 1px solid {edge}; border-bottom: 2px solid {t.accent}; }}
QPushButton:disabled {{ background: {t.hover}; color: {t.text_tertiary};
    border: 1px solid transparent; }}
QPushButton#Primary, QPushButton#Accent {{ background: {t.accent}; color: {t.on_accent};
    border: 1px solid {t.accent_hover}; border-bottom: 1px solid {t.accent_pressed};
    border-radius: 4px; min-height: 32px; padding: 0 18px;
    font-size: 13px; font-weight: 600; }}
QPushButton#Accent {{ padding: 0 14px; min-height: 30px; }}
QPushButton#Primary:hover, QPushButton#Accent:hover {{ background: {t.accent_hover}; }}
QPushButton#Primary:pressed, QPushButton#Accent:pressed {{
    background: {t.accent_pressed}; color: {t.on_accent}; }}
QPushButton#Primary:focus, QPushButton#Accent:focus {{
    border: 2px solid {t.text}; border-radius: 4px; }}
QPushButton#Primary:disabled, QPushButton#Accent:disabled {{
    background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}
QPushButton#Secondary {{ min-height: 30px; padding: 0 14px; }}
QPushButton#Danger {{ color: {t.danger}; }}
QPushButton#Link, QPushButton#IconButton, QPushButton#Flat {{ background: transparent;
    border: none; border-radius: 4px; }}
QPushButton#Link:hover, QPushButton#IconButton:hover, QPushButton#Flat:hover {{
    background: {t.hover}; }}
QPushButton#Link:pressed, QPushButton#IconButton:pressed, QPushButton#Flat:pressed {{
    background: {t.pressed}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {ctrl}; border: 1px solid {edge};
    border-bottom: 1px solid {edge_bottom}; border-radius: 4px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ background: {ctrl_hover};
    border-color: {edge}; border-bottom-color: {edge_bottom}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    background: {t.surface}; border: 1px solid {edge}; border-bottom: 2px solid {t.accent}; }}
QLineEdit:read-only {{ background: {t.hover}; }}
QComboBox QAbstractItemView {{ background: {t.surface}; border: 1px solid {edge};
    border-radius: 8px; padding: 4px; selection-background-color: {t.hover};
    selection-color: {t.text}; }}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px; border-radius: 4px; }}
QComboBox QAbstractItemView::item:selected {{ background: {t.hover}; color: {t.text}; }}

QFrame#Card, QFrame#DeviceBox, QFrame#SubtleCard {{ border: 1px solid {edge};
    border-radius: 8px; }}
QFrame#Hairline {{ background: {edge}; }}

QLabel#PageTitle {{ font-size: 24px; font-weight: 600; }}
QLabel#AppTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#SidebarSection {{ font-size: 12px; font-weight: 600; color: {t.text_tertiary}; }}
QLabel#Pill {{ border-radius: 10px; padding: 2px 10px; }}

QMenu {{ background: {t.surface}; border: 1px solid {edge}; border-radius: 8px;
    padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 4px; margin: 1px 2px; }}
QMenu::item:selected {{ background: {t.hover}; color: {t.text}; }}
QMenuBar {{ padding: 0 6px; }}
QMenuBar::item {{ padding: 3px 9px; border-radius: 4px; margin: 2px 1px; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QTreeWidget#SourceList::item, QTreeView#SourceList::item, QListWidget#SourceList::item {{
    padding: 5px 6px; border-radius: 5px; border-left: 3px solid transparent; }}
QTreeWidget#SourceList::item:hover, QTreeView#SourceList::item:hover,
QListWidget#SourceList::item:hover {{ background: {t.hover}; }}
QTreeWidget#SourceList::item:selected, QTreeView#SourceList::item:selected,
QListWidget#SourceList::item:selected {{ background: {t.pressed}; color: {t.text};
    border-left: 3px solid {t.accent}; }}
QToolTip {{ border: 1px solid {edge}; border-radius: 4px; padding: 4px 8px; }}

QScrollBar:vertical {{ width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.text_tertiary}; border-radius: 2px;
    min-height: 32px; margin: 0 4px; }}
QScrollBar::handle:vertical:hover {{ margin: 0 2px; }}
QScrollBar:horizontal {{ height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.text_tertiary}; border-radius: 2px;
    min-width: 32px; margin: 4px 0; }}
QScrollBar::handle:horizontal:hover {{ margin: 2px 0; }}
"""


def _legacy(t: theme.Tokens, mica: bool) -> str:
    """The pre-2026 object names HWVAL Studio and HWVAL Factory still use, drawn
    in the current look: titles, chips, toolbars, metrics, logs, bench pages."""
    panel = "transparent" if mica else t.window
    return f"""
QWidget#BenchPage, QScrollArea#BenchScroll, QWidget#BenchScrollBody {{
    background: {panel}; border: none; }}
QFrame#Toolbar, QFrame#Header {{ background: {panel};
    border: none; border-bottom: 1px solid {t.hairline}; }}
QFrame#Separator {{ background: {t.hairline}; border: none; max-height: 1px; }}
QFrame#SidebarFooter {{ background: transparent; border-top: 1px solid {t.hairline}; }}
QLabel#Title, QLabel#ToolbarTitle {{ font-size: 24px; font-weight: 600; }}
QLabel#Subtitle, QLabel#ToolbarSubtitle, QLabel#Secondary, QLabel#Muted,
QLabel#FieldLabel, QLabel#SidebarMuted {{ font-size: 13px; color: {t.text_secondary}; }}
QLabel#Heading {{ font-size: 14px; font-weight: 600; }}
QLabel#FieldValue {{ font-size: 13px; }}
QLabel#Metric, QLabel#ToolbarMetric {{ font-size: 22px; font-weight: 600; }}
QLabel#ToolbarMetricLabel {{ font-size: 11px; font-weight: 600; color: {t.text_tertiary}; }}
QLabel#ToolbarProfile {{ font-size: 13px; font-weight: 600; }}
QLabel#SectionHeader {{ font-size: 12px; font-weight: 600; color: {t.text_tertiary};
    padding: 12px 12px 4px 12px; }}
QLabel#SidebarTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#SidebarVersion {{ font-size: 11px; color: {t.text_tertiary}; }}
QLabel#SidebarUser {{ font-size: 13px; font-weight: 600; }}
QLabel#SidebarAvatar {{ background: {t.accent_soft}; color: {t.accent};
    border-radius: 17px; font-size: 13px; font-weight: 600; }}
QLabel#SidebarBadge, QLabel#ToolbarBadge, QLabel#ChipIdle, QLabel#ChipOk,
QLabel#ChipBad, QLabel#ChipWarn {{ border-radius: 10px; padding: 2px 10px;
    font-size: 12px; font-weight: 600; background: {t.hover}; color: {t.text_secondary}; }}
QLabel#ChipOk, QLabel#ChipBad, QLabel#ChipWarn {{ background: {t.accent_soft};
    color: {t.accent}; }}
QPlainTextEdit#Log, QPlainTextEdit#LogView {{ background: {t.console};
    color: {t.console_text}; font-size: 12px; }}
QLineEdit#AddressEdit {{ font-size: 13px; }}
QTreeWidget#SourceList::branch {{ background: transparent; }}
"""


_base_stylesheet = None


def _stylesheet_with_native(t: theme.Tokens) -> str:
    """soc_theme.stylesheet plus the Fluent rules (zoom-scaled like the rest)."""
    mica = mica_available()
    return _base_stylesheet(t) + theme._scaled_type(
        _stylesheet(t, mica) + _legacy(t, mica))


def _restyle_windows(_tokens: theme.Tokens | None = None) -> None:
    app = QApplication.instance()
    if app is not None:
        for widget in app.topLevelWidgets():
            style_window(widget)


# ---- native window ------------------------------------------------------------

def _dwm_set(hwnd: int, attribute: int, value: int) -> None:
    data = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), ctypes.c_uint(attribute), ctypes.byref(data),
        ctypes.sizeof(data))


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


def prepare_window(window: QWidget) -> None:
    """Call first thing in a main window's ``__init__``, right after
    ``super().__init__()``: lets the backdrop shine through the window.  Set any
    later, the repaint no longer clears old pixels and text ghosts."""
    if mica_available():
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


def style_window(window: QWidget) -> None:
    """Title bar colour, corners and backdrop for a top-level window."""
    if not ENABLED or not window.isWindow() or build() < 19041:
        return
    try:
        hwnd = int(window.winId())
        _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, int(theme.current().dark))
        if build() >= 22000:
            _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
        if mica_available() and window.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground):
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                wintypes.HWND(hwnd), ctypes.byref(_Margins(-1, -1, -1, -1)))
            _dwm_set(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_MAINWINDOW)
    except (OSError, AttributeError, ValueError):
        pass  # a missing DWM feature only costs the effect


class _WindowFilter(QObject):
    """Application filter: DWM styling (title bar, corners, Mica) whenever a
    top-level window is shown, so no app styles its windows by hand."""

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                and obj.isWindow()):
            style_window(obj)
        return False


def fluent_nav(base: type) -> type:
    """``base`` (the shared NavItem, or an app's own copy) drawn the Windows 11
    way: a soft fill and an accent bar on the selected item instead of a solid
    accent block, and a focus ring only for keyboard focus."""
    if not ENABLED:
        return base

    class FluentNavItem(base):  # type: ignore[misc, valid-type]
        _plain = False
        _keyboard_focus = False

        def sizeHint(self) -> QSize:
            return QSize(200, theme.px(40) if self.subtitle else theme.px(34))

        def isChecked(self) -> bool:
            return False if self._plain else super().isChecked()

        def hasFocus(self) -> bool:
            return False if self._plain else super().hasFocus()

        def focusInEvent(self, event) -> None:
            self._keyboard_focus = event.reason() in (
                Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
            super().focusInEvent(event)

        def paintEvent(self, event) -> None:
            t = theme.current()
            checked = super().isChecked()
            ring = self._keyboard_focus and super().hasFocus() and not checked
            if checked or ring:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                if checked:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 23) if t.dark
                                     else QColor(0, 0, 0, 14))
                    painter.drawRoundedRect(self.rect(), 5, 5)
                    painter.setBrush(QColor(t.accent))
                    height = self.height() * 0.46
                    painter.drawRoundedRect(
                        0, (self.height() - height) / 2, 3, height, 1.5, 1.5)
                else:
                    painter.setPen(QPen(QColor(t.text), 1.5))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)
                painter.end()
            self._plain = True
            try:
                super().paintEvent(event)
            finally:
                self._plain = False

    FluentNavItem.__name__ = f"Fluent{base.__name__}"
    return FluentNavItem


# ---- entry point --------------------------------------------------------------

def install() -> None:
    """Call once, after QApplication exists, before the first soc_theme.apply()."""
    global _installed, _base_stylesheet
    if not ENABLED or _installed:
        return
    _installed = True
    _tokens()
    theme.ui_font = _ui_font
    theme.mono_font = _mono_font
    _base_stylesheet = theme.stylesheet
    theme.stylesheet = _stylesheet_with_native
    theme.on_change(_restyle_windows)
    app = QApplication.instance()
    if app is not None:
        global _window_filter
        _window_filter = _WindowFilter(app)
        app.installEventFilter(_window_filter)
