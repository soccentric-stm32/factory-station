"""Main station window, menus and operator interaction (UI-*)."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, icon
from hwval_factory import APP_NAME, __version__
from hwval_factory.auth import AuthService
from hwval_factory.config import StationConfig
from hwval_factory.database import Database
from hwval_factory.models import FixtureState, Role, RunKind
from hwval_factory.profile import ProductProfile, list_profiles
from hwval_factory.reports import certificate_pdf, export_csv, export_json
from hwval_factory.station import Station, StationError
from hwval_factory.ui.dashboard import DashboardView
from hwval_factory.ui.fixture_detail import FixtureDetailDialog
from hwval_factory.ui.history import HistoryView
from hwval_factory.ui.login import LoginDialog, PinChangeDialog, SupervisorDialog
from hwval_factory.ui.supervisor import (
    FixturesDialog,
    OperatorPromptDialog,
    PrinterDialog,
    UsersDialog,
    WorkOrderDialog,
    confirm_rework,
    import_profile_dialog,
)
from hwval_factory.ui.tiles import FixtureTile


class _FunctionThread(QThread):
    finished_with_result = Signal(object, str)

    def __init__(self, function: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.function = function

    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:
            self.finished_with_result.emit(None, f"{type(exc).__name__}: {exc}")
        else:
            self.finished_with_result.emit(result, "")


class ScannerRouter(QObject):
    """Global HID scanner recogniser: fast keystrokes ending in Enter (UI-020)."""

    def __init__(
        self,
        callback: Callable[[str], None],
        parent: QObject | None = None,
        activity: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.callback = callback
        self.activity = activity
        self.buffer = ""
        self.last_time = 0.0
        self.max_gap = 0.08

    def eventFilter(self, obj: QObject, event: Any) -> bool:
        from PySide6.QtCore import QEvent

        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.KeyPress,
        ) and self.activity is not None:
            self.activity()
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            return False
        key = event.key()
        text = event.text()
        now = time.monotonic()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            fast = self.buffer and (now - self.last_time) <= self.max_gap
            if len(self.buffer) >= 4 and fast:
                scanned = self.buffer
                self.buffer = ""
                self.callback(scanned)
                return True
            self.buffer = ""
            return False
        if key == Qt.Key.Key_Escape:
            self.buffer = ""
            return False
        if text and text.isprintable() and not (
            event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        ):
            if self.buffer and (now - self.last_time) > 0.5:
                self.buffer = ""
            self.buffer += text
            self.last_time = now
            if len(self.buffer) > 160:
                self.buffer = self.buffer[-160:]
        return False


class StationWindow(QMainWindow):
    _pass_value: QLabel
    _fail_value: QLabel
    _fpy_value: QLabel

    def __init__(
        self,
        config: StationConfig,
        db: Database,
        auth: AuthService,
        station: Station,
        profile: ProductProfile,
        *,
        kiosk: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.db = db
        self.auth = auth
        self.station = station
        self.profile = profile
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._prompts: queue.Queue[tuple[str, dict[str, Any], dict[str, str], threading.Event]] = (
            queue.Queue()
        )
        self._prompt_active = False
        self._detail: FixtureDetailDialog | None = None
        self._threads: list[_FunctionThread] = []
        self._tick = 0
        self._last_run_id: int | None = None
        self.station.on_event = self._events.put
        self.station.prompt_handler = self.handle_prompt
        self._detected_probes: list[str] = []
        self._probe_thread: _FunctionThread | None = None
        self._scanner = ScannerRouter(
            self._scanned,
            self,
            activity=lambda: (
                self.auth.session.touch() if self.auth.session is not None else None
            ),
        )
        QApplication.instance().installEventFilter(self._scanner)  # type: ignore[union-attr]
        self.setWindowTitle(f"{APP_NAME} — {config.station_name}")
        self.resize(1600, 980)
        self._build_ui()
        self._build_menus()
        self._build_shortcuts()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(400)
        self._probe_timer = QTimer(self)
        self._probe_timer.timeout.connect(self._auto_detect_probes)
        self._probe_timer.start(15000)
        QTimer.singleShot(800, self._auto_detect_probes)
        if kiosk:
            QTimer.singleShot(100, self.toggle_kiosk)

    # -- UI construction ----------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())

        self.tabs = QTabWidget()
        self.tabs.setContentsMargins(14, 12, 14, 10)
        self.tabs.addTab(self._station_page(), "Station")
        self.dashboard = DashboardView(self.db)
        self.tabs.addTab(self.dashboard, "Dashboard")
        self.history = HistoryView(
            self.db,
            certificates_dir=self.config.certificates_dir,
            labels_dir=self.config.labels_dir,
        )
        self.history.reprint_requested.connect(self.reprint_label)
        self.tabs.addTab(self.history, "Unit history")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(
            f"Signed in as {self._operator()} · profile {self.profile.display}"
        )

    def _header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        header.setStyleSheet(
            f"QWidget#Header {{ background: {COLORS['surface']}; "
            f"border-bottom: 1px solid {COLORS['border']}; }}"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(18)
        logo = QLabel()
        logo.setPixmap(icon("logo", COLORS["accent"], 34).pixmap(34, 34))
        names = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 23px; font-weight: 700;")
        self.station_label = QLabel(
            f"{self.config.station_id} · {self.config.station_name}"
        )
        self.station_label.setObjectName("Muted")
        names.addWidget(title)
        names.addWidget(self.station_label)
        layout.addWidget(logo)
        layout.addLayout(names)
        layout.addSpacing(16)
        identity = QVBoxLayout()
        self.profile_label = QLabel(self.profile.display)
        self.profile_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.operator_label = QLabel("")
        self.operator_label.setObjectName("Muted")
        self.work_order_label = QLabel("")
        self.work_order_label.setObjectName("Muted")
        identity.addWidget(self.profile_label)
        identity.addWidget(self.operator_label)
        identity.addWidget(self.work_order_label)
        layout.addLayout(identity)
        layout.addStretch(1)
        counts = QHBoxLayout()
        self.pass_card = self._metric("PASS", "0", COLORS["pass"])
        self.fail_card = self._metric("FAIL", "0", COLORS["fail"])
        self.fpy_card = self._metric("FPY", "0 %", COLORS["accent"])
        counts.addLayout(self.pass_card)
        counts.addLayout(self.fail_card)
        counts.addLayout(self.fpy_card)
        layout.addLayout(counts)
        self.mes_label = QLabel("MES queue 0")
        self.mes_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mes_label.setStyleSheet(
            f"background: {COLORS['surface_alt']}; border: 1px solid {COLORS['border']}; "
            "border-radius: 9px; padding: 8px 12px;"
        )
        layout.addWidget(self.mes_label)
        self.session_label = QLabel("")
        self.session_label.setObjectName("Muted")
        layout.addWidget(self.session_label)
        return header

    def _metric(self, title: str, value: str, color: str) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(0)
        label = QLabel(title)
        label.setObjectName("Muted")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {color};")
        layout.addWidget(label)
        layout.addWidget(value_label)
        setattr(self, f"_{title.lower()}_value", value_label)
        return layout

    def _station_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 8)
        layout.setSpacing(10)
        controls = QHBoxLayout()
        start_all = QPushButton(icon("play", "white", 18), "Start all")
        start_all.setObjectName("Primary")
        start_all.clicked.connect(self.start_all)
        abort_all = QPushButton(icon("stop", "white", 18), "Abort all")
        abort_all.setObjectName("Danger")
        abort_all.clicked.connect(self.station.abort_all)
        detect = QPushButton(icon("refresh", COLORS["text"], 18), "Detect probes")
        detect.clicked.connect(self.detect_probes)
        self.scan_hint = QLabel("Scan a board barcode for the fixture that is waiting.")
        self.scan_hint.setObjectName("Muted")
        controls.addWidget(start_all)
        controls.addWidget(abort_all)
        controls.addWidget(detect)
        controls.addWidget(self.scan_hint, 1)
        layout.addLayout(controls)
        self.probe_label = QLabel("")
        self.probe_label.setObjectName("Muted")
        layout.addWidget(self.probe_label)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {COLORS['warn']};")
        layout.addWidget(self.warning_label)

        self.tiles: dict[str, FixtureTile] = {}
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        fixtures = [f for f in self.config.fixtures if f.enabled]
        columns = 2 if len(fixtures) <= 4 else 4
        for index, fixture in enumerate(fixtures):
            tile = FixtureTile(fixture.name)
            tile.start_requested.connect(self.start_fixture)
            tile.abort_requested.connect(self.station.abort)
            tile.detail_requested.connect(self.show_detail)
            tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tile.customContextMenuRequested.connect(
                lambda pos, name=fixture.name, widget=tile: self._tile_menu(name, widget, pos)
            )
            grid.addWidget(tile, index // columns, index % columns)
            self.tiles[fixture.name] = tile
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_widget)
        layout.addWidget(scroll, 1)
        return page

    def _build_menus(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        self._action(file_menu, "Export CSV…", self.export_csv)
        self._action(file_menu, "Export JSON…", self.export_json)
        self._action(file_menu, "Print certificate…", self.print_certificate)
        file_menu.addSeparator()
        self._action(file_menu, "Quit", self.close, "Ctrl+Q")

        station_menu = menu.addMenu("&Station")
        self._action(station_menu, "Start all", self.start_all, "Ctrl+Shift+S")
        self._action(station_menu, "Abort all", self.station.abort_all, "Ctrl+Shift+A")
        station_menu.addSeparator()
        self._action(station_menu, "Self-test…", self.self_test)
        self._action(station_menu, "Fixtures…", self.show_fixtures)

        wo_menu = menu.addMenu("&Work Order")
        self._action(wo_menu, "Select…", self.select_work_order, "Ctrl+W")
        self._action(wo_menu, "Close", self.close_work_order)

        tools = menu.addMenu("&Tools")
        self._action(tools, "Profiles…", self.show_profiles)
        self._action(tools, "Label printer…", self.show_printer)
        self._action(tools, "Test label print…", self.test_label_print)
        self._action(tools, "Unit history", lambda: self.tabs.setCurrentWidget(self.history))
        tools.addSeparator()
        self._action(tools, "Users…", self.show_users)
        self._action(tools, "Change PIN…", self.change_pin)

        view = menu.addMenu("&View")
        self._action(view, "Station", lambda: self.tabs.setCurrentIndex(0))
        self._action(view, "Dashboard", lambda: self.tabs.setCurrentIndex(1))
        self.kiosk_action = QAction("Kiosk mode", self)
        self.kiosk_action.setCheckable(True)
        self.kiosk_action.triggered.connect(self.toggle_kiosk)
        view.addAction(self.kiosk_action)

        help_menu = menu.addMenu("&Help")
        self._action(help_menu, "User guide", self.open_guide, "F12")
        self._action(help_menu, "About", self.about)

    def _action(
        self,
        menu: QMenu,
        text: str,
        callback: Callable[..., Any],
        shortcut: str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: callback())
        menu.addAction(action)
        return action

    def _build_shortcuts(self) -> None:
        for index, fixture in enumerate([f for f in self.config.fixtures if f.enabled][:8], 1):
            shortcut = QShortcut(QKeySequence(f"F{index}"), self)
            shortcut.activated.connect(
                lambda name=fixture.name: self._fixture_toggle(name)
            )

    # -- operator actions --------------------------------------------
    def _operator(self) -> str:
        return self.auth.session.username if self.auth.session else "not signed in"

    def _fixture_toggle(self, fixture: str) -> None:
        status = self.station.status(fixture)
        if status.state in (FixtureState.RUNNING, FixtureState.LOAD):
            self.station.abort(fixture)
        else:
            self.start_fixture(fixture)

    def _tile_menu(self, fixture: str, widget: QWidget, pos: Any) -> None:
        menu = QMenu(widget)
        menu.addAction("Start (first run)", lambda: self.start_fixture(fixture))
        menu.addAction("Retest…", lambda: self.start_fixture(fixture, RunKind.RETEST))
        menu.addAction("Rework…", lambda: self.start_fixture(fixture, RunKind.REWORK))
        menu.addSeparator()
        menu.addAction("Detail", lambda: self.show_detail(fixture))
        menu.addAction("Abort", lambda: self.station.abort(fixture))
        menu.exec(widget.mapToGlobal(pos))

    def start_all(self) -> None:
        if not self._ensure_work_order():
            return
        try:
            self.station.start_all(operator=self._operator())
        except StationError as exc:
            QMessageBox.warning(self, "Start all", str(exc))

    def start_fixture(self, fixture: str, kind: RunKind = RunKind.FIRST) -> None:
        if not self._ensure_work_order():
            return
        retest_approved = False
        rework_approved = False
        if kind == RunKind.RETEST:
            if not self._supervisor_approval(f"Approve a retest of the board in {fixture}?"):
                return
            retest_approved = True
        elif kind == RunKind.REWORK:
            if not self._supervisor_approval(
                f"Approve rework of the board in {fixture}? RDP may be lowered and "
                "the chip mass-erased."
            ):
                return
            if not confirm_rework(self):
                return
            rework_approved = True
        try:
            self.station.start_run(
                fixture,
                operator=self._operator(),
                kind=kind,
                retest_approved=retest_approved,
                rework_approved=rework_approved,
            )
        except StationError as exc:
            QMessageBox.warning(self, "Fixture", str(exc))

    def _supervisor_approval(self, action: str) -> bool:
        return self._supervisor_approval_user(action) is not None

    def _supervisor_approval_user(self, action: str) -> str | None:
        dialog = SupervisorDialog(self.auth, action, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.approved:
            return None
        return dialog.approved_by or "supervisor"

    def _ensure_work_order(self) -> bool:
        if self.station.work_order is not None:
            return True
        if not self._require_role(Role.SUPERVISOR, "select a work order"):
            return False
        dialog = WorkOrderDialog(
            self.station, self.profile, self, user=self._operator()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if self.station.profile is not None:
            self.profile = self.station.profile
            self.profile_label.setText(self.profile.display)
        self._refresh_work_order()
        return True

    def select_work_order(self) -> None:
        if not self._require_role(Role.SUPERVISOR, "select a work order"):
            return
        dialog = WorkOrderDialog(
            self.station, self.profile, self, user=self._operator()
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.station.profile is not None:
                self.profile = self.station.profile
                self.profile_label.setText(self.profile.display)
            self._refresh_work_order()

    def close_work_order(self) -> None:
        if self.station.work_order is None:
            return
        if not self._supervisor_approval("Close the current work order?"):
            return
        self.station.close_work_order(self._operator())
        self._refresh_work_order()

    def _refresh_work_order(self) -> None:
        wo = self.station.work_order
        if wo is None:
            self.work_order_label.setText("no work order")
        else:
            counts = self.station.serial_service.usage(wo.name)
            self.work_order_label.setText(
                f"WO {wo.name} · {counts['CONSUMED']}/{counts['TOTAL']} serials used"
            )
        self.dashboard.set_work_orders([row["name"] for row in self.db.work_orders()])

    def show_detail(self, fixture: str) -> None:
        if self._detail is not None:
            self._detail.close()
        self._detail = FixtureDetailDialog(self.station, fixture, self)
        self._detail.start_requested.connect(self.start_fixture)
        self._detail.abort_requested.connect(self.station.abort)
        self._detail.show()

    # -- operator prompts from HWLINK ---------------------------------
    def handle_prompt(self, fixture: str, payload: dict[str, Any]) -> str:
        holder: dict[str, str] = {"answer": "OK"}
        event = threading.Event()
        self._prompts.put((fixture, payload, holder, event))
        if not event.wait(timeout=300):
            return "TIMEOUT"
        return holder.get("answer", "OK")

    def _drain_prompts(self) -> None:
        if self._prompt_active or self._prompts.empty():
            return
        fixture, payload, holder, event = self._prompts.get()
        self._prompt_active = True
        try:
            self.station._update_status(
                fixture, state=FixtureState.ACTION, message="operator action"
            )
            dialog = OperatorPromptDialog(fixture, payload, self)
            dialog.exec()
            holder["answer"] = dialog.answer
        finally:
            self.station._update_status(fixture, state=FixtureState.RUNNING, message="")
            self._prompt_active = False
            event.set()

    # -- scanner and events ------------------------------------------
    def _scanned(self, text: str) -> None:
        text = text.strip()
        if text.upper().startswith("WO:"):
            name = text[3:].strip()
            if not name:
                return
            row = self.db.work_order(name)
            if row is None:
                self.statusBar().showMessage(
                    f"Work order {name} is not known; select it in Work Order → Select."
                )
                return
            if not self._require_role(Role.SUPERVISOR, "scan a work order"):
                return
            try:
                self.station.select_work_order(
                    name,
                    profile_id=str(row["profile_id"]),
                    quantity=int(row["quantity"]),
                    serial_prefix=str(row["serial_prefix"]),
                    serial_start=int(row["serial_start"]),
                    serial_end=int(row["serial_end"]),
                    mac_start=str(row["mac_start"]),
                    mac_count=int(row["mac_count"]),
                    user=self._operator(),
                )
            except Exception as exc:
                QMessageBox.warning(self, "Work order", str(exc))
                return
            if self.station.profile is not None:
                self.profile = self.station.profile
                self.profile_label.setText(self.profile.display)
            self._refresh_work_order()
            self.statusBar().showMessage(f"Work order {name} selected from barcode")
            return
        routed = self.station.submit_scan(text)
        if routed:
            self.statusBar().showMessage(f"Scanned {text} for fixture {routed}")
        else:
            self.statusBar().showMessage(f"Scan {text} ignored: no fixture waiting")

    def _on_tick(self) -> None:
        self._tick += 1
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self._drain_prompts()
        self._refresh_status()
        if self._tick % 12 == 0:
            self._refresh_warnings()

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "run_finished":
            result = event.get("result", "")
            if result == "PASS":
                QApplication.beep()
                self.statusBar().showMessage(
                    f"Fixture {event.get('fixture')}: PASS — {event.get('serial', '')}"
                )
            else:
                for _ in range(3):
                    QApplication.beep()
                self.statusBar().showMessage(
                    f"Fixture {event.get('fixture')}: {result} — "
                    f"{event.get('failed_step') or ''} {event.get('reason') or ''}"
                )
            self._last_run_id = event.get("run_id")
            if self.tabs.currentWidget() is self.history:
                self.history.refresh()
            if self.tabs.currentWidget() is self.dashboard:
                self.dashboard.refresh()
        elif kind == "maintenance":
            self.statusBar().showMessage(str(event.get("message", "maintenance due")))
        elif kind == "self_test":
            self.statusBar().showMessage(
                f"Self-test {event.get('fixture')}: {event.get('result')} {event.get('detail', '')}"
            )
        elif kind == "mes_queue":
            self.mes_label.setText(f"MES queue {event.get('length', 0)}")
        elif kind == "run_started":
            self.statusBar().showMessage(
                f"Fixture {event.get('fixture')}: run {event.get('run_id')} started"
            )
        elif kind == "work_order" or kind == "work_order_closed":
            self._refresh_work_order()

    def _refresh_status(self) -> None:
        for name, tile in self.tiles.items():
            tile.update_status(self.station.status(name))
            if self._detail is not None and self._detail.fixture == name:
                self._detail.refresh()
        self.operator_label.setText(f"Operator: {self._operator()}")
        if self.auth.session is not None:
            role = self.auth.session.role.value
            remaining = ""
            self.session_label.setText(f"{role}{remaining}")
        wo = self.station.work_order
        if wo is not None:
            counts = self.station.serial_service.usage(wo.name)
            self.work_order_label.setText(
                f"WO {wo.name} · {counts['CONSUMED']}/{counts['TOTAL']} serials used"
            )
        stats = self.db.yield_stats(work_order=wo.name if wo else "")
        self._pass_value.setText(str(stats["passed"]))
        self._fail_value.setText(str(stats["failed"]))
        self._fpy_value.setText(f"{stats['fpy']:.0f} %")
        self.mes_label.setText(f"MES queue {self.station.mes_queue_length()}")
        if self.auth.session is not None and self.auth.lock_if_idle(
            self.config.session_timeout_minutes
        ):
            self._relogin()

    def _refresh_warnings(self) -> None:
        warnings = self.station.self_test_warnings() + self.station.maintenance_warnings()
        wo = self.station.work_order
        if wo is not None:
            serial_warning = self.station.serial_service.warning(wo.name)
            if serial_warning:
                warnings.append(serial_warning)
        self.warning_label.setText(" · ".join(warnings))
        self.warning_label.setVisible(bool(warnings))
        if self._detected_probes:
            configured = {f.probe_serial for f in self.config.fixtures if f.probe_serial}
            missing = sorted(configured - set(self._detected_probes))
            extra = sorted(set(self._detected_probes) - configured)
            parts = [f"Probes detected: {len(self._detected_probes)}"]
            if missing:
                parts.append("missing " + ", ".join(missing))
            if extra:
                parts.append("unconfigured " + ", ".join(extra))
            self.probe_label.setText(" · ".join(parts))
        else:
            self.probe_label.setText("No ST-LINK probes detected")

    def _relogin(self) -> None:
        dialog = LoginDialog(self.auth, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.close()
            return
        self.statusBar().showMessage(f"Signed in as {self._operator()}")

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.dashboard:
            self.dashboard.set_work_orders([row["name"] for row in self.db.work_orders()])
            self.dashboard.refresh()
        elif self.tabs.widget(index) is self.history:
            self.history.refresh()

    # -- menus --------------------------------------------------------
    def _run_background(
        self, title: str, function: Callable[[], Any], on_success: Callable[[Any], Any]
    ) -> None:
        thread = _FunctionThread(function, self)

        def _done(result: Any, error: str) -> None:
            self._threads.remove(thread)
            if error:
                QMessageBox.critical(self, title, error)
                return
            on_success(result)

        thread.finished_with_result.connect(_done)
        self._threads.append(thread)
        thread.start()

    def export_csv(self) -> None:
        work_order = self.station.work_order.name if self.station.work_order else ""
        default = f"runs-{work_order or 'all'}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV (*.csv)")
        if not path:
            return
        self._run_background(
            "Export",
            lambda: export_csv(self.db, path, work_order=work_order),
            lambda written: QMessageBox.information(self, "Export", f"Written to {written}"),
        )

    def export_json(self) -> None:
        work_order = self.station.work_order.name if self.station.work_order else ""
        default = f"runs-{work_order or 'all'}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", default, "JSON (*.json)")
        if not path:
            return
        self._run_background(
            "Export",
            lambda: export_json(self.db, path, work_order=work_order),
            lambda written: QMessageBox.information(self, "Export", f"Written to {written}"),
        )

    def print_certificate(self) -> None:
        run_id = self._selected_run()
        if run_id is None:
            return
        self.config.certificates_dir.mkdir(parents=True, exist_ok=True)
        default = str(self.config.certificates_dir / f"certificate-{run_id}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Certificate of test", default, "PDF (*.pdf)")
        if not path:
            return
        self._run_background(
            "Certificate",
            lambda: certificate_pdf(self.db, run_id, path),
            lambda written: QMessageBox.information(
                self, "Certificate", f"Written to {written}"
            ),
        )

    def _selected_run(self) -> int | None:
        selected = self.history.selected_run()
        if selected is not None:
            return selected
        if self._last_run_id is not None:
            return self._last_run_id
        run_id, ok = QInputDialog.getInt(self, "Run", "Run ID:", 1, 1, 10_000_000)
        return run_id if ok else None

    def reprint_label(self, run_id: int) -> None:
        approved_by = self._supervisor_approval_user(
            f"Approve reprint of the label for run {run_id}?"
        )
        if approved_by is None:
            return
        user = f"{self._operator()} (approved by {approved_by})"
        thread = _FunctionThread(
            lambda: self.station.label_service.reprint(run_id=run_id, user=user), self
        )

        def _done(result: Any, error: str) -> None:
            self._threads.remove(thread)
            if error:
                QMessageBox.critical(self, "Reprint", error)
                return
            self.db.audit(user, "label.reprint", f"run {run_id} {result.status}")
            QMessageBox.information(
                self,
                "Reprint",
                result.status
                + (
                    "\n" + "\n".join(str(p) for p in result.output_paths)
                    if result.output_paths
                    else ""
                ),
            )

        thread.finished_with_result.connect(_done)
        self._threads.append(thread)
        thread.start()

    def show_fixtures(self) -> None:
        if not self._require_role(Role.ENGINEER, "edit fixtures"):
            return
        FixturesDialog(self.station, self, user=self._operator()).exec()

    def show_profiles(self) -> None:
        profiles = list_profiles(
            self.config.profiles_dir,
            require_signature=self.config.require_profile_signature,
            public_key=self.config.profile_public_key or None,
        )
        choices = [f"{p.profile_id}@{p.version} — {p.product}" for p in profiles]
        choices.append("Import a profile…")
        choice, ok = QInputDialog.getItem(
            self, "Product profiles", "Active profile:", choices, 0, False
        )
        if not ok:
            return
        if choice.startswith("Import"):
            if not self._require_role(Role.ENGINEER, "import a product profile"):
                return
            imported = import_profile_dialog(
                self.station,
                self,
                require_signature=self.config.require_profile_signature,
                user=self._operator(),
            )
            if imported is not None:
                self.profile = imported
                self.profile_label.setText(imported.display)
            return
        index = choices.index(choice)
        if index < 0 or index >= len(profiles):
            return
        if not self._require_role(Role.ENGINEER, "select a product profile"):
            return
        self.station.set_profile(profiles[index])
        self.profile = profiles[index]
        self.profile_label.setText(self.profile.display)
        self.db.audit(self._operator(), "profile.select", self.profile.display)

    def show_printer(self) -> None:
        if not self._require_role(Role.ENGINEER, "edit the label printer"):
            return
        PrinterDialog(self.station, self, user=self._operator()).exec()

    def test_label_print(self) -> None:
        if not self._require_role(Role.SUPERVISOR, "test-print a label"):
            return
        profile = self.station.profile
        if profile is None:
            QMessageBox.warning(self, "Label", "No profile selected.")
            return
        user = self._operator()
        thread = _FunctionThread(
            lambda: self.station.label_service.test_print(profile, user), self
        )

        def _done(result: Any, error: str) -> None:
            self._threads.remove(thread)
            if error:
                QMessageBox.critical(self, "Test label", error)
                return
            message = result.status
            if result.error:
                message += f"\n{result.error}"
            if result.output_paths:
                message += "\n" + "\n".join(str(p) for p in result.output_paths)
            QMessageBox.information(self, "Test label", message)

        thread.finished_with_result.connect(_done)
        self._threads.append(thread)
        thread.start()

    def show_users(self) -> None:
        if not self._require_role(Role.ADMIN, "manage users"):
            return
        UsersDialog(self.auth, self).exec()

    def change_pin(self) -> None:
        if self.auth.session is None:
            return
        dialog = PinChangeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pin = dialog.value()
        if not pin:
            return
        self.auth.set_pin(self.auth.session.username, pin)
        self.auth.db.audit(self.auth.session.username, "user.pin", "self-service PIN change")
        QMessageBox.information(self, "PIN", "PIN changed.")

    def _require_role(self, role: Role, action: str) -> bool:
        if self.auth.session is not None and self.auth.session.role.rank >= role.rank:
            return True
        QMessageBox.warning(
            self, "Permission", f"{role.value.title()} permission is required to {action}."
        )
        return False

    def self_test(self) -> None:
        if not self._require_role(Role.SUPERVISOR, "run a station self-test"):
            return
        choices = ["All fixtures", *self.station.fixture_names()]
        choice, ok = QInputDialog.getItem(
            self, "Self-test", "Fixture:", choices, 0, False
        )
        if not ok:
            return
        names = self.station.fixture_names() if choice == "All fixtures" else [choice]
        self.statusBar().showMessage(f"Self-test of {', '.join(names)} running…")
        user = self._operator()
        thread = _FunctionThread(
            lambda: {
                name: self.station.run_self_test(name, user) for name in names
            },
            self,
        )

        def _done(result: Any, error: str) -> None:
            self._threads.remove(thread)
            if error:
                QMessageBox.critical(self, "Self-test", error)
            else:
                lines = [
                    f"{name}: {'PASS' if passed else 'FAIL'}"
                    for name, passed in (result or {}).items()
                ]
                QMessageBox.information(self, "Self-test", "\n".join(lines))
            self._refresh_warnings()

        thread.finished_with_result.connect(_done)
        self._threads.append(thread)
        thread.start()

    def detect_probes(self) -> None:
        self._start_probe_detection(manual=True)

    def _auto_detect_probes(self) -> None:
        self._start_probe_detection(manual=False)

    def _start_probe_detection(self, *, manual: bool) -> None:
        if self._probe_thread is not None and self._probe_thread.isRunning():
            return
        self.statusBar().showMessage("Detecting ST-LINK probes…")
        thread = _FunctionThread(self.station.detect_probes, self)
        self._probe_thread = thread

        def _done(result: Any, error: str) -> None:
            self._threads.remove(thread)
            self._probe_thread = None
            if error:
                if manual:
                    QMessageBox.critical(self, "Probes", error)
                return
            probes = list(result or [])
            self._detected_probes = probes
            missing = self.station.missing_probes(probes)
            unconfigured = [
                p for p in probes if p not in {f.probe_serial for f in self.config.fixtures}
            ]
            if manual:
                text = f"Detected: {', '.join(probes) or 'none'}"
                if missing:
                    text += f"\nMissing configured probes: {', '.join(missing)}"
                if unconfigured:
                    text += f"\nUnconfigured probes: {', '.join(unconfigured)}"
                QMessageBox.information(self, "Probes", text)
            if missing or unconfigured:
                self.statusBar().showMessage(
                    "Probe warning: "
                    + ("missing " + ", ".join(missing) if missing else "")
                    + ("; unconfigured " + ", ".join(unconfigured) if unconfigured else "")
                )

        thread.finished_with_result.connect(_done)
        self._threads.append(thread)
        thread.start()

    def toggle_kiosk(self, _checked: bool = False) -> None:
        kiosk = self.kiosk_action.isChecked()
        self.menuBar().setVisible(not kiosk)
        if kiosk:
            self.showFullScreen()
        else:
            self.showNormal()

    def open_guide(self) -> None:
        candidates = [
            Path("docs/operator-guide.md"),
            Path(__file__).resolve().parents[3] / "docs" / "operator-guide.md",
        ]
        for candidate in candidates:
            if candidate.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate.resolve())))
                return
        QMessageBox.information(
            self,
            "User guide",
            "The operator guide is installed under docs/. See README.md for the full guide.",
        )

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "End-of-line test and provisioning for STM32F767ZI boards.<br>"
            "Shared host core: soccentric-stm32-hostcore.<br><br>"
            "© 2026 Sandesh Ghimire — MIT licence.",
        )

    # -- shutdown ------------------------------------------------------
    def closeEvent(self, event: Any) -> None:
        running = [
            name
            for name in self.station.fixture_names()
            if self.station.status(name).state in (FixtureState.RUNNING, FixtureState.LOAD)
        ]
        if running:
            answer = QMessageBox.question(
                self,
                "Quit",
                "Fixtures " + ", ".join(running) + " are running. Abort them and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        QApplication.instance().removeEventFilter(self._scanner)  # type: ignore[union-attr]
        self._timer.stop()
        self._probe_timer.stop()
        for thread in list(self._threads):
            thread.wait(2000)
        self.station.close()
        self.db.close()
        event.accept()
