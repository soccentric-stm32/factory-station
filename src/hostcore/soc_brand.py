"""SoCcentric brand: one colour and one icon set for every app.

Shared by every SoCcentric PySide6 app, byte-identical, next to ``soc_theme.py``.
Applied at run time so ``soc_theme.py`` itself stays untouched:

* one colour   the theme's green, orange and red status colours are mapped onto
               the single accent.  The UI is neutral greys plus one blue; a
               state is told apart by its icon, its label and filled-versus-
               hollow, never by a second hue.  ``SOC_MONOCHROME=0`` restores
               the three status colours.
* icon set     refined glyphs with an identity per thing: a boot chip for the
               bootloader, layers for FreeRTOS, wind for Zephyr, an orbit for
               Embassy, a factory, a lab flask, and the common actions.  They
               are merged into ``soc_icons.GLYPHS`` when the app has it; apps
               with their own icon registry call :func:`inline` or read
               :data:`GLYPHS`.

``install()`` runs once, after QApplication exists and after
``soc_native.install()``, before the first ``soc_theme.apply()``.
"""

from __future__ import annotations

import dataclasses
import math
import os

from . import soc_theme

_FILL = 'fill="currentColor" stroke="none"'


def _gear(teeth: int = 8, outer: float = 9.6, root: float = 7.3,
          cx: float = 12, cy: float = 12) -> str:
    """A gear outline: trapezoid teeth joined by arcs on the root circle."""
    def pt(radius: float, deg: float) -> str:
        a = math.radians(deg)
        return f"{cx + radius * math.sin(a):.2f} {cy - radius * math.cos(a):.2f}"
    step = 360 / teeth
    d = f"M{pt(root, -step * 0.30)}"
    for i in range(teeth):
        t = i * step
        d += (f"L{pt(outer, t - step * 0.17)}L{pt(outer, t + step * 0.17)}"
              f"L{pt(root, t + step * 0.30)}"
              f"A{root} {root} 0 0 1 {pt(root, t + step - step * 0.30)}")
    return d + "z"


GLYPHS: dict[str, str] = {
    # The bootloader: a chip with a boot arrow on the die.
    "boot": '<rect x="6" y="6" width="12" height="12" rx="2.6"/>'
            '<path d="M9.5 3v3M14.5 3v3M9.5 18v3M14.5 18v3'
            'M3 9.5h3M3 14.5h3M18 9.5h3M18 14.5h3"/>'
            '<path d="M12 14.6V9.6M9.7 11.8l2.3-2.3 2.3 2.3"/>',
    # FreeRTOS: a stack of tasks.
    "layers": '<path d="M12 3.2l8.6 4.3-8.6 4.3-8.6-4.3z"/>'
              '<path d="M3.4 12l8.6 4.3 8.6-4.3M3.4 16.5l8.6 4.3 8.6-4.3"/>',
    # Zephyr: the west wind.
    "wind": '<path d="M3 8.5h10.2a2.7 2.7 0 1 0-2.6-3.4"/>'
            '<path d="M3 12.5h15.2a2.9 2.9 0 1 1-2.7 3.9"/>'
            '<path d="M3 16.5h6.2a2.3 2.3 0 1 1-2.2 3"/>',
    # Embassy: async tasks in orbit.
    "orbit": f'<circle cx="12" cy="12" r="3.1" {_FILL}/>'
             '<ellipse cx="12" cy="12" rx="9.6" ry="3.4" transform="rotate(-32 12 12)"/>'
             f'<circle cx="20.1" cy="7" r="1.5" {_FILL}/>',
    "gear": f'<path d="{_gear()}"/><circle cx="12" cy="12" r="2.8"/>',
    "factory": '<path d="M3.5 20.5V11l6 3.6V11l6 3.6V4h5v16.5z"/>'
               '<path d="M8 20.5v-3M12.5 20.5v-3M17 20.5v-3M18 7.5h.01"/>',
    "lab": '<path d="M9.2 3h5.6M10.2 3v6.1L4.9 17.6A2.2 2.2 0 0 0 6.8 21h10.4a2.2 2.2 0 0 0 1.9-3.4l-5.3-8.5V3"/>'
           '<path d="M8.3 15.2l2.5 2.4 4.6-4.5"/>',
    # Actions.
    "zap": '<path d="M13.4 2.6L5 13.4c-.4.5 0 1.1.6 1.1h5.2l-.9 6.2c-.1.7.8 1 1.2.4l8.4-10.9c.4-.5 0-1.1-.6-1.1h-5.2l.9-5.9c.1-.7-.8-1-1.2-.4z"/>',
    "refresh": '<path d="M19.6 11a7.8 7.8 0 0 0-13.9-3.3M5.2 3.6v4.4h4.4"/>'
               '<path d="M4.4 13a7.8 7.8 0 0 0 13.9 3.3M18.8 20.4V16h-4.4"/>',
    "reset": '<path d="M4.4 12a7.8 7.8 0 1 0 2.4-5.6L4.2 9M4.2 4v5.2h5.2"/>',
    "copy": '<rect x="9" y="9" width="11.5" height="11.5" rx="2.6"/>'
            '<path d="M5.7 15H5.4A2.4 2.4 0 0 1 3 12.6V5.4A2.4 2.4 0 0 1 5.4 3h7.2A2.4 2.4 0 0 1 15 5.4v.3"/>',
    "trash": '<path d="M4 6.8h16M9 6.8V4.6A1.6 1.6 0 0 1 10.6 3h2.8A1.6 1.6 0 0 1 15 4.6v2.2"/>'
             '<path d="M6.4 6.8l.8 12.4A2 2 0 0 0 9.2 21h5.6a2 2 0 0 0 2-1.8l.8-12.4M10 10.8v6M14 10.8v6"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="3.2"/>'
                '<path d="M7.4 9.6l3 2.4-3 2.4M13 15h3.6"/>',
    "app": '<rect x="3" y="4" width="18" height="16" rx="3.2"/><path d="M3 9h18M6.6 6.5h.01M9.4 6.5h.01"/>',
    "folder": '<path d="M3 7.4A2.4 2.4 0 0 1 5.4 5h3.7c.6 0 1.2.3 1.6.8l.8 1.2H18.6A2.4 2.4 0 0 1 21 9.4v8.2a2.4 2.4 0 0 1-2.4 2.4H5.4A2.4 2.4 0 0 1 3 17.6z"/>',
    "check": '<path d="M4.6 12.6l4.9 4.9L19.4 7.5"/>',
    "x": '<path d="M6.2 6.2l11.6 11.6M17.8 6.2L6.2 17.8"/>',
    "alert": '<path d="M10.4 4.2L3 17.6A1.9 1.9 0 0 0 4.6 20.4h14.8a1.9 1.9 0 0 0 1.6-2.8L13.6 4.2a1.9 1.9 0 0 0-3.2 0z"/>'
             '<path d="M12 9.6v4.2M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.2M12 7.8h.01"/>',
}

ALIASES = {"bootloader": "boot", "firmware": "boot", "chip-boot": "boot",
           "freertos": "layers", "zephyr": "wind", "embassy": "orbit",
           "validation": "lab", "flask": "lab"}

_installed = False


def _monochrome(t: soc_theme.Tokens) -> soc_theme.Tokens:
    """Every status colour becomes the accent."""
    return dataclasses.replace(
        t,
        success=t.accent, success_soft=t.accent_soft,
        warning=t.accent, warning_soft=t.accent_soft,
        danger=t.accent, danger_soft=t.accent_soft,
    )


def inline(name: str, color: str = "{c}", stroke: float = 1.8) -> str:
    """A glyph as SVG fragment for registries that format a colour in:
    ``<g ...>`` with ``stroke`` and ``fill`` set, ready for a 24 x 24 viewBox."""
    body = GLYPHS[ALIASES.get(name, name)].replace("currentColor", color)
    return (f'<g fill="none" stroke="{color}" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</g>')


def install() -> None:
    """Apply the one-colour tokens and merge the icon set (once)."""
    global _installed
    if _installed:
        return
    _installed = True
    if os.environ.get("SOC_MONOCHROME") != "0":
        soc_theme.LIGHT = _monochrome(soc_theme.LIGHT)
        soc_theme.DARK = _monochrome(soc_theme.DARK)
    try:
        from . import soc_icons
    except ImportError:      # this app keeps its own icon registry
        return
    soc_icons.GLYPHS.update(GLYPHS)
    soc_icons.ALIASES.update(ALIASES)
    for name in GLYPHS:      # a new glyph beats an older alias of that name
        soc_icons.ALIASES.pop(name, None)
