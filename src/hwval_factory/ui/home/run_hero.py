"""The run panel at the top of Home: the state of the whole run at a glance."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from hostcore import soc_theme, soc_widgets

STEPS = 8


class StepTrack(QWidget):
    """Eight segments, one per step: grey waiting, accent done, hollow failed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.states: dict[int, str] = {}
        self.fraction = 0.0
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, number: int, state: str, fraction: float = 0.0) -> None:
        self.states[number] = state
        if state == "running":
            self.fraction = fraction
        self.update()

    def clear(self) -> None:
        self.states.clear()
        self.fraction = 0.0
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        t = soc_theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap = 4.0
        width = (self.width() - gap * (STEPS - 1)) / STEPS
        height = float(self.height())
        for index in range(STEPS):
            number = index + 1
            rect = QRectF(index * (width + gap), 0, width, height)
            state = self.states.get(number, "idle")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.hairline))
            painter.drawRoundedRect(rect, height / 2, height / 2)
            if state == "ok":
                painter.setBrush(QColor(t.accent))
                painter.drawRoundedRect(rect, height / 2, height / 2)
            elif state == "running":
                part = QRectF(rect.x(), rect.y(), max(height, rect.width() * self.fraction), height)
                painter.setBrush(QColor(t.accent))
                painter.drawRoundedRect(part, height / 2, height / 2)
            elif state == "failed":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(t.accent), 1.5))
                painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), height / 2, height / 2)


class RunHero(QFrame):
    """Big state word, the unit and the one button that runs the whole procedure."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(14)
        self.badge = soc_widgets.Badge("zap", 44)
        top.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        words = QVBoxLayout()
        words.setSpacing(2)
        self.state = soc_widgets.label("Ready", "PageTitle")
        self.detail = soc_widgets.ElidedLabel("", "Hint", Qt.TextElideMode.ElideRight)
        words.addWidget(self.state)
        words.addWidget(self.detail)
        top.addLayout(words, 1)

        self.counter = soc_widgets.label("0 / 8", "StatValue")
        self.counter.setToolTip("Steps done")
        top.addWidget(self.counter, 0, Qt.AlignmentFlag.AlignVCenter)

        self.button = soc_widgets.button("Start run", "Primary", "play")
        self.button.setMinimumSize(132, 40)
        top.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(top)

        self.track = StepTrack()
        outer.addWidget(self.track)

    def show_state(self, title: str, detail: str, glyph: str, done: int) -> None:
        self.state.setText(title)
        self.detail.setText(detail)
        self.badge.set_glyph(glyph)
        self.counter.setText(f"{done} / {STEPS}")


__all__ = ["RunHero", "StepTrack"]
