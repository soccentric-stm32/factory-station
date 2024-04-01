"""CSV table model backed by the same loader the generator uses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from hwval_factory.core.generators import Dataset, Generator, Issue


class CsvTableModel(QAbstractTableModel):
    def __init__(self, gen: Generator, parent=None) -> None:
        super().__init__(parent)
        self.gen = gen
        self.dataset: Dataset | None = None
        self.path: Path | None = None
        self.issues: list[Issue] = []
        self._problems: dict[int, list[str]] = {}
        self._rows: list[int] = []
        self._filter = ""

    # -- loading -------------------------------------------------------
    def load(self, path: str | Path) -> list[Issue]:
        self.path = Path(path)
        self.dataset = self.gen.load(self.path)
        self.issues = self.gen.validate(self.dataset)
        self._problems = {}
        for issue in self.issues:
            if issue.severity == "error":
                self._problems.setdefault(issue.line, []).append(issue.message)
        self._apply_filter()
        return self.issues

    def set_filter(self, text: str) -> None:
        self._filter = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self) -> None:
        self.beginResetModel()
        self._rows = []
        if self.dataset is not None:
            for index, row in enumerate(self.dataset.rows):
                haystack = " ".join(
                    str(row.get(name, "")) for name in ("id", "serial", "mac", "product", "batch")
                ).lower()
                if not self._filter or self._filter in haystack:
                    self._rows.append(index)
        self.endResetModel()

    def row_record(self, view_row: int) -> dict[str, str] | None:
        if self.dataset is None or not 0 <= view_row < len(self._rows):
            return None
        return self.dataset.rows[self._rows[view_row]]

    # -- Qt model ------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid() or self.dataset is None:
            return 0
        return len(self.dataset.columns) + 1  # line number column

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or self.dataset is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section == 0:
                return "line"
            return self.dataset.columns[section - 1]
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or self.dataset is None:
            return None
        source = self._rows[index.row()]
        row = self.dataset.rows[source]
        line = self.dataset.lines[source] if source < len(self.dataset.lines) else 0
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if column == 0:
                return str(line)
            name = self.dataset.columns[column - 1]
            value = str(row.get(name, ""))
            if role == Qt.ItemDataRole.ToolTipRole:
                problems = self._problems.get(line)
                return "\n".join(problems) if problems else value
            return value
        if role == Qt.ItemDataRole.BackgroundRole and line in self._problems:
            return QColor("#fde8e8")
        if role == Qt.ItemDataRole.BackgroundRole and self.gen.grouped and row.get("id") == "*":
            return QColor("#fff6dd")
        return None


__all__ = ["CsvTableModel"]
