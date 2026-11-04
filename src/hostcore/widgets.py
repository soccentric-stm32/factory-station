"""Reusable widgets shared by HWVAL Studio and HWVAL Factory."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, icon, state_icon


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


class MetricCard(Card):
    def __init__(self, title: str, value: str = "—", caption: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("Muted")
        self.value = QLabel(value)
        self.value.setObjectName("Metric")
        self.caption = QLabel(caption)
        self.caption.setObjectName("Muted")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, value: str, caption: str = "") -> None:
        self.value.setText(value)
        if caption:
            self.caption.setText(caption)


class StatusPill(QWidget):
    """State as icon + word + colour (UI-003)."""

    def __init__(self, state: str = "IDLE", text: str | None = None) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 10, 3)
        layout.setSpacing(6)
        self._icon = QLabel()
        self._text = QLabel(text or state)
        self._text.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.set_state(state, text)

    def set_state(self, state: str, text: str | None = None) -> None:
        color = COLORS.get(state.lower(), COLORS["idle"])
        if state in ("PASS", "FAIL", "ACTION", "ERROR"):
            color = COLORS[state.lower()]
        self._icon.setPixmap(state_icon(state, 18).pixmap(18, 18))
        self._text.setText(text or state)
        self.setStyleSheet(
            f"background: {color}18; border: 1px solid {color}55; border-radius: 10px;"
        )
        self._text.setStyleSheet(f"font-weight: 700; color: {color};")


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "Search…") -> None:
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icon("search", COLORS["muted"], 18), QLineEdit.ActionPosition.LeadingPosition)


class DataTable(QTableWidget):
    def __init__(self, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)

    def set_rows(self, rows: list[list[object]]) -> None:
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, value)
                self.setItem(r, c, item)

    def selected_key(self) -> object | None:
        row = self.currentRow()
        if row < 0:
            return None
        item = self.item(row, 0)
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)


class StepList(QWidget):
    """Compact list of EOL steps with state glyphs and timings."""

    def __init__(self) -> None:
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._rows: dict[int, tuple[QLabel, QLabel, QLabel]] = {}

    def set_steps(self, names: list[str]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        self._rows.clear()
        for index, name in enumerate(names, 1):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(6, 3, 6, 3)
            h.setSpacing(8)
            glyph = QLabel("○")
            glyph.setFixedWidth(20)
            title = QLabel(f"{index}. {name}")
            title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            timing = QLabel("—")
            timing.setObjectName("Muted")
            timing.setAlignment(Qt.AlignmentFlag.AlignRight)
            h.addWidget(glyph)
            h.addWidget(title)
            h.addWidget(timing)
            self._layout.addWidget(row)
            self._rows[index] = (glyph, title, timing)

    def update_step(self, seq: int, result: str, duration_ms: int | None = None) -> None:
        if seq not in self._rows:
            return
        glyph, title, timing = self._rows[seq]
        marks = {
            "RUNNING": ("▶", COLORS["running"]),
            "PASS": ("✓", COLORS["pass"]),
            "FAIL": ("✕", COLORS["fail"]),
            "SKIP": ("-", COLORS["muted"]),
            "ABORTED": ("■", COLORS["warn"]),
            "ERROR": ("!", COLORS["fail"]),
        }
        mark, color = marks.get(result, ("○", COLORS["muted"]))
        glyph.setText(mark)
        glyph.setStyleSheet(f"color: {color}; font-weight: 700;")
        if duration_ms is not None:
            timing.setText(f"{duration_ms / 1000:.2f} s")
        if result == "RUNNING":
            title.setStyleSheet("font-weight: 700;")
        elif result == "FAIL":
            title.setStyleSheet(f"font-weight: 700; color: {COLORS['fail']};")


class EmptyState(QWidget):
    def __init__(self, text: str, icon_name: str = "chip") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph = QLabel()
        glyph.setPixmap(icon(icon_name, COLORS["muted"], 48).pixmap(48, 48))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
        layout.addWidget(label)
