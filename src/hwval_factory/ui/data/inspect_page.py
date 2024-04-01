"""Inspect one .sys/.cal/.diag file: header fields, TLVs and a hex view."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hwval_factory.core import fileformat as ff


class InspectPage(QWidget):
    def __init__(self, state: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        row.addWidget(self.path_edit, 1)
        open_button = QPushButton("Open…")
        open_button.clicked.connect(self._open)
        row.addWidget(open_button)
        outer.addLayout(row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["offset", "size", "field", "value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        outer.addWidget(self.table, 2)

        heading = QLabel("Hex view")
        heading.setObjectName("CardTitle")
        outer.addWidget(heading)
        self.hex_view = QPlainTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setObjectName("Console")
        outer.addWidget(self.hex_view, 1)

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open a factory data file",
            str(self.state.workspace.out_dir_path),
            "Factory data (*.sys *.cal *.diag);;All files (*)",
        )
        if path:
            self.load(path)

    def load(self, path: str | Path) -> None:
        target = Path(path)
        data = target.read_bytes()
        self.path_edit.setText(str(target))
        self.table.setRowCount(0)
        try:
            fields = ff.layout(data)
        except ff.FileFormatError as exc:
            self.hex_view.setPlainText(str(exc))
            return
        for offset, size, name, value in fields:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(f"{offset:#06x}"))
            self.table.setItem(row, 1, QTableWidgetItem(str(size)))
            self.table.setItem(row, 2, QTableWidgetItem(name))
            self.table.setItem(row, 3, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.hex_view.setPlainText(ff.hexdump(data))


__all__ = ["InspectPage"]
