"""Sidebar for the merged factory station.

Vertical sections stay as requested: Home, SYS, CAL, DIAG, Inspect, Units,
Settings.  The horizontal tab strip lives inside each data page.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from hostcore import soc_native, soc_widgets

NavItem = soc_native.fluent_nav(soc_widgets.NavItem)

APP_GLYPH = "logo"

# key, title, subtitle, glyph
_ITEMS: tuple[tuple[str, str, str, str], ...] = (
    ("home", "Home", "Eight bench actions", "probe"),
    ("sys", "SYS", "System info CSV and .sys", "list"),
    ("cal", "CAL", "Calibration CSV and .cal", "sliders"),
    ("diag", "DIAG", "Diagnostics CSV and .diag", "pulse"),
    ("inspect", "Inspect", "Read a memory file", "file"),
    ("units", "Units", "The unit registry", "test"),
    ("settings", "Settings", "Artifacts and images", "settings"),
)

SECTION_TITLES: dict[str, str] = {key: title for key, title, _sub, _icon in _ITEMS}
SECTION_SUBTITLES: dict[str, str] = {key: sub for key, _title, sub, _icon in _ITEMS}


class Sidebar(QWidget):
    """Sidebar with the app header, one nav item per section and the version."""

    section_changed = Signal(str)

    def __init__(self, app_name: str, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(soc_widgets.SIDEBAR_WIDTH)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 16, 12, 12)
        column.setSpacing(2)

        column.addWidget(soc_widgets.SidebarHeader(app_name, "STM32F767ZI bench", APP_GLYPH))
        column.addSpacing(24)
        column.addWidget(soc_widgets.section("FACTORY"))

        self._nav: dict[str, tuple[NavItem, str]] = {}
        for key, title, subtitle, glyph in _ITEMS:
            item = NavItem(glyph, title, subtitle, show_status=False)
            item.clicked.connect(lambda _checked=False, name=key: self.section_changed.emit(name))
            self._nav[key] = (item, glyph)
            column.addWidget(item)
        column.addStretch(1)
        column.addWidget(soc_widgets.version_label(version))
        self.set_active(_ITEMS[0][0])

    def set_active(self, section: str) -> None:
        for key, (item, _glyph) in self._nav.items():
            item.setChecked(key == section)


__all__ = ["SECTION_SUBTITLES", "SECTION_TITLES", "Sidebar"]
