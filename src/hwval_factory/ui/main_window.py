"""Main window: a sidebar of bench pages and a stacked page area."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hostcore.soc_terminal import TerminalPanel
from hwval_factory import APP_NAME, __version__
from hwval_factory.ui.bench.pages_connect import (
    BootloaderPage,
    DetectPage,
    DeviceInfoPage,
    FirmwarePage,
)
from hwval_factory.ui.bench.pages_data import CalibrationPage, DiagnosticsPage, SysInfoPage
from hwval_factory.ui.bench.pages_validation import ValidationPage
from hwval_factory.ui.bench.state import BenchState
from hwval_factory.ui.sidebar import SECTION_TITLES, Sidebar

_SHORTCUTS = {
    "detect": "Ctrl+1",
    "bootloader": "Ctrl+2",
    "firmware": "Ctrl+3",
    "device": "Ctrl+4",
    "sysinfo": "Ctrl+5",
    "calibration": "Ctrl+6",
    "diagnostics": "Ctrl+7",
    "validation": "Ctrl+8",
}


class BenchWindow(QMainWindow):
    def __init__(self, data_dir: Path, *, simulate: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.bench = BenchState(data_dir, simulate=simulate)
        self._build_ui()
        self._build_menus()
        self._show_section("detect")

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

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")
        self.bench_pages = {
            "detect": DetectPage(self.bench),
            "bootloader": BootloaderPage(self.bench),
            "firmware": FirmwarePage(self.bench),
            "device": DeviceInfoPage(self.bench),
            "sysinfo": SysInfoPage(self.bench),
            "calibration": CalibrationPage(self.bench),
            "diagnostics": DiagnosticsPage(self.bench),
            "validation": ValidationPage(self.bench),
        }
        for page in self.bench_pages.values():
            self.pages.addWidget(page)
            self.bench.changed.connect(page.on_state_changed)
        self.pages.currentChanged.connect(self._page_changed)
        column.addWidget(self.pages, 1)
        layout.addWidget(content, 1)

        # Debug UART terminal on the right, as in the BOOT flasher.
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
        header = QFrame()
        header.setObjectName("Toolbar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 12, 22, 12)
        self.section_title = QLabel("")
        self.section_title.setObjectName("ToolbarTitle")
        layout.addWidget(self.section_title)
        layout.addStretch(1)
        return header

    def _build_menus(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        self._action(
            file_menu,
            "Open Factory Database…",
            lambda: self._open_file("sysinfo", lambda page: page.csv_row.choose()),
            "Ctrl+O",
        )
        self._action(
            file_menu,
            "Open Calibration File…",
            lambda: self._open_file("calibration", lambda page: page.file_row.choose()),
            "Ctrl+Shift+O",
        )
        self._action(
            file_menu,
            "Open Diagnostic File…",
            lambda: self._open_file("diagnostics", lambda page: page.file_row.choose()),
            "Ctrl+Alt+O",
        )
        file_menu.addSeparator()
        quit_action = self._action(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)

        edit_menu = menu.addMenu("&Edit")
        for text, method, key in (
            ("Undo", "undo", QKeySequence.StandardKey.Undo),
            ("Redo", "redo", QKeySequence.StandardKey.Redo),
            (None, None, None),
            ("Cut", "cut", QKeySequence.StandardKey.Cut),
            ("Copy", "copy", QKeySequence.StandardKey.Copy),
            ("Paste", "paste", QKeySequence.StandardKey.Paste),
            ("Select All", "selectAll", QKeySequence.StandardKey.SelectAll),
        ):
            if text is None:
                edit_menu.addSeparator()
                continue
            self._action(edit_menu, text, lambda name=method: self._route_edit_action(name), key)

        bench_menu = menu.addMenu("&Bench")
        for section, title in SECTION_TITLES.items():
            self._action(
                bench_menu,
                title,
                lambda _checked=False, name=section: self._show_section(name),
                _SHORTCUTS.get(section),
            )
        bench_menu.addSeparator()
        self._action(bench_menu, "Refresh Detection", self.bench.refresh_detection, "Ctrl+R")
        self.simulate_action = QAction("Simulated Board", self)
        self.simulate_action.setCheckable(True)
        self.simulate_action.setChecked(self.bench.simulate)
        self.simulate_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.simulate_action.triggered.connect(self.bench.set_simulate)
        bench_menu.addAction(self.simulate_action)
        self.bench.changed.connect(lambda: self.simulate_action.setChecked(self.bench.simulate))

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

        window_menu = menu.addMenu("&Window")
        self._action(window_menu, "Minimize", self.showMinimized, "Ctrl+M")
        self._action(
            window_menu,
            "Zoom",
            lambda: self.showNormal() if self.isMaximized() else self.showMaximized(),
        )

        help_menu = menu.addMenu("&Help")
        self._action(help_menu, "User guide", self.open_guide, "F12")
        about_action = self._action(help_menu, "About", self.about)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)

    def _action(
        self,
        menu: Any,
        text: str,
        callback: Callable[..., Any],
        shortcut: Any = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: callback())
        menu.addAction(action)
        return action

    def _route_edit_action(self, method_name: str | None) -> None:
        """Edit-menu commands go to whichever widget has the keyboard focus."""
        widget = QApplication.focusWidget()
        method = getattr(widget, method_name or "", None)
        if callable(method):
            method()

    def _open_file(self, section: str, choose: Callable[[Any], None]) -> None:
        self._show_section(section)
        choose(self.bench_pages[section])

    # -- navigation ---------------------------------------------------
    def _show_section(self, section: str) -> None:
        page = self.bench_pages.get(section)
        if page is None:
            return
        self.pages.setCurrentWidget(page)
        self._sync_header(section)

    def _page_changed(self, index: int) -> None:
        current = self.pages.widget(index)
        for section, page in self.bench_pages.items():
            if current is page:
                self._sync_header(section)
                page.on_shown()

    def _sync_header(self, section: str) -> None:
        self.sidebar.set_active(section)
        self.section_title.setText(SECTION_TITLES[section])

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
            "Bench tool for STM32F767ZI boards: detect, flash the bootloader and "
            "factory firmware, and write device data.<br>"
            "Shared host core: soccentric-stm32-hostcore.<br><br>"
            "© 2026 Sandesh Ghimire — MIT licence.",
        )

    def closeEvent(self, event: Any) -> None:
        event.accept()
