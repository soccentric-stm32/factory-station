"""SVG icons the bench pages add on top of the shared icon set.

All colours, fonts and style rules come from ``hostcore.soc_theme``.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from hostcore.theme import COLORS
from hostcore.theme import icon as base_icon

_STROKE = (
    'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
)

_ICONS: dict[str, str] = {
    "probe": f'<rect x="7" y="3" width="10" height="9" rx="2" {_STROKE}/><path d="M10 12v3h4v-3M12 15v6M9 7h6" {_STROKE}/>',
    "uart": f'<path d="M8 3v5M16 3v5M6 8h12v4a6 6 0 01-12 0zM12 18v3" {_STROKE}/>',
    "flash": f'<rect x="4" y="9" width="16" height="10" rx="2" {_STROKE}/><path d="M12 3v8M9 8l3 3 3-3" {_STROKE}/>',
    "firmware": f'<rect x="6" y="6" width="12" height="12" rx="2" {_STROKE}/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3M10 10h4v4h-4z" {_STROKE}/>',
    "info": f'<circle cx="12" cy="12" r="9" {_STROKE}/><path d="M12 11v6M12 7.5v.5" {_STROKE}/>',
    "list": f'<path d="M8 6h12M8 12h12M8 18h12M4 6h.5M4 12h.5M4 18h.5" {_STROKE}/>',
    "sliders": f'<path d="M4 7h9M17 7h3M4 17h3M11 17h9M13 4v6M7 14v6" {_STROKE}/>',
    "pulse": f'<path d="M3 12h4l2-6 4 12 2-6h6" {_STROKE}/>',
    "folder": f'<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" {_STROKE}/>',
    "key": f'<circle cx="8" cy="15" r="4" {_STROKE}/><path d="M11 12l9-9M16 7l3 3M14 9l2 2" {_STROKE}/>',
    "save": f'<path d="M5 3h11l3 3v15H5z" {_STROKE}/><path d="M8 3v6h8V3M8 21v-7h8v7" {_STROKE}/>',
    "reset": f'<path d="M4 12a8 8 0 108-8" {_STROKE}/><path d="M4 4v5h5" {_STROKE}/>',
    "dot": '<circle cx="12" cy="12" r="5" fill="{c}"/>',
}

def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """An icon from the bench set, falling back to the shared set."""
    body = _ICONS.get(name)
    if body is None:
        return base_icon(name, color, size)
    colour = color or COLORS["text"]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24">{body.format(c=colour)}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size * 2, size * 2))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)


def bench_icon_names() -> list[str]:
    return sorted(_ICONS)
