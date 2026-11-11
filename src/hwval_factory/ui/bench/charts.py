"""Painted charts for the bench pages (no plotting dependency).

Author: Sandesh Ghimire <sandesh@soccentric.com>
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from hostcore.theme import COLORS

# Order and colour of the segments in every bar.
SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("pass", "Pass", "pass"),
    ("fail", "Fail", "fail"),
    ("warn", "Incomplete", "warn"),
    ("skip", "Skipped", "muted"),
)


@dataclass
class GroupCounts:
    name: str
    pass_: int = 0
    fail: int = 0
    warn: int = 0
    skip: int = 0

    @property
    def total(self) -> int:
        return self.pass_ + self.fail + self.warn + self.skip

    def value(self, key: str) -> int:
        return int(getattr(self, "pass_" if key == "pass" else key))


def count_by_group(checks: Iterable[tuple[str, str]]) -> list[GroupCounts]:
    """(group, status) pairs to per-group counts, groups in first-seen order.

    ``status`` is one of "pass", "fail", "warn", "skip"; anything else counts as skipped.
    """
    groups: OrderedDict[str, GroupCounts] = OrderedDict()
    for group, status in checks:
        entry = groups.setdefault(group, GroupCounts(group))
        key = status if status in ("pass", "fail", "warn") else "skip"
        attr = "pass_" if key == "pass" else key
        setattr(entry, attr, getattr(entry, attr) + 1)
    return list(groups.values())


class GroupResultChart(QWidget):
    """One horizontal stacked bar per check group, with a total on the right."""

    ROW = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list[GroupCounts] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._resize_to_rows()

    def set_groups(self, groups: Sequence[GroupCounts]) -> None:
        self._groups = list(groups)
        self._resize_to_rows()
        self.update()

    def groups(self) -> list[GroupCounts]:
        return list(self._groups)

    def _resize_to_rows(self) -> None:
        rows = max(1, len(self._groups))
        self.setFixedHeight(28 + rows * self.ROW)

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        text = QColor(COLORS["text"])
        muted = QColor(COLORS["muted"])

        # Legend.
        x = 0.0
        for _key, label, colour in SEGMENTS:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS[colour]))
            painter.drawRoundedRect(QRectF(x, 5, 9, 9), 2, 2)
            painter.setPen(muted)
            width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(QRectF(x + 14, 2, width + 4, 15), Qt.AlignmentFlag.AlignVCenter, label)
            x += 14 + width + 18

        if not self._groups:
            painter.setPen(muted)
            painter.drawText(
                QRectF(0, 24, self.width(), self.ROW),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "Run validation to see the results by group.",
            )
            return

        label_w = max(painter.fontMetrics().horizontalAdvance(g.name) for g in self._groups) + 12
        total_w = painter.fontMetrics().horizontalAdvance("999 / 999") + 8
        bar_left = float(label_w)
        bar_w = max(40.0, self.width() - label_w - total_w - 8)
        widest = max(1, max(g.total for g in self._groups))
        for row, group in enumerate(self._groups):
            top = 24 + row * self.ROW
            painter.setPen(text)
            painter.drawText(
                QRectF(0, top, label_w - 8, self.ROW),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                group.name,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["border"]).lighter(112))
            track = QRectF(bar_left, top + 6, bar_w, self.ROW - 12)
            painter.drawRoundedRect(track, 4, 4)
            cursor = bar_left
            full = bar_w * group.total / widest
            for key, _label, colour in SEGMENTS:
                count = group.value(key)
                if count <= 0:
                    continue
                seg = full * count / group.total
                painter.setBrush(QColor(COLORS[colour]))
                painter.drawRoundedRect(QRectF(cursor, top + 6, max(2.0, seg - 1), self.ROW - 12), 3, 3)
                cursor += seg
            painter.setPen(muted)
            painter.drawText(
                QRectF(bar_left + bar_w + 8, top, total_w, self.ROW),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{group.pass_} / {group.total}",
            )
