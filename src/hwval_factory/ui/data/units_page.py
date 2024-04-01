"""The unit registry (units.csv) of the artifacts repository."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hwval_factory.core.units import COLUMNS, STATUSES, Registry, RegistryError, can_move


class UnitsPage(QWidget):
    def __init__(self, state: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.registry: Registry | None = None
        self._build()
        self.reload()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Hint")
        outer.addWidget(self.subtitle)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("serial, id, MAC, UID or batch")
        self.search.textChanged.connect(self._fill)
        row.addWidget(self.search, 1)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload)
        row.addWidget(reload_button)
        outer.addLayout(row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        outer.addWidget(self.table, 1)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Status"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUSES)
        action_row.addWidget(self.status_combo)
        apply_button = QPushButton("Set status")
        apply_button.clicked.connect(self._set_status)
        action_row.addWidget(apply_button)
        action_row.addStretch(1)
        outer.addLayout(action_row)

    def reload(self) -> None:
        path = self.state.workspace.units_path
        try:
            self.registry = Registry.load(path)
        except RegistryError as exc:
            self.subtitle.setText(str(exc))
            self.registry = None
            return
        self.subtitle.setText(f"{len(self.registry.units)} unit(s) · {path}")
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(0)
        if self.registry is None:
            return
        for unit in self.registry.find(self.search.text()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, name in enumerate(COLUMNS):
                item = QTableWidgetItem(str(getattr(unit, name)))
                if name == "serial":
                    item.setData(256, unit.serial)  # Qt.UserRole
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

    def _selected_serial(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        return str(item.data(256)) if item is not None else ""

    def _set_status(self) -> None:
        serial = self._selected_serial()
        if not serial or self.registry is None:
            QMessageBox.information(self, "Units", "Select a unit first.")
            return
        status = self.status_combo.currentText()
        unit = self.registry.units.get(serial)
        if unit is None:
            return
        if not can_move(unit.status, status):
            QMessageBox.warning(self, "Units", f"{serial}: cannot go from {unit.status} to {status}")
            return
        try:
            self.registry.set_status(serial, status, "changed in the factory station")
            self.registry.save()
        except RegistryError as exc:
            QMessageBox.warning(self, "Units", str(exc))
            return
        self._fill()


__all__ = ["UnitsPage"]
