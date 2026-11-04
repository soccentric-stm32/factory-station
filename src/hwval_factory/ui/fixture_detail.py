"""Fixture detail: steps, live tests, OpenOCD log and label preview (UI-011)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import icon
from hostcore.widgets import DataTable, StatusPill, StepList
from hwval_factory.labels import label_data, preview_bytes
from hwval_factory.models import FixtureState
from hwval_factory.station import Station


class FixtureDetailDialog(QDialog):
    start_requested = Signal(str)
    abort_requested = Signal(str)

    def __init__(self, station: Station, fixture: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.station = station
        self.fixture = fixture
        self._run_id: int | None = None
        self._label_serial = ""
        self.setWindowTitle(f"Fixture {fixture}")
        self.resize(1000, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(f"Fixture {fixture}")
        title.setObjectName("Title")
        self.profile_label = QLabel(
            station.profile.display if station.profile is not None else "no profile"
        )
        self.profile_label.setObjectName("Muted")
        self.pill = StatusPill("IDLE")
        self.step_label = QLabel("Ready")
        self.step_label.setObjectName("Muted")
        header.addWidget(title)
        header.addWidget(self.profile_label)
        header.addWidget(self.pill)
        header.addStretch(1)
        header.addWidget(self.step_label)
        self.start_button = QPushButton(icon("play", "white", 18), "Start")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        self.abort_button = QPushButton(icon("stop", "white", 18), "Abort")
        self.abort_button.setObjectName("Danger")
        self.abort_button.clicked.connect(lambda: self.abort_requested.emit(self.fixture))
        header.addWidget(self.start_button)
        header.addWidget(self.abort_button)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.steps_tab = QWidget()
        steps_layout = QVBoxLayout(self.steps_tab)
        self.steps = StepList()
        steps_layout.addWidget(self.steps)
        self.tests = DataTable(
            ["Test", "Result", "Measured", "Unit", "Min", "Max", "Operator", "Reason"]
        )
        steps_layout.addWidget(self.tests, 1)
        self.tabs.addTab(self.steps_tab, "Steps and tests")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("OpenOCD / HWLINK log")
        self.tabs.addTab(self.log, "OpenOCD log")

        self.label_tab = QWidget()
        label_layout = QVBoxLayout(self.label_tab)
        self.label_image = QLabel("The label preview appears after provisioning.")
        self.label_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_image.setMinimumHeight(260)
        label_layout.addWidget(self.label_image)
        self.label_path = QLabel("")
        self.label_path.setObjectName("Muted")
        self.label_path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label_layout.addWidget(self.label_path)
        self.tabs.addTab(self.label_tab, "Label preview")
        root.addWidget(self.tabs, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(700)
        self.refresh()

    def _start(self) -> None:
        self.start_requested.emit(self.fixture)

    def refresh(self) -> None:
        status = self.station.status(self.fixture)
        self.pill.set_state(status.state.value, status.state.value)
        if status.step_name:
            self.step_label.setText(
                f"{status.step_name} — {status.elapsed_s:.1f} s"
            )
        else:
            self.step_label.setText(status.message or "Ready")
        running = status.state in (FixtureState.RUNNING, FixtureState.LOAD)
        self.abort_button.setEnabled(running)
        self.start_button.setEnabled(not running)
        ctx = self.station.worker_ctx(self.fixture)
        if ctx is not None and ctx.run_id != self._run_id:
            self._run_id = ctx.run_id

            profile = self.station.profile
            names = [step.name for step in self.station._build_steps(profile, ctx)] if profile else []
            self.steps.set_steps(names)
        if ctx is not None:
            for step in self.station.db.steps(ctx.run_id):
                self.steps.update_step(
                    int(step["seq"]),
                    str(step["result"]),
                    None if step["duration_ms"] is None else int(step["duration_ms"]),
                )
            rows: list[list[object]] = [
                [
                    test.test_id,
                    test.result,
                    test.measured_value,
                    test.measured_unit,
                    test.limit_min,
                    test.limit_max,
                    test.operator_answer,
                    test.reason,
                ]
                for test in ctx.test_results
            ]
            if not rows:
                # Results from an earlier run (or before this dialog opened).
                rows = [
                    [
                        row["test_id"],
                        row["result"],
                        row["measured_value"],
                        row["measured_unit"],
                        row["limit_min"],
                        row["limit_max"],
                        row["operator_answer"],
                        row["reason"],
                    ]
                    for row in self.station.db.tests(ctx.run_id)
                ]
            self.tests.set_rows(rows)
            if status.serial_number and status.serial_number != self._label_serial:
                self._label_serial = status.serial_number
                self._show_label(ctx)
        log_lines = self.station.run_log(self.fixture)
        text = "\n".join(log_lines)
        if self.log.toPlainText() != text:
            self.log.setPlainText(text)
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _show_label(self, ctx: object) -> None:
        profile = self.station.profile
        if profile is None:
            return
        from hwval_factory.models import RunContext

        if not isinstance(ctx, RunContext):
            return
        data = ctx.label_data or label_data(
            serial_number=ctx.serial_number,
            mac=ctx.mac,
            product=profile.product,
            hw_revision=ctx.hw_revision,
            firmware_version=(
                profile.app_image.image.version_string()
                if profile.app_image.image is not None
                else ""
            ),
            pcb_serial=ctx.pcb_serial,
            uid=ctx.uid,
            work_order=ctx.work_order or "",
            profile=profile.display,
            station=self.station.config.station_id,
            operator=ctx.operator,
        )
        try:
            payload = preview_bytes(profile.label, data)
        except Exception as exc:
            self.label_image.setText(f"Preview unavailable: {exc}")
            return
        pixmap = QPixmap()
        pixmap.loadFromData(payload, b"PNG")
        self.label_image.setPixmap(
            pixmap.scaled(
                860,
                360,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.label_path.setText(
            f"{data.get('serial', '')} — {profile.label.width_mm}x{profile.label.height_mm} mm"
        )

    def closeEvent(self, event: object) -> None:
        self.timer.stop()
        super().closeEvent(event)  # type: ignore[arg-type]
