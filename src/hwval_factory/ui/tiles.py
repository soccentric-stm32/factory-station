"""Large fixture tiles for the station view (UI-010, UI-002, UI-022)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, STATE_COLORS, icon
from hostcore.widgets import StatusPill
from hwval_factory.models import FixtureState, FixtureStatus


class FixtureTile(QFrame):
    start_requested = Signal(str)
    abort_requested = Signal(str)
    detail_requested = Signal(str)

    def __init__(self, fixture: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fixture = fixture
        self.setObjectName("FixtureTile")
        self.setMinimumSize(300, 250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        top = QHBoxLayout()
        self.name = QLabel(fixture)
        self.name.setObjectName("Title")
        self.pill = StatusPill("IDLE")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.pill)
        root.addLayout(top)

        self.step = QLabel("Ready")
        self.step.setWordWrap(True)
        self.step.setStyleSheet("font-size: 20px; font-weight: 600;")
        root.addWidget(self.step)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        info = QHBoxLayout()
        self.elapsed = QLabel("0.0 s")
        self.elapsed.setObjectName("Muted")
        self.serial = QLabel("—")
        self.serial.setStyleSheet("font-family: monospace; font-size: 17px;")
        self.serial.setAlignment(
            self.serial.alignment() | Qt.AlignmentFlag.AlignRight
        )
        info.addWidget(self.elapsed)
        info.addStretch(1)
        info.addWidget(self.serial)
        root.addLayout(info)

        self.message = QLabel("")
        self.message.setObjectName("Muted")
        self.message.setWordWrap(True)
        root.addWidget(self.message)

        buttons = QHBoxLayout()
        self.primary = QPushButton(icon("play", "white", 18), "Start")
        self.primary.setObjectName("Primary")
        self.primary.setMinimumHeight(42)
        self.primary.clicked.connect(self._primary_clicked)
        self.detail = QPushButton(icon("eye", COLORS["text"], 18), "Detail")
        self.detail.setMinimumHeight(42)
        self.detail.clicked.connect(lambda: self.detail_requested.emit(self.fixture))
        buttons.addWidget(self.primary, 2)
        buttons.addWidget(self.detail, 1)
        root.addLayout(buttons)
        self._status = FixtureStatus(name=fixture)
        self._apply_state()

    def _primary_clicked(self) -> None:
        if self._status.state in (FixtureState.RUNNING, FixtureState.LOAD):
            self.abort_requested.emit(self.fixture)
        else:
            self.start_requested.emit(self.fixture)

    def update_status(self, status: FixtureStatus) -> None:
        self._status = status
        self.pill.set_state(status.state.value, status.state.value)
        self.step.setText(status.step_name or {
            FixtureState.IDLE: "Ready",
            FixtureState.LOAD: "Waiting for scan",
            FixtureState.RUNNING: "Running",
            FixtureState.ACTION: "Operator action",
            FixtureState.PASS: "PASS",
            FixtureState.FAIL: "FAIL",
            FixtureState.ERROR: "Station error",
        }.get(status.state, "Ready"))
        self.progress.setValue(status.progress)
        self.elapsed.setText(f"{status.elapsed_s:.1f} s")
        self.serial.setText(status.serial_number or status.pcb_serial or "—")
        message = status.message
        if status.failed_step:
            message = f"Failed at {status.failed_step}" + (f": {message}" if message else "")
        elif status.state == FixtureState.PASS:
            message = f"PASS — {status.last_result or 'unit complete'}"
        self.message.setText(message)
        self._apply_state()

    def _apply_state(self) -> None:
        state = self._status.state
        color = STATE_COLORS.get(state.value, COLORS["idle"])
        tint = {
            FixtureState.PASS: COLORS["pass_bg"],
            FixtureState.FAIL: COLORS["fail_bg"],
            FixtureState.ERROR: COLORS["warn_bg"],
            FixtureState.ACTION: COLORS["warn_bg"],
            FixtureState.RUNNING: "#F0F7FF",
            FixtureState.LOAD: "#F7FBFF",
        }.get(state, COLORS["surface"])
        self.setStyleSheet(
            f"""
            QFrame#FixtureTile {{
                background: {tint};
                border: 2px solid {color};
                border-radius: 16px;
            }}
            """
        )
        if state in (FixtureState.RUNNING, FixtureState.LOAD):
            self.primary.setText("Abort")
            self.primary.setIcon(icon("stop", "white", 18))
            self.primary.setObjectName("Danger")
        else:
            self.primary.setText("Start")
            self.primary.setIcon(icon("play", "white", 18))
            self.primary.setObjectName("Primary")
        self.primary.style().unpolish(self.primary)
        self.primary.style().polish(self.primary)
        self.progress.setStyleSheet(
            f"QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}"
        )

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.detail_requested.emit(self.fixture)
        super().mouseDoubleClickEvent(event)
