"""Validation page: check the factory data and the peripherals of one board."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

from hostcore.theme import COLORS
from hwval_factory.bench import validation
from hwval_factory.ui.bench.common import BenchPage, Card, chip, set_chip, value_label
from hwval_factory.ui.bench.state import BenchState

_LABELS = {
    validation.PASS: "Pass",
    validation.FAIL: "Fail",
    validation.WARN: "Incomplete",
    validation.SKIP: "Skipped",
}
_CHIPS = {validation.PASS: "ok", validation.FAIL: "bad", validation.WARN: "warn"}


class ValidationPage(BenchPage):
    title = "Validation"
    subtitle = (
        "Run a basic check of the board: the factory data blocks and the peripherals. "
        "It only reads from the board; nothing is written."
    )

    def __init__(self, state: BenchState) -> None:
        super().__init__()
        self.state = state
        self.report: validation.ValidationReport | None = None

        run = Card("Run validation")
        self.verdict_chip = chip("Not run", "idle")
        self.summary_text = value_label("Open Detect, then press Run validation.")
        line = QHBoxLayout()
        line.setSpacing(10)
        line.addWidget(self.verdict_chip)
        line.addWidget(self.summary_text, 1)
        run.add_row("Result", line)
        self.run_button = self.primary("Run validation", "check")
        self.save_button = self.secondary("Save report…", "save")
        self.save_button.setEnabled(False)
        run.body.addLayout(self.button_row(self.run_button, self.save_button))
        self.add_card(run)

        results = Card("Checks", "Contents come from the identity sector; peripherals from the firmware self-test.")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Group", "Check", "Result", "Detail"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(120)
        results.body.addWidget(self.table)
        self.add_card(results)
        self.add_activity()

        self.run_button.clicked.connect(self.run)
        self.save_button.clicked.connect(self.save_report)

    def set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self.report is not None)

    def run(self) -> None:
        state = self.state
        if not state.simulate and not state.port:
            QMessageBox.warning(
                self, "Validation", "No UART port is selected. Open the Detect page."
            )
            return

        def job(log: Any, _progress: Any) -> validation.ValidationReport:
            link = state.open_link()
            try:
                return validation.run_validation(link, log)
            finally:
                link.close()

        set_chip(self.verdict_chip, "Running", "idle")
        self.summary_text.setText("Checking the board…")
        self.run_job(job, self._show_report, self._failed)

    def _failed(self, message: str) -> None:
        set_chip(self.verdict_chip, "No answer", "bad")
        self.summary_text.setText(message)

    def _show_report(self, report: validation.ValidationReport) -> None:
        self.report = report
        verdict = report.verdict
        text = {"pass": "Pass", "fail": "Fail", "warn": "Incomplete"}[verdict]
        set_chip(self.verdict_chip, text, _CHIPS[verdict])
        self.summary_text.setText(report.summary())
        self.table.setRowCount(len(report.checks))
        colours = {
            validation.PASS: COLORS["pass"],
            validation.FAIL: COLORS["fail"],
            validation.WARN: COLORS["warn"],
            validation.SKIP: COLORS["muted"],
        }
        for row, check in enumerate(report.checks):
            result = QTableWidgetItem(_LABELS[check.status])
            result.setForeground(QBrush(QColor(colours[check.status])))
            for column, item in enumerate(
                (
                    QTableWidgetItem(check.group),
                    QTableWidgetItem(check.name),
                    result,
                    QTableWidgetItem(check.detail),
                )
            ):
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        # Show every row instead of scrolling inside the card.
        self.table.setFixedHeight(
            self.table.horizontalHeader().height()
            + len(report.checks) * self.table.verticalHeader().defaultSectionSize()
            + 6
        )
        self.save_button.setEnabled(True)

    def board_label(self) -> str:
        info = self.state.info
        return f"Board UID {info.uid_hex}" if info is not None else ""

    def save_report(self) -> None:
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save validation report",
            str(self.state.data_dir / "validation-report.txt"),
            "Text files (*.txt)",
        )
        if path:
            Path(path).write_text(self.report.text(self.board_label()), encoding="utf-8")
            self.log.line(f"Report saved to {path}")
