"""Shared building blocks of the bench pages: cards, path rows, log, background work."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS
from hwval_factory.ui.bench.style import icon

Progress = Callable[[int, int], None]
Log = Callable[[str], None]


def mnemonic_safe(text: str) -> str:
    """Escape ``&`` so Qt does not eat it as a mnemonic marker."""
    return text.replace("&", "&&")


class Worker(QThread):
    """Run ``function(log, progress)`` off the GUI thread and report back by signal."""

    log = Signal(str)
    progress = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self, function: Callable[[Log, Progress], Any], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._function = function

    def run(self) -> None:
        try:
            result = self._function(self.log.emit, self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(result)


class Card(QFrame):
    """White rounded panel with a title; add rows through ``body``."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(12)
        heading = QLabel(mnemonic_safe(title))
        heading.setObjectName("CardTitle")
        outer.addWidget(heading)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setObjectName("Hint")
            hint.setWordWrap(True)
            outer.addWidget(hint)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        outer.addLayout(self.body)

    def add_row(self, label: str, widget: QWidget | QLayout, label_width: int = 150) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        name = QLabel(label)
        name.setObjectName("FieldLabel")
        name.setFixedWidth(label_width)
        row.addWidget(name)
        if isinstance(widget, QWidget):
            row.addWidget(widget, 1)
        else:
            row.addLayout(widget, 1)
        self.body.addLayout(row)


def chip(text: str, kind: str = "idle") -> QLabel:
    label = QLabel(text)
    set_chip(label, text, kind)
    return label


def set_chip(label: QLabel, text: str, kind: str) -> None:
    names = {"ok": "ChipOk", "bad": "ChipBad", "warn": "ChipWarn", "idle": "ChipIdle"}
    label.setText(text)
    label.setObjectName(names.get(kind, "ChipIdle"))
    # The object name drives the style sheet; re-polish so the new rule applies.
    label.style().unpolish(label)
    label.style().polish(label)


def value_label(text: str = "—") -> QLabel:
    label = QLabel(text)
    label.setObjectName("FieldValue")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class PathRow(QWidget):
    """A read/write path field with a Choose… button."""

    changed = Signal(str)

    def __init__(self, placeholder: str, caption: str, filters: str, *, save: bool = False) -> None:
        super().__init__()
        self._caption = caption
        self._filters = filters
        self._save = save
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setObjectName("PathEdit")
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text().strip()))
        self.button = QPushButton(icon("folder", COLORS["text"], 16), "Choose…")
        self.button.clicked.connect(self.choose)
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        if self.edit.text() != value:
            self.edit.setText(value)
            self.edit.setToolTip(value)

    def choose(self) -> None:
        start = self.text() or str(Path.home())
        if self._save:
            path, _ = QFileDialog.getSaveFileName(self, self._caption, start, self._filters)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self._caption, start, self._filters)
        if path:
            self.edit.setText(path)
            self.edit.setToolTip(path)
            self.changed.emit(path)


class LogPanel(QPlainTextEdit):
    def __init__(self, height: int = 180) -> None:
        super().__init__()
        self.setObjectName("Log")
        self.setReadOnly(True)
        self.setMinimumHeight(height)
        self.setMaximumBlockCount(2000)

    def line(self, text: str) -> None:
        self.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")


class KeyValueTable(QTableWidget):
    """Two-column read-only table for previews."""

    def __init__(self) -> None:
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels(["Field", "Value"])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.setAlternatingRowColors(True)

    def show_items(self, items: dict[str, str]) -> None:
        self.setRowCount(len(items))
        for row, (key, value) in enumerate(items.items()):
            self.setItem(row, 0, QTableWidgetItem(key))
            self.setItem(row, 1, QTableWidgetItem(value))
        self.resizeColumnToContents(0)
        self.setColumnWidth(0, max(self.columnWidth(0) + 24, 140))
        # Fit the rows (3 to 10) instead of scrolling inside a tiny box.
        rows = min(max(len(items), 3), 10)
        self.setFixedHeight(
            self.horizontalHeader().height() + rows * self.verticalHeader().defaultSectionSize() + 6
        )


class BenchPage(QWidget):
    """Scrolling page with a title, cards, a progress bar and a log."""

    title = ""
    subtitle = ""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BenchPage")
        self._worker: Worker | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("BenchScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("BenchScrollBody")
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(24, 20, 24, 24)
        self.column.setSpacing(16)
        # The window toolbar already shows the page title; the page opens with its lead-in.
        if self.subtitle:
            sub = QLabel(self.subtitle)
            sub.setObjectName("PageSubtitle")
            sub.setWordWrap(True)
            self.column.addWidget(sub)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.progress.setVisible(False)
        self.log = LogPanel()

    def add_card(self, card: Card) -> Card:
        self.column.addWidget(card)
        return card

    def add_activity(self) -> Card:
        """Progress bar and log in one card; call once, after the action cards."""
        card = Card("Activity")
        card.body.addWidget(self.progress)
        card.body.addWidget(self.log)
        self.column.addWidget(card)
        self.column.addStretch(1)
        return card

    @staticmethod
    def button_row(*buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return row

    @staticmethod
    def primary(text: str, icon_name: str) -> QPushButton:
        button = QPushButton(icon(icon_name, "#FFFFFF", 16), f" {mnemonic_safe(text)}")
        button.setObjectName("Primary")
        return button

    @staticmethod
    def secondary(text: str, icon_name: str) -> QPushButton:
        return QPushButton(icon(icon_name, COLORS["text"], 16), f" {mnemonic_safe(text)}")

    # -- background work --------------------------------------------
    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def set_busy(self, busy: bool) -> None:
        """Pages override to disable their buttons while work runs."""

    def run_job(
        self,
        function: Callable[[Log, Progress], Any],
        on_done: Callable[[Any], None] | None = None,
        on_fail: Callable[[str], None] | None = None,
    ) -> bool:
        if self.busy:
            return False
        worker = Worker(function, self)
        self._worker = worker
        worker.log.connect(self.log.line)
        worker.progress.connect(self._on_progress)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.set_busy(True)

        def finished_ok(result: object) -> None:
            self._finish()
            if on_done:
                on_done(result)

        def finished_bad(message: str) -> None:
            self._finish()
            self.log.line(f"FAILED: {message}")
            if on_fail:
                on_fail(message)

        worker.succeeded.connect(finished_ok)
        worker.failed.connect(finished_bad)
        worker.start()
        return True

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _finish(self) -> None:
        self.progress.setVisible(False)
        self.set_busy(False)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait(2000)
            worker.deleteLater()

    def wait_for_worker(self, timeout_ms: int = 5000) -> None:
        if self._worker is not None:
            self._worker.wait(timeout_ms)

    def on_shown(self) -> None:
        """Called when the page becomes visible."""

    def on_state_changed(self) -> None:
        """Called when the shared bench state changes."""


def parse_address(text: str) -> int:
    """Parse a hex or decimal address typed by the operator."""
    try:
        return int(text.strip(), 0)
    except ValueError as exc:
        raise ValueError(f"{text!r} is not an address; write it like 0x08105000") from exc
