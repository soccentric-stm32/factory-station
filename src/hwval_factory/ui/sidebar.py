"""Sidebar listing the bench pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS
from hwval_factory.ui.bench.style import icon

_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("detect", "Detect", "probe"),
    ("bootloader", "Bootloader", "flash"),
    ("firmware", "Factory firmware", "firmware"),
    ("device", "Device info", "info"),
    ("sysinfo", "System info", "list"),
    ("calibration", "Calibration", "sliders"),
    ("diagnostics", "Diagnostics", "pulse"),
    ("validation", "Validation", "check"),
)

SECTION_TITLES: dict[str, str] = {key: label for key, label, _icon in _ITEMS}


class Sidebar(QFrame):
    """Narrow translucent sidebar with one button per bench page."""

    section_changed = Signal(str)

    def __init__(self, app_name: str, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(238)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 16, 14, 12)
        root.setSpacing(8)

        root.addWidget(self._brand(app_name))
        root.addSpacing(8)

        self._nav: dict[str, tuple[QPushButton, str]] = {}
        for key, label, icon_name in _ITEMS:
            root.addWidget(self._nav_button(key, label, icon_name))
        root.addStretch(1)

        footer = QLabel(f"Version {version}")
        footer.setObjectName("SidebarVersion")
        root.addWidget(footer)
        self.set_active(_ITEMS[0][0])

    def _brand(self, app_name: str) -> QWidget:
        brand = QWidget()
        row = QHBoxLayout(brand)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icon("logo", COLORS["accent"], 30).pixmap(30, 30))
        logo.setFixedSize(30, 30)
        title = QLabel(app_name)
        title.setObjectName("SidebarTitle")
        row.addWidget(logo)
        row.addWidget(title, 1)
        return brand

    def _nav_button(self, key: str, label: str, icon_name: str) -> QPushButton:
        button = QPushButton(icon(icon_name, COLORS["text"], 18), f"  {label}")
        button.setObjectName("SidebarItem")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, name=key: self.section_changed.emit(name))
        self._nav[key] = (button, icon_name)
        return button

    def set_active(self, section: str) -> None:
        for key, (button, icon_name) in self._nav.items():
            selected = key == section
            button.setChecked(selected)
            colour = "#FFFFFF" if selected else COLORS["text"]
            button.setIcon(icon(icon_name, colour, 18))


__all__ = ["SECTION_TITLES", "Sidebar"]
