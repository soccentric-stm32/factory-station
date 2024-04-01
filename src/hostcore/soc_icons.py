"""SoCcentric desktop GUI icons: one glyph set, drawn sharp at any scale.

This file is shared by every SoCcentric PySide6 app (BOOT Flasher, Factory
Data, HWVAL Factory, HWVAL Studio).  Each app carries its own byte-identical
copy next to ``soc_theme.py``, so the apps stay independent while they look
the same; ``scripts/check-theme-sync.sh`` in the workspace repo checks the
copies.

One style everywhere: a 24 x 24 grid, 1.8 px round strokes, no fills except
for the media glyphs (play, stop) and small dots.

Icons returned by :func:`icon` are live.  Their colour is a theme token
("text", "accent", ...), or a hex colour that matches a token at the time of
the call, so a light/dark switch recolours every icon on the next repaint
without the app rebuilding anything.  They render from the vector source at
the exact device pixel ratio of the screen, so they stay crisp at 125, 150
and 200 % scaling.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QByteArray, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import soc_theme

_F = 'fill="currentColor" stroke="none"'

GLYPHS: dict[str, str] = {
    # ---- hardware ----------------------------------------------------------
    "chip": '<rect x="5" y="5" width="14" height="14" rx="2"/>'
            '<rect x="9" y="9" width="6" height="6" rx="1"/>'
            '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "board": '<rect x="3" y="4" width="18" height="16" rx="2"/>'
             f'<circle cx="8" cy="9" r="1.6" {_F}/><circle cx="16" cy="9" r="1.6" {_F}/>'
             '<path d="M8 15h8"/>',
    "memory": '<rect x="3" y="7" width="18" height="10" rx="2"/>'
              '<path d="M7 7v10M11 7v10M15 7v10M3 4v3M21 4v3M3 17v3M21 17v3"/>',
    "probe": '<rect x="7" y="3" width="10" height="9" rx="2"/><path d="M10 12v3h4v-3M12 15v6M9 7h6"/>',
    "plug": '<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5"/>',
    "usb": '<path d="M12 21V4M12 4l-3 3M12 4l3 3M12 11l-4 3v2M12 11l4 3v2"/>'
           f'<circle cx="12" cy="20" r="1.5" {_F}/>',
    "ethernet": '<rect x="4" y="9" width="16" height="9" rx="2"/><path d="M8 9V4M12 9V4M16 9V4"/>',
    "led": '<circle cx="12" cy="12" r="5"/>'
           '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1"/>',
    "button": f'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3" {_F}/>',
    "gpio": '<path d="M4 7h4l2 4h4l2-4h4M4 17h4l2-4h4l2 4h4"/>'
            f'<circle cx="4" cy="7" r="1.5" {_F}/><circle cx="20" cy="17" r="1.5" {_F}/>',
    "analog": '<path d="M3 12c2-6 4 6 6 0s4 6 6 0 4 6 6 0"/>',
    "pulse": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "power": '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "timers": '<circle cx="12" cy="13" r="8"/><path d="M9 2h6M12 2v3M12 13l3-2"/>',
    "comm": '<path d="M4 8h12a3 3 0 0 1 3 3v1M20 16H8a3 3 0 0 1-3-3v-1M4 5v6M20 19v-6"/>',
    "connectivity": f'<circle cx="12" cy="12" r="2.5" {_F}/>'
                    '<path d="M6.5 6.5a7.8 7.8 0 0 0 0 11M17.5 6.5a7.8 7.8 0 0 1 0 11'
                    'M3.5 3.5a12 12 0 0 0 0 17M20.5 3.5a12 12 0 0 1 0 17"/>',
    "graphics": '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 5M8 21h8"/>',
    "connect": '<path d="M9 15l6-6M8 7l-2 2a4 4 0 0 0 0 6l1 1M16 17l2-2a4 4 0 0 0 0-6l-1-1"/>',
    "disconnect": '<path d="M9 15l6-6M8 7l-2 2a4 4 0 0 0 0 6l1 1M16 17l2-2a4 4 0 0 0 0-6l-1-1M3 3l18 18"/>',
    # ---- software and projects ---------------------------------------------
    "app": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>',
    "cog": '<circle cx="12" cy="12" r="3"/>'
           '<path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3'
           'M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "flask": '<path d="M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0'
             ' 1.5-2.5L14 9V3"/><path d="M7 15h10"/>',
    "zap": '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
    "hammer": '<path d="M15 12l-8.5 8.5a2.1 2.1 0 0 1-3-3L12 9"/>'
              '<path d="M17.6 15L22 10.6M20.9 11.7l-6.6-6.6a4 4 0 0 0-5.7 0L8 5.7l7.3 7.3"/>',
    "key": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M16 7l3 3M19 4l2 2"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "branch": '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/>'
              '<circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5a6 6 0 0 1-6 6H8.5"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>',
    "demo": '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M12 17v4"/>'
            f'<path d="M10 8.5l4 2.5-4 2.5z" {_F}/>',
    "database": '<ellipse cx="12" cy="5" rx="7" ry="3"/>'
                '<path d="M5 5v14c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "logo": '<path d="M12 2.5l8.5 4.75v9.5L12 21.5l-8.5-4.75v-9.5z"/>'
            '<path d="M3.5 7.25L12 12l8.5-4.75M12 12v9.5"/>',
    # ---- documents and data ------------------------------------------------
    "file": '<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v5h5M9 13h6M9 17h6"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "book": '<path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v18H6.5A2.5 2.5 0 0 0 4 22.5z"/><path d="M4 20V4.5"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/>'
            '<path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    "printer": '<path d="M7 8V3h10v5"/><rect x="3" y="8" width="18" height="8" rx="2"/>'
               '<rect x="7" y="14" width="10" height="7"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "all": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 12h10M7 15h6"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/>'
               '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M2 20h20"/>',
    "dashboard": '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/>'
                 '<rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>',
    "overview": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    "history": '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    "label": '<path d="M3 7h13l5 5-5 5H3z"/>' f'<circle cx="8" cy="12" r="1.5" {_F}/>',
    "other": '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
    # ---- actions -----------------------------------------------------------
    "download": '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
    "upload": '<path d="M12 15V3M7 8l5-5 5 5M4 14v6h16v-6"/>',
    "import": '<path d="M12 3v12M7.5 10.5L12 15l4.5-4.5M4 14v6h16v-6"/>',
    "update": '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5M12 8v5l3 2"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/>',
    "play": f'<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12-7.5a1 1 0 0 0 0-1.72l-12-7.5A1 1 0 0 0 7 4.5z" {_F}/>',
    "stop": f'<rect x="5.5" y="5.5" width="13" height="13" rx="2.5" {_F}/>',
    "skip": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "zoom": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6M11 8v6"/>',
    "login": '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="M10 17l5-5-5-5M15 12H3"/>',
    "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "sidebar": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M6 15l6-6 6 6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    # ---- status ------------------------------------------------------------
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-.8.7-1.5 1.2-1.5 2.5M12 17h.01"/>',
    "alert": '<path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
             '<path d="M12 9v4M12 17h.01"/>',
    "na": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "running": '<circle cx="12" cy="12" r="9" stroke-dasharray="4 3"/>',
    "dot": f'<circle cx="12" cy="12" r="5" {_F}/>',
}

# Names the apps use for the same idea.  One glyph per idea.
ALIASES: dict[str, str] = {
    "bolt": "zap", "flash": "zap", "firmware": "chip", "cpu": "chip",
    "settings": "cog", "test": "flask", "warning": "alert", "console": "terminal",
    "print": "printer", "reports": "file", "export": "upload", "uart": "plug",
    "stlink": "probe", "boot": "chip",
}

# Glyphs drawn heavier so they read at 12-16 px next to text.
_HEAVY = {"check": 2.4, "x": 2.4}


def resolve(name: str) -> str:
    """The glyph name behind ``name``; unknown names fall back to ``dot``."""
    name = ALIASES.get(name, name)
    return name if name in GLYPHS else "dot"


def names() -> list[str]:
    return sorted(GLYPHS)


def svg(name: str, color: str, stroke: float = 1.8) -> str:
    glyph = resolve(name)
    width = max(stroke, _HEAVY.get(glyph, 0.0))
    body = GLYPHS[glyph].replace("currentColor", color)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


# ---- colours ----------------------------------------------------------------

# Token names tried, in order, when a hex colour is mapped back to a token.
_TOKEN_ORDER = ("text", "text_secondary", "text_tertiary", "accent", "on_accent",
                "success", "warning", "danger", "accent_hover", "accent_pressed",
                "border", "surface")


def token_for(color: str | None) -> str | None:
    """The theme token a colour stands for, or None for a fixed colour."""
    if color is None:
        return "text"
    if not color.startswith("#") and hasattr(soc_theme.current(), color):
        return color
    wanted = QColor(color).name().lower()
    tokens = soc_theme.current()
    for name in _TOKEN_ORDER:
        if QColor(getattr(tokens, name)).name().lower() == wanted:
            return name
    return None


def _hex(color: str | None) -> str:
    """Current hex value of a token name or a fixed colour."""
    token = token_for(color)
    if token is not None:
        return QColor(getattr(soc_theme.current(), token)).name()
    return QColor(color).name()


# ---- rendering ----------------------------------------------------------------

_CACHE: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
_CACHE_LIMIT = 512


def _screen_ratio() -> float:
    screen = QGuiApplication.primaryScreen() if QGuiApplication.instance() is not None else None
    return max(1.0, screen.devicePixelRatio()) if screen is not None else 1.0


def pixmap(name: str, color: str | None = None, size: int = 16,
           ratio: float | None = None, stroke: float = 1.8) -> QPixmap:
    """A glyph at ``size`` logical pixels, rendered for ``ratio`` (screen ratio
    by default, at least 2x so it stays sharp when dragged to a denser screen)."""
    ratio = ratio or max(2.0, _screen_ratio())
    colour = _hex(color)
    key = (name, colour, size, round(ratio, 3), stroke)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    device = max(1, round(size * ratio))
    pix = QPixmap(QSize(device, device))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, colour, stroke).encode())).render(
        painter, QRectF(0, 0, device, device))
    painter.end()
    pix.setDevicePixelRatio(ratio)
    _CACHE[key] = pix
    if len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)
    return pix


def paint(painter: QPainter, name: str, rect: QRectF, color: str | None = None,
          stroke: float = 1.8) -> None:
    """Draw a glyph straight into a custom paintEvent (vector, always sharp)."""
    QSvgRenderer(QByteArray(svg(name, _hex(color), stroke).encode())).render(painter, rect)


class _GlyphEngine(QIconEngine):
    """Renders the glyph at paint time in the current theme's colours."""

    def __init__(self, name: str, color: str | None, selected: str | None,
                 disabled: str | None, stroke: float) -> None:
        super().__init__()
        self._name = name
        self._color = token_for(color) or color
        self._selected = token_for(selected) or selected if selected else None
        self._disabled = token_for(disabled) or disabled if disabled else "text_tertiary"
        self._stroke = stroke

    def _colour_for(self, mode: QIcon.Mode) -> str | None:
        if mode == QIcon.Mode.Disabled:
            return self._disabled
        if mode in (QIcon.Mode.Selected, QIcon.Mode.Active) and self._selected:
            return self._selected
        return self._color

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        side = min(rect.width(), rect.height())
        target = QRectF(rect.x() + (rect.width() - side) / 2,
                        rect.y() + (rect.height() - side) / 2, side, side)
        paint(painter, self._name, target, self._colour_for(mode), self._stroke)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State,
                     scale: float) -> QPixmap:
        side = max(1, min(size.width(), size.height()))
        return pixmap(self._name, self._colour_for(mode), side, max(1.0, scale), self._stroke)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, max(1.0, _screen_ratio()))

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        side = min(size.width(), size.height())
        return QSize(side, side)

    def clone(self) -> QIconEngine:
        return _GlyphEngine(self._name, self._color, self._selected, self._disabled, self._stroke)

    def key(self) -> str:
        return "soc-glyph"


def icon(name: str, color: str | None = None, size: int = 16, *,
         selected: str | None = None, disabled: str | None = None,
         stroke: float = 1.8) -> QIcon:
    """A live icon.  ``color`` is a token name ("text", "accent", ...) or a
    hex colour; ``selected`` colours the glyph on a selected row or the
    current sidebar item.  ``size`` is kept for callers that pass it; the
    icon draws at whatever size the widget asks for."""
    del size
    return QIcon(_GlyphEngine(name, color, selected, disabled, stroke))


def nav_icon(name: str) -> QIcon:
    """Sidebar icon: secondary text normally, white on the selected row."""
    return icon(name, "text_secondary", selected="on_accent")


# ---- the app badge -----------------------------------------------------------

# Corner radius and glyph inset as fractions of the tile; the packaged app
# icons (packaging/make_icons.py in each app) use the same geometry.
BADGE_RADIUS = 0.24
BADGE_INSET = 0.22
BADGE_STROKE = 1.9

def paint_badge(painter: QPainter, glyph: str, rect: QRectF) -> None:
    """Rounded accent tile with a white glyph: the SoCcentric app badge."""
    t = soc_theme.current()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(t.accent))
    radius = rect.width() * BADGE_RADIUS
    painter.drawRoundedRect(rect, radius, radius)
    inset = rect.width() * BADGE_INSET
    paint(painter, glyph, rect.adjusted(inset, inset, -inset, -inset), "#ffffff", BADGE_STROKE)
    painter.restore()


def badge(glyph: str, size: int = 36, ratio: float | None = None) -> QPixmap:
    """The app badge as a pixmap (for window icons and dialogs)."""
    ratio = ratio or max(2.0, _screen_ratio())
    device = max(1, round(size * ratio))
    pix = QPixmap(QSize(device, device))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    paint_badge(painter, glyph, QRectF(QPoint(0, 0), QSize(device, device)))
    painter.end()
    pix.setDevicePixelRatio(ratio)
    return pix


def app_icon(glyph: str) -> QIcon:
    """Window icon built from the badge at the usual sizes."""
    result = QIcon()
    for side in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(badge(glyph, side, 1.0))
    return result
