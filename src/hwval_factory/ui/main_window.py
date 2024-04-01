"""Main window of the merged factory station."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hostcore import soc_native, soc_widgets
from hostcore.soc_terminal import TerminalPanel
from hwval_factory import APP_NAME, __version__
from hwval_factory.bench.actions import ACTIONS
from hwval_factory.ui.bench.state import BenchState
from hwval_factory.ui.data.artifacts_bar import ArtifactsBar
from hwval_factory.ui.data.data_page import DataPage
from hwval_factory.ui.data.inspect_page import InspectPage
from hwval_factory.ui.data.units_page import UnitsPage
from hwval_factory.ui.home.home_page import HomePage
from hwval_factory.ui.settings.settings_page import SettingsPage
from hwval_factory.ui.sidebar import SECTION_SUBTITLES, SECTION_TITLES, Sidebar

_PAGE_SHORTCUTS = {
    "home": "Ctrl+Alt+1",
    "sys": "Ctrl+Alt+2",
    "cal": "Ctrl+Alt+3",
    "diag": "Ctrl+Alt+4",
    "inspect": "Ctrl+Alt+5",
    "units": "Ctrl+Alt+6",
    "settings": "Ctrl+Alt+7",
}

_ACTION_SHORTCUTS = {number: f"Ctrl+{number}" for number, _title, _fn in ACTIONS}


class BenchWindow(QMainWindow):
    def __init__(self, data_dir: Path, *, simulate: bool = False) -> None:
        super().__init__()
        soc_native.prepare_window(self)
        self.setWindowTitle(APP_NAME)
        self.resize(1480, 940)
        self.bench = BenchState(data_dir, simulate=simulate)
        self._build_ui()
        self._build_menus()
        self._show_section("home")
        self.bench.refresh_detection()

    # -- UI construction ----------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar(APP_NAME, __version__)
        self.sidebar.section_changed.connect(self._show_section)
        layout.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName("Content")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._header())
        self.artifacts = ArtifactsBar(self.bench)
        column.addWidget(self.artifacts)
        column.addWidget(soc_widgets.hairline())

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")
        self.bench_pages: dict[str, QWidget] = {
            "home": HomePage(self.bench),
            "sys": DataPage("sys", self.bench),
            "cal": DataPage("cal", self.bench),
            "diag": DataPage("diag", self.bench),
            "inspect": InspectPage(self.bench),
            "units": UnitsPage(self.bench),
            "settings": SettingsPage(self.bench),
        }
        for page in self.bench_pages.values():
            self.pages.addWidget(page)
        self.pages.currentChanged.connect(self._page_changed)
        column.addWidget(self.pages, 1)
        layout.addWidget(content, 1)

        self.terminal = TerminalPanel(title="Debug UART")
        self.terminal.setMaximumWidth(460)
        self.terminal.closed.connect(lambda: self.terminal_action.setChecked(False))
        self.terminal.stop("Board port in use")
        self.terminal.refresh_ports(self.bench.port)
        self.bench.changed.connect(self._sync_terminal_port)
        layout.addWidget(self.terminal)
        self.setCentralWidget(root)

    def _sync_terminal_port(self) -> None:
        self.terminal.set_board_port(self.bench.port)

    def _header(self) -> QWidget:
        header = QWidget()
        box = QVBoxLayout(header)
        box.setContentsMargins(24, 14, 24, 10)
        box.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.section_title = soc_widgets.label("", "PageTitle")
        self.section_subtitle = soc_widgets.label("", "PageSubtitle")
        titles.addWidget(self.section_title)
        titles.addWidget(self.section_subtitle)
        row.addLayout(titles, 1)
        self.session_label = soc_widgets.label("", "Hint")
        row.addWidget(self.session_label, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(row)
        box.addSpacing(8)
        box.addWidget(soc_widgets.hairline())
        self.bench.changed.connect(self._sync_session_label)
        return header

    def _sync_session_label(self) -> None:
        unit = self.bench.unit_id or "no unit"
        port = self.bench.port or "no port"
        mode = "simulated" if self.bench.simulate else "hardware"
        self.session_label.setText(f"{unit} · {port} · {mode}")

    # -- menus ---------------------------------------------------------
    def _build_menus(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        for kind in ("sys", "cal", "diag"):
            self._action(
                file_menu,
                f"Open {kind.upper()} CSV…",
                lambda _checked=False, name=kind: self._open_csv(name),
            )
        file_menu.addSeparator()
        self._action(
            file_menu,
            "Open Artifacts Repository…",
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.bench.workspace.resolved_root))
            ),
        )
        file_menu.addSeparator()
        quit_action = self._action(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)

        action_menu = menu.addMenu("&Actions")
        for number, title, _fn in ACTIONS:
            self._action(
                action_menu,
                f"{number}. {title}",
                lambda _checked=False, value=number: self._run_action(value),
                _ACTION_SHORTCUTS[number],
            )
        action_menu.addSeparator()
        self._action(
            action_menu,
            "Run sequence 1 → 8",
            lambda: self._home()._run_sequence(),
            "Ctrl+Shift+R",
        )
        self._action(action_menu, "Refresh detection", self.bench.refresh_detection, "Ctrl+R")

        view = menu.addMenu("&View")
        self.sidebar_action = QAction("Show Sidebar", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(True)
        self.sidebar_action.setShortcut(QKeySequence("Ctrl+Alt+B"))
        self.sidebar_action.toggled.connect(self.sidebar.setVisible)
        view.addAction(self.sidebar_action)
        self.terminal_action = QAction("Debug Terminal", self)
        self.terminal_action.setCheckable(True)
        self.terminal_action.setChecked(True)
        self.terminal_action.setShortcut(QKeySequence("Ctrl+J"))
        self.terminal_action.toggled.connect(self.terminal.setVisible)
        view.addAction(self.terminal_action)
        view.addSeparator()
        for key, title in SECTION_TITLES.items():
            self._action(
                view,
                title,
                lambda _checked=False, name=key: self._show_section(name),
                _PAGE_SHORTCUTS.get(key),
            )

        help_menu = menu.addMenu("&Help")
        self._action(help_menu, "User guide", self.open_guide, "F12")
        about_action = self._action(help_menu, "About", self.about)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)

    def _action(
        self, menu: Any, text: str, callback: Callable[..., Any], shortcut: Any = None
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: callback())
        menu.addAction(action)
        return action

    # -- navigation ---------------------------------------------------
    def _home(self) -> HomePage:
        page = self.bench_pages["home"]
        assert isinstance(page, HomePage)
        return page

    def _open_csv(self, kind: str) -> None:
        self._show_section(kind)
        page = self.bench_pages[kind]
        if isinstance(page, DataPage):
            page._choose_csv()

    def _run_action(self, number: int) -> None:
        self._show_section("home")
        self._home()._start(number)

    def _show_section(self, section: str) -> None:
        page = self.bench_pages.get(section)
        if page is None:
            return
        self.pages.setCurrentWidget(page)
        self.sidebar.set_active(section)
        self.section_title.setText(SECTION_TITLES[section])
        self.section_subtitle.setText(SECTION_SUBTITLES[section])
        self.artifacts.refresh()
        self._sync_session_label()

    def _page_changed(self, index: int) -> None:
        current = self.pages.widget(index)
        for section, page in self.bench_pages.items():
            if current is page:
                self.sidebar.set_active(section)
                self.section_title.setText(SECTION_TITLES[section])
                self.section_subtitle.setText(SECTION_SUBTITLES[section])
                refresh = getattr(page, "on_shown", None)
                if callable(refresh):
                    refresh()
                if isinstance(page, DataPage):
                    page.refresh()

    # -- help ---------------------------------------------------------
    def open_guide(self) -> None:
        candidates = [
            Path("docs/bench-guide.md"),
            Path(__file__).resolve().parents[3] / "docs" / "bench-guide.md",
        ]
        for candidate in candidates:
            if candidate.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate.resolve())))
                return
        QMessageBox.information(
            self,
            "User guide",
            "The user guide is installed under docs/. See README.md for the full guide.",
        )

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "One station for the whole factory flow: detect, load BOOT and the "
            "factory firmware, then write and validate SYS, CAL and DIAG.<br>"
            "FlatBuffers over HWLINK, artifacts tracked in git.<br><br>"
            "© 2026 Sandesh Ghimire — MIT licence.",
        )

    def closeEvent(self, event: Any) -> None:
        self.terminal.stop("closing")
        event.accept()
