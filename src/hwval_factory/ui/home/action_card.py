"""One step of the factory procedure: a status marker, what it does and one button.

The rows sit inside one card, split by hairlines, so the eight steps read as a
single ordered procedure.  The marker carries the state, not the colour: a
hollow ring with the step number is waiting, a dashed turning ring is running,
a filled disc with a check is done, a ring with a cross failed, and a ring with
a triangle needs attention before it can run.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from hostcore import soc_icons, soc_theme, soc_widgets
from hwval_factory.bench.actions import ActionResult
from hwval_factory.ui.bench.common import mnemonic_safe

MARKER = 32


class StepMarker(QWidget):
    """The round state marker at the left of a step row."""

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self.state = "idle"
        self._angle = 0
        self.setFixedSize(MARKER, MARKER)
        self._spin = QTimer(self)
        self._spin.setInterval(70)
        self._spin.timeout.connect(self._tick)

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "running":
            self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        t = soc_theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        inner = rect.adjusted(7, 7, -7, -7)
        state = self.state
        if state == "ok":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawEllipse(rect)
            soc_icons.paint(painter, "check", inner, "#ffffff", 2.4)
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if state == "running":
            pen = QPen(QColor(t.accent), 2.0)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3.0, 2.4])
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.save()
            painter.translate(self.rect().center())
            painter.rotate(self._angle)
            painter.translate(-self.rect().center().x(), -self.rect().center().y())
            painter.setPen(pen)
            painter.drawEllipse(rect)
            painter.restore()
        elif state == "failed":
            painter.setPen(QPen(QColor(t.accent), 2.0))
            painter.drawEllipse(rect)
            soc_icons.paint(painter, "x", inner, "accent", 2.4)
            return
        else:
            painter.setPen(QPen(QColor(t.border), 1.5))
            painter.drawEllipse(rect)
        if state in ("blocked", "stale"):
            soc_icons.paint(painter, "alert", inner.adjusted(0.5, 0.5, -0.5, -0.5), "text_tertiary", 2.0)
            return
        painter.setPen(QColor(t.accent if state == "running" else t.text_tertiary))
        font = soc_theme.ui_font(12)
        font.setWeight(font.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self.number))


class ActionCard(QWidget):
    """A row: marker, title with its one-line state, elapsed time and a Run button."""

    run_requested = Signal(int)

    def __init__(
        self,
        number: int,
        title: str,
        subtitle: str,
        glyph: str = "dot",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.number = number
        self.setObjectName("StepRow")
        self.setMinimumHeight(60)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)

        self.marker = StepMarker(number)
        row.addWidget(self.marker, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.heading = soc_widgets.label(mnemonic_safe(title), "CardTitle")
        self.detail = soc_widgets.ElidedLabel(subtitle, "Hint", Qt.TextElideMode.ElideRight)
        text.addWidget(self.heading)
        text.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setObjectName("Progress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        text.addWidget(self.progress)
        row.addLayout(text, 1)

        self.elapsed = soc_widgets.label("", "Caption")
        self.elapsed.setMinimumWidth(44)
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.elapsed)

        self.button = soc_widgets.button("Run", "Secondary", "play")
        self.button.setFixedWidth(116)
        self.button.setToolTip(f"Run step {number}: {mnemonic_safe(title)}")
        self.button.clicked.connect(lambda: self.run_requested.emit(self.number))
        row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._rest = subtitle
        self._last = subtitle
        self._state = "idle"
        self._started = 0.0
        self._clock = QTimer(self)
        self._clock.setInterval(200)
        self._clock.timeout.connect(self._show_elapsed)

    # -- state ---------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        self._state = state
        self.marker.set_state(state)

    def _show_elapsed(self) -> None:
        self.elapsed.setText(f"{time.monotonic() - self._started:.1f} s")

    def set_running(self, text: str = "Running") -> None:
        self._set_state("running")
        self.button.setEnabled(False)
        self.button.setText("Running")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.detail.setText(text)
        self._started = time.monotonic()
        self._show_elapsed()
        self._clock.start()

    def set_progress(self, percent: int, text: str) -> None:
        self.progress.setValue(percent)
        self._last = text or self._last
        self.detail.setText(self._last)

    def set_result(self, result: ActionResult) -> None:
        self._clock.stop()
        if self._started:
            self._show_elapsed()
        self.button.setEnabled(True)
        self.button.setText("Run again")
        self.progress.setVisible(False)
        self.progress.setValue(100)
        state = "ok" if result.ok else result.status if result.status in ("blocked", "stale") else "failed"
        self._set_state(state)
        self.detail.setText(result.summary)
        if result.details:
            self.detail.setToolTip(f"{result.summary}\n\n{result.details}")

    def reset(self) -> None:
        """Back to the resting look before a new run."""
        self._clock.stop()
        self._set_state("idle")
        self.elapsed.setText("")
        self.detail.setText(self._rest)
        self.detail.setToolTip(self._rest)
        self.button.setEnabled(True)
        self.button.setText("Run")
        self.progress.setVisible(False)

    def reset_button(self) -> None:
        self.button.setEnabled(True)
        self.button.setText("Run")
        self.progress.setVisible(False)
        if self._state in ("blocked", "stale"):
            self._set_state("idle")

    def mark_blocked(self, reason: str) -> None:
        self._set_state("blocked")
        self.detail.setText(reason)
        self.button.setEnabled(False)

    def mark_stale(self, reason: str) -> None:
        self._set_state("stale")
        self.detail.setText(reason)
        self.button.setEnabled(False)


__all__ = ["ActionCard", "StepMarker"]
