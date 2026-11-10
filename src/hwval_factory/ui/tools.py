"""Engineer Tools page: run one standalone step per fixture (P1-07).

The page lists the same tools as ``hwval-factory tool …``.  Destructive
actions (erase, protect, recover) need a typed confirmation and the station
role check; every action writes an audit record through ``ToolRunner``.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hwval_factory.models import Role
from hwval_factory.tools import ToolError, ToolRunner

CONFIRM_TOOLS = {"erase", "protect", "recover"}


class _ToolThread(QThread):
    finished_with = Signal(object, str)

    def __init__(self, function: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.function = function

    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:
            self.finished_with.emit(None, f"{type(exc).__name__}: {exc}")
        else:
            self.finished_with.emit(result, "")


class ToolsView(QWidget):
    def __init__(
        self,
        station: Any,
        operator: str,
        role_provider: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.station = station
        self.operator = operator
        self.role_provider = role_provider
        self._thread: _ToolThread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(10)
        title = QLabel("Tools")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        hint = QLabel(
            "Run one step on its own. Erase, protect and recover require a typed "
            "confirmation and a supervisor role."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("Fixture"))
        self.fixture = QComboBox()
        for name in station.fixture_names():
            self.fixture.addItem(name)
        row.addWidget(self.fixture)
        row.addStretch(1)
        root.addLayout(row)

        self.tools = QListWidget()
        for name in ToolRunner.TOOL_ROLES:
            item = QListWidgetItem(name)
            self.tools.addItem(item)
        self.tools.setCurrentRow(0)
        root.addWidget(self.tools, 1)

        confirm_row = QHBoxLayout()
        self.confirm_label = QLabel("Type the tool name to confirm:")
        self.confirm = QLineEdit()
        self.confirm.setPlaceholderText("")
        confirm_row.addWidget(self.confirm_label)
        confirm_row.addWidget(self.confirm, 1)
        root.addLayout(confirm_row)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Run tool")
        self.run_button.clicked.connect(self.run_selected)
        actions.addWidget(self.run_button)
        actions.addStretch(1)
        root.addLayout(actions)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        root.addWidget(self.log, 1)

        self.tools.currentTextChanged.connect(self._tool_changed)
        self._tool_changed(self.tools.currentItem().text() if self.tools.currentItem() else "")
        self.refresh()

    def refresh(self) -> None:
        current = self.fixture.currentText()
        self.fixture.clear()
        for name in self.station.fixture_names():
            self.fixture.addItem(name)
        if current:
            index = self.fixture.findText(current)
            if index >= 0:
                self.fixture.setCurrentIndex(index)

    def _tool_changed(self, name: str) -> None:
        needs = name in CONFIRM_TOOLS
        self.confirm_label.setVisible(needs)
        self.confirm.setVisible(needs)
        self.confirm.setPlaceholderText(name.upper() if needs else "")

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def run_selected(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        item = self.tools.currentItem()
        if item is None:
            return
        name = item.text()
        fixture = self.fixture.currentText()
        if name in CONFIRM_TOOLS and self.confirm.text().strip().upper() != name.upper():
            self._append(f"{name}: type {name.upper()} in the confirmation box first")
            return
        role = self.role_provider()
        if not isinstance(role, Role):
            role = Role.OPERATOR
        try:
            runner = ToolRunner(self.station, self.operator)
            runner.check_role(name, role)
        except ToolError as exc:
            self._append(str(exc))
            return
        tool = name.replace("-", "_")
        self._append(f"--- {name} on {fixture} ---")
        self.run_button.setEnabled(False)

        def _run() -> Any:
            if name == "erase":
                return runner.erase(fixture, confirm=True)
            if name == "recover":
                return runner.recover(fixture, action="catch", confirmed=True)
            if name == "load-app":
                return runner.load_app(fixture)
            if name == "test":
                return runner.test(fixture)
            if name == "identity":
                return runner.identity(fixture, action="read")
            if name == "capture-log":
                return runner.capture_log(fixture, seconds=5)
            return getattr(runner, tool)(fixture)

        self._thread = _ToolThread(_run, self)
        self._thread.finished_with.connect(self._finished)
        self._thread.start()

    def _finished(self, result: Any, error: str) -> None:
        self.run_button.setEnabled(True)
        if error:
            self._append(f"FAIL: {error}")
            return
        self._append(f"{'PASS' if result.ok else 'FAIL'}  {result.detail}")
        if result.data:
            self._append(json.dumps(result.data, indent=2, sort_keys=True, default=str))


__all__ = ["ToolsView"]
