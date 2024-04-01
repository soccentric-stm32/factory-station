"""Icons for the bench pages: the shared SoCcentric set (``hostcore.soc_icons``).

All colours, fonts and style rules come from ``hostcore.soc_theme``.
"""

from __future__ import annotations

from hostcore import soc_icons
from hostcore.theme import icon


def bench_icon_names() -> list[str]:
    return soc_icons.names()


__all__ = ["bench_icon_names", "icon"]
