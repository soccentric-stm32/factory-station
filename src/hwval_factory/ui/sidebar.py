"""macOS-style navigation sidebar for the station window."""

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

from hostcore.theme import COLORS, icon

_NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("station", "Station", "dashboard"),
    ("dashboard", "Dashboard", "chart"),
    ("history", "Unit history", "history"),
)

_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("start_all", "Start all", "play"),
    ("abort_all", "Abort all", "stop"),
    ("select_wo", "Work order", "board"),
    ("self_test", "Self-test", "check"),
    ("profiles", "Profiles", "settings"),
    ("printer", "Label printer", "printer"),
)


class Sidebar(QFrame):
    """Narrow translucent sidebar with workspace navigation and quick actions."""

    section_changed = Signal(str)
    action_requested = Signal(str)

    def __init__(
        self,
        app_name: str,
        station_name: str,
        station_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(238)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 16, 14, 12)
        root.setSpacing(8)

        root.addWidget(self._brand(app_name, station_name, station_id))
        root.addSpacing(8)

        root.addWidget(self._section_label("WORKSPACE"))
        self._nav: dict[str, tuple[QPushButton, str]] = {}
        for key, label, icon_name in _NAV_ITEMS:
            button = self._nav_button(key, label, icon_name)
            root.addWidget(button)

        root.addSpacing(10)
        root.addWidget(self._section_label("ACTIONS"))
        for key, label, icon_name in _ACTIONS:
            root.addWidget(self._action_button(key, label, icon_name))

        root.addStretch(1)
        root.addWidget(self._footer())
        self.set_active("station")

    # -- construction -------------------------------------------------
    def _brand(self, app_name: str, station_name: str, station_id: str) -> QWidget:
        brand = QWidget()
        row = QHBoxLayout(brand)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icon("logo", COLORS["accent"], 30).pixmap(30, 30))
        logo.setFixedSize(30, 30)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel(app_name)
        title.setObjectName("SidebarTitle")
        station = QLabel(f"{station_id} · {station_name}")
        station.setObjectName("SidebarMuted")
        station.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(station)
        row.addWidget(logo)
        row.addLayout(titles, 1)
        return brand

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SidebarSection")
        return label

    def _nav_button(self, key: str, label: str, icon_name: str) -> QPushButton:
        button = QPushButton(icon(icon_name, COLORS["text"], 18), f"  {label}")
        button.setObjectName("SidebarItem")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, name=key: self.section_changed.emit(name))
        self._nav[key] = (button, icon_name)
        return button

    def _action_button(self, key: str, label: str, icon_name: str) -> QPushButton:
        button = QPushButton(icon(icon_name, COLORS["muted"], 16), f"  {label}")
        button.setObjectName("SidebarAction")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, name=key: self.action_requested.emit(name))
        return button

    def _footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(4, 10, 4, 0)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(9)
        self._avatar = QLabel("?")
        self._avatar.setObjectName("SidebarAvatar")
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setFixedSize(34, 34)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._user = QLabel("")
        self._user.setObjectName("SidebarUser")
        self._role = QLabel("")
        self._role.setObjectName("SidebarMuted")
        names.addWidget(self._user)
        names.addWidget(self._role)
        row.addWidget(self._avatar)
        row.addLayout(names, 1)
        layout.addLayout(row)
        self._mes = QLabel("")
        self._mes.setObjectName("SidebarBadge")
        self._mes.setVisible(False)
        layout.addWidget(self._mes)
        version = QLabel("")
        version.setObjectName("SidebarVersion")
        self._version = version
        layout.addWidget(version)
        return footer

    # -- state --------------------------------------------------------
    def set_active(self, section: str) -> None:
        for key, (button, icon_name) in self._nav.items():
            selected = key == section
            button.setChecked(selected)
            colour = COLORS["accent"] if selected else COLORS["text"]
            button.setIcon(icon(icon_name, colour, 18))

    def set_user(self, username: str, role: str) -> None:
        initials = "".join(part[:1] for part in username.replace("_", " ").split()[:2]).upper()
        self._avatar.setText(initials or "?")
        self._user.setText(username or "Not signed in")
        self._role.setText(role.replace("_", " ").title() if role else "No role")

    def set_mes_queue(self, length: int) -> None:
        self._mes.setText(f"MES queue · {length}")
        self._mes.setVisible(length > 0)

    def set_version(self, version: str) -> None:
        self._version.setText(f"Version {version}")


__all__ = ["Sidebar"]
