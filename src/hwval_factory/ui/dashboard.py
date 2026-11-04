"""Yield dashboard with FPY, throughput, cycle time and failure Pareto (UI-012)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, icon
from hostcore.widgets import Card, DataTable, MetricCard
from hwval_factory.database import Database


class ParetoChart(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title = title
        self.items: list[tuple[str, int]] = []
        self.setMinimumHeight(220)

    def set_items(self, items: list[tuple[str, int]]) -> None:
        self.items = items[:10]
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLORS["surface"]))
        painter.setPen(QColor(COLORS["text"]))
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(12, 24, self.title)
        if not self.items:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(12, 60, "No failures recorded")
            painter.end()
            return
        top = 42
        row_height = max(18, (self.height() - top - 10) // max(1, len(self.items)))
        max_value = max(count for _, count in self.items) or 1
        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        label_width = min(220, max(110, self.width() // 3))
        for index, (name, count) in enumerate(self.items):
            y = top + index * row_height
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(
                QRectF(8, y, label_width - 12, row_height),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                name,
            )
            bar_width = (self.width() - label_width - 46) * count / max_value
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["fail"]))
            painter.drawRoundedRect(QRectF(label_width, y + 3, bar_width, row_height - 7), 4, 4)
            painter.setPen(QColor(COLORS["text"]))
            painter.drawText(
                QRectF(label_width + bar_width + 6, y, 40, row_height),
                int(Qt.AlignmentFlag.AlignVCenter),
                str(count),
            )
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawLine(label_width - 4, top - 4, label_width - 4, self.height() - 8)
        painter.end()


class DashboardView(QWidget):
    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(12)

        controls = QHBoxLayout()
        title = QLabel("Yield dashboard")
        title.setObjectName("Heading")
        self.filter = QComboBox()
        self.filter.addItem("All work orders", "")
        self.period = QComboBox()
        self.period.addItem("All time", 0)
        self.period.addItem("Today", 1)
        self.period.addItem("Last 7 days", 7)
        self.period.addItem("Last 30 days", 30)
        self.period.currentIndexChanged.connect(self.refresh)
        self.refresh_button = QPushButton(icon("refresh", COLORS["text"], 18), "Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(title)
        controls.addStretch(1)
        controls.addWidget(QLabel("Work order"))
        controls.addWidget(self.filter)
        controls.addWidget(QLabel("Period"))
        controls.addWidget(self.period)
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)

        metrics = QHBoxLayout()
        self.fpy = MetricCard("First pass yield", "—")
        self.rate = MetricCard("Units per hour", "—")
        self.cycle = MetricCard("Average cycle", "—")
        self.total = MetricCard("Runs", "—")
        for card in (self.fpy, self.rate, self.cycle, self.total):
            metrics.addWidget(card)
        root.addLayout(metrics)

        charts = QHBoxLayout()
        step_card = Card()
        step_layout = QVBoxLayout(step_card)
        self.step_chart = ParetoChart("Failures by step")
        step_layout.addWidget(self.step_chart)
        test_card = Card()
        test_layout = QVBoxLayout(test_card)
        self.test_chart = ParetoChart("Failures by test ID")
        test_layout.addWidget(self.test_chart)
        charts.addWidget(step_card)
        charts.addWidget(test_card)
        root.addLayout(charts, 2)

        self.cycle_table = DataTable(["Group", "Runs", "Average cycle", "Fastest", "Slowest"])
        root.addWidget(self.cycle_table, 1)

        self.table = DataTable(
            [
                "Run",
                "Started",
                "Work order",
                "Fixture",
                "Profile",
                "Kind",
                "Result",
                "Step",
                "Reason",
            ]
        )
        root.addWidget(self.table, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)
        self.refresh()

    def set_work_orders(self, names: list[str]) -> None:
        current = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("All work orders", "")
        for name in names:
            self.filter.addItem(name, name)
        index = self.filter.findData(current)
        self.filter.setCurrentIndex(max(0, index))
        self.filter.blockSignals(False)

    def _since(self) -> str:
        days = int(self.period.currentData() or 0)
        if days <= 0:
            return ""
        now = datetime.now(UTC)
        if days == 1:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now - timedelta(days=days)
        return start.isoformat(timespec="seconds")

    def refresh(self) -> None:
        work_order = str(self.filter.currentData() or "")
        since = self._since()
        stats = self.db.yield_stats(work_order=work_order, since=since)
        self.fpy.set_value(f"{stats['fpy']:.1f} %", f"{stats['passed']} / {stats['total']}")
        self.rate.set_value(f"{stats['units_per_hour']:.1f}", "units/hour")
        self.cycle.set_value(f"{stats['average_cycle_s']:.1f} s", "average")
        self.total.set_value(str(stats["total"]), f"{stats['failed']} failed")
        self.step_chart.set_items(stats["step_pareto"])
        self.test_chart.set_items(stats["test_pareto"])
        cycles = self.db.cycle_stats(since=since)
        cycle_rows: list[list[object]] = []
        for group, label in (("fixture", "Fixture"), ("profile", "Profile")):
            for item in cycles[group]:
                cycle_rows.append(
                    [
                        f"{label}: {item['name']}",
                        item["runs"],
                        f"{item['average_s']:.1f} s",
                        f"{item['min_s']:.1f} s",
                        f"{item['max_s']:.1f} s",
                    ]
                )
        self.cycle_table.set_rows(cycle_rows)
        rows = self.db.search_runs(work_order=work_order, since=since, limit=200)
        self.table.set_rows(
            [
                [
                    row["id"],
                    str(row["started_at"])[0:19].replace("T", " "),
                    row["work_order"] or "",
                    row["fixture"],
                    f"{row['profile_id']} v{row['profile_version']}",
                    row["kind"],
                    row["result"] or "",
                    row["failed_step"] or "",
                    row["reason"] or "",
                ]
                for row in rows
            ]
        )


__all__ = ["DashboardView", "ParetoChart"]
