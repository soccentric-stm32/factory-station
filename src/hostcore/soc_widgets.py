"""SoCcentric desktop GUI shell: the widgets every SoCcentric app is built from.

Shared, byte-identical, by BOOT Flasher, Factory Data, HWVAL Factory and HWVAL
Studio (see ``soc_theme.py``).  Each app owns its copy; none imports another.

The shell, in the order a window is built:

  SidebarHeader   app badge, app name and a one-line subtitle
  section()       SIDEBAR SECTION caption
  NavItem         sidebar entry: icon, title, optional subtitle, status dot
  version_label() "Version 1.2.3" at the foot of the sidebar
  PageHeader      40 px badge, page title, subtitle, trailing pill/actions
  Card            surface with a title row and hairline-separated rows
  FlowLayout      left-to-right layout that wraps onto new lines
  StatTile        small number tile (tests, PASS, FAIL...)
  Glyph           a live icon as a widget (empty states, status marks)
  StatusPill      icon + word pill for a result state
  OutputPanel     titled console card with progress, cancel, copy and clear
  about(), confirm(), error(), info()   the dialogs
  add_zoom_actions(), restore_zoom()   View > Zoom, saved per app

Everything painted here reads the theme tokens at paint time, so a light/dark
switch only needs a repaint.  Every Qt name used inside a paintEvent is
imported at the top: an exception there aborts the process.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSettings,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import soc_icons, soc_theme

# Fits a long row label ("Activate after flash") with room to spare.
ROW_LABEL_WIDTH = 152
SIDEBAR_WIDTH = 248
PAGE_MARGINS = (32, 24, 32, 24)


# ---- small helpers ------------------------------------------------------------

def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def set_tone(widget: QWidget, tone: str) -> None:
    """Pill / Hint / Banner / StatValue colour: '', accent, success, warning, danger."""
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        repolish(widget)


def label(text: str, name: str, parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    widget.setObjectName(name)
    return widget


def pill(text: str = "", tone: str = "") -> QLabel:
    widget = label(text, "Pill")
    widget.setProperty("tone", tone)
    return widget


def section(text: str) -> QLabel:
    """Sidebar section caption, upper case like the other apps."""
    heading = label(text.upper(), "SidebarSection")
    heading.setContentsMargins(8, 0, 0, 4)
    return heading


def version_label(version: str) -> QLabel:
    footer = label(f"Version {version}", "Caption")
    footer.setContentsMargins(8, 0, 0, 0)
    return footer


def hairline() -> QFrame:
    line = QFrame()
    line.setObjectName("Hairline")
    line.setFixedHeight(1)
    return line


def button(text: str, kind: str = "", glyph: str | None = None) -> QPushButton:
    """A push button of one of the theme's kinds (Primary, Accent, Secondary,
    Danger, Link, Flat), with an optional live icon."""
    widget = QPushButton(text)
    if kind:
        widget.setObjectName(kind)
    if glyph:
        filled = kind in ("Primary", "Accent", "LargeAction")
        widget.setIcon(soc_icons.icon(glyph, "on_accent" if filled else
                                      "danger" if kind == "Danger" else "text"))
    return widget


def icon_button(glyph: str, tip: str, color: str = "text_secondary") -> QPushButton:
    widget = QPushButton()
    widget.setObjectName("IconButton")
    widget.setToolTip(tip)
    widget.setIcon(soc_icons.icon(glyph, color))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


class ElidedLabel(QLabel):
    """Single-line label that elides and shows the full text as a tooltip."""

    def __init__(self, text: str = "", name: str = "Hint",
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle) -> None:
        super().__init__()
        self.setObjectName(name)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text if text else "")
        self._elide()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, self._mode, max(self.width(), 40)))


# ---- badge, sidebar ------------------------------------------------------------

class Badge(QWidget):
    """The rounded accent tile with a white glyph (app and page badges)."""

    def __init__(self, glyph: str, size: int = 36, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self.setFixedSize(size, size)

    def set_glyph(self, glyph: str) -> None:
        self.glyph = glyph
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        soc_icons.paint_badge(painter, self.glyph, QRectF(self.rect()))


class SidebarHeader(QWidget):
    """App badge, app name and subtitle at the top of the sidebar."""

    def __init__(self, app_name: str, subtitle: str, glyph: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(12)
        self.badge = Badge(glyph, 36)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label(app_name, "AppTitle")
        self.subtitle = ElidedLabel(subtitle, "AppSubtitle", Qt.TextElideMode.ElideRight)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        row.addWidget(self.badge)
        row.addLayout(titles, 1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)


class NavItem(QAbstractButton):
    """Sidebar entry: icon, title, optional subtitle and an optional status dot."""

    TONES: ClassVar[dict[str, str]] = {"": "text_tertiary", "success": "success",
                                       "warning": "warning", "accent": "accent",
                                       "danger": "danger"}

    def __init__(self, icon_name: str, title: str, subtitle: str = "", *,
                 show_status: bool = True) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.icon_name = icon_name
        self.title = title
        self.subtitle = subtitle
        self.show_status = show_status
        self.status_tone = ""
        self.status_filled = False
        self.setToolTip(f"{title} · {subtitle}" if subtitle else title)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_status(self, tone: str, filled: bool, tip: str = "") -> None:
        self.status_tone = tone
        self.status_filled = filled
        base = f"{self.title} · {self.subtitle}" if self.subtitle else self.title
        self.setToolTip(base + (f"\n{tip}" if tip else ""))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(200, soc_theme.px(48) if self.subtitle else soc_theme.px(36))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:
        t = soc_theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        checked = self.isChecked()
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(rect, 8, 8)
        elif self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 14) if not t.dark else QColor(255, 255, 255, 18))
            painter.drawRoundedRect(rect, 8, 8)
        if self.hasFocus() and not checked:
            painter.setPen(QPen(QColor(t.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)

        fg = t.on_accent if checked else t.text
        secondary = QColor(t.on_accent if checked else t.text_secondary)
        if checked:
            secondary.setAlphaF(0.82)
        soc_icons.paint(painter, self.icon_name,
                        QRectF(12, (rect.height() - 18) / 2, 18, 18),
                        t.on_accent if checked else t.text_secondary, 1.8)

        text_left = 42
        dot_space = 22 if self.show_status else 8
        width = rect.width() - text_left - dot_space
        font = QFont(self.font())
        font.setPixelSize(soc_theme.px(13))
        font.setWeight(QFont.Weight.DemiBold if checked else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(fg))
        metrics = QFontMetrics(font)
        title_line = metrics.height()
        if self.subtitle:
            small = QFont(self.font())
            small.setPixelSize(soc_theme.px(11))
            small_metrics = QFontMetrics(small)
            block = title_line + small_metrics.height()
            top = (rect.height() - block) / 2
            painter.drawText(QRectF(text_left, top, width, title_line),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(self.title, Qt.TextElideMode.ElideRight, int(width)))
            painter.setFont(small)
            painter.setPen(secondary)
            painter.drawText(QRectF(text_left, top + title_line, width, small_metrics.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             small_metrics.elidedText(self.subtitle, Qt.TextElideMode.ElideRight,
                                                      int(width)))
        else:
            painter.drawText(QRectF(text_left, 0, width, rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(self.title, Qt.TextElideMode.ElideRight, int(width)))

        if not self.show_status:
            return
        dot = QColor(getattr(t, self.TONES.get(self.status_tone, "text_tertiary")))
        if checked:
            dot = QColor(t.on_accent)
        centre = QPointF(rect.width() - 14, rect.height() / 2)
        if self.status_filled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
        else:
            painter.setPen(QPen(dot, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, 3.5, 3.5)


# ---- page --------------------------------------------------------------------

class PageHeader(QWidget):
    """Badge, page title and subtitle, then trailing widgets on the right."""

    def __init__(self, title: str, subtitle: str = "", glyph: str | None = None) -> None:
        super().__init__()
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(14)
        self.badge = Badge(glyph or "app", 40)
        self.badge.setVisible(glyph is not None)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label(title, "PageTitle")
        self.subtitle = label(subtitle, "PageSubtitle")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        self.row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        self.row.addLayout(texts, 1)
        self.pill = pill()
        self.pill.hide()
        self.row.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_title(self, title: str, subtitle: str | None = None) -> None:
        self.title.setText(title)
        if subtitle is not None:
            self.subtitle.setText(subtitle)
            self.subtitle.setVisible(bool(subtitle))

    def set_glyph(self, glyph: str | None) -> None:
        self.badge.setVisible(glyph is not None)
        if glyph is not None:
            self.badge.set_glyph(glyph)

    def set_pill(self, text: str, tone: str = "") -> None:
        self.pill.setText(text)
        self.pill.setVisible(bool(text))
        set_tone(self.pill, tone)

    def add_trailing(self, widget: QWidget) -> None:
        self.row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)


class Card(QFrame):
    """Grouped surface: optional title row, then rows split by hairlines."""

    def __init__(self, title: str = "", trailing: QWidget | None = None,
                 subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(0)
        self._rows = 0
        self.head: QHBoxLayout | None = None
        if title:
            head = QHBoxLayout()
            head.setContentsMargins(16, 12, 12, 8 if not subtitle else 2)
            head.setSpacing(8)
            self.title_label = label(title, "CardTitle")
            head.addWidget(self.title_label)
            head.addStretch(1)
            if trailing is not None:
                head.addWidget(trailing)
            self._box.addLayout(head)
            self.head = head
            if subtitle:
                hint = label(subtitle, "Hint")
                hint.setWordWrap(True)
                hint.setContentsMargins(16, 0, 16, 8)
                self._box.addWidget(hint)

    def add_trailing(self, widget: QWidget) -> None:
        if self.head is not None:
            self.head.addWidget(widget)

    def add_row(self, title: str | None, *items: Any, stretch: int = -1,
                align_top: bool = False) -> QWidget:
        """One row: a fixed-width label, then widgets (None = stretch)."""
        if self._rows:
            self._box.addWidget(hairline())
        self._rows += 1
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        if title is not None:
            name = label(title, "RowLabel")
            name.setFixedWidth(ROW_LABEL_WIDTH)
            layout.addWidget(name, 0, Qt.AlignmentFlag.AlignTop if align_top
                             else Qt.AlignmentFlag.AlignVCenter)
        for index, item in enumerate(items):
            if item is None:
                layout.addStretch(1)
            elif isinstance(item, QWidget):
                layout.addWidget(item, 1 if index == stretch else 0)
            else:
                layout.addLayout(item, 1 if index == stretch else 0)
        self._box.addWidget(row)
        return row

    def add_widget(self, widget: QWidget, padded: bool = False) -> None:
        if self._rows:
            self._box.addWidget(hairline())
        self._rows += 1
        if padded:
            holder = QWidget()
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.addWidget(widget)
            widget = holder
        self._box.addWidget(widget)


class FlowLayout(QLayout):
    """Place widgets left to right; wrap onto a new line when needed."""

    def __init__(self, parent: QWidget | None = None, margin: int = 0,
                 spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y = area.x(), y + line + self.spacing()
                line = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


def field(*widgets: QWidget, spacing: int = 4) -> QVBoxLayout:
    """Stack an input over its hint."""
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(spacing)
    for widget in widgets:
        column.addWidget(widget)
    return column


class Glyph(QWidget):
    """A live icon drawn at its widget size; ``color`` is a token or hex."""

    def __init__(self, name: str, color: str = "text_secondary", size: int = 16,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.color = color
        self.setFixedSize(size, size)

    def set_glyph(self, name: str, color: str | None = None) -> None:
        self.name = name
        if color is not None:
            self.color = color
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        soc_icons.paint(painter, self.name, QRectF(self.rect()), self.color)


class StatusPill(QFrame):
    """A result shown three ways at once: glyph, word and colour."""

    def __init__(self, text: str = "", glyph: str = "dot", tone: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Pill")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 10, 3)
        row.setSpacing(5)
        self.glyph = Glyph(glyph, "text_secondary", 13)
        self.text = QLabel(text)
        row.addWidget(self.glyph)
        row.addWidget(self.text)
        self.set_state(text, glyph, tone)

    def set_state(self, text: str, glyph: str, tone: str) -> None:
        self.text.setText(text)
        self.glyph.set_glyph(glyph, tone or "text_secondary")
        set_tone(self, tone)
        self.setToolTip(text)


class StatTile(QFrame):
    """A number with a caption above it, in a subtle card."""

    def __init__(self, caption: str, value: str = "0", tone: str = "") -> None:
        super().__init__()
        self.setObjectName("SubtleCard")
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 8, 12, 8)
        column.setSpacing(0)
        self.caption = label(caption.upper(), "StatLabel")
        self.value = label(value, "StatValue")
        self.value.setProperty("tone", tone)
        column.addWidget(self.caption)
        column.addWidget(self.value)

    def set_value(self, value: str, tone: str | None = None) -> None:
        self.value.setText(value)
        if tone is not None:
            set_tone(self.value, tone)


class Switch(QAbstractButton):
    """macOS-style on/off switch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(42, 24)

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._anim.stop()
        self._offset = 1.0 if checked else 0.0
        self.update()

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def paintEvent(self, _event: QPaintEvent) -> None:
        t = soc_theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        track = QRectF(1, 1, 40, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_mix(QColor(t.border), QColor(t.accent), self._offset))
        painter.drawRoundedRect(track, 11, 11)
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-1, -1, 1, 1), 12, 12)
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(3 + self._offset * 18, 3, 18, 18))


# ---- output panel --------------------------------------------------------------

class OutputPanel(QFrame):
    """The output card: title, status, progress, tools, then a console."""

    cancelled = Signal()

    def __init__(self, title: str = "Output", placeholder: str = "",
                 copy: bool = True, clear: bool = True,
                 console: QPlainTextEdit | None = None) -> None:
        super().__init__()
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.head = QHBoxLayout()
        self.head.setContentsMargins(16, 8, 8, 8)
        self.head.setSpacing(8)
        self.title = label(title, "CardTitle")
        self.status = ElidedLabel(name="Hint", mode=Qt.TextElideMode.ElideRight)
        self.progress = QProgressBar()
        self.progress.setObjectName("Progress")
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("Link")
        self.cancel_button.clicked.connect(self.cancelled)
        self.cancel_button.hide()
        self.head.addWidget(self.title)
        self.head.addWidget(self.status, 1)
        self.head.addWidget(self.progress)
        self.head.addWidget(self.cancel_button)
        self._tools = QHBoxLayout()
        self._tools.setSpacing(2)
        self.head.addLayout(self._tools)
        self.copy_button = icon_button("copy", "Copy the output")
        self.copy_button.clicked.connect(self.copy)
        self.clear_button = icon_button("trash", "Clear the output")
        self.clear_button.clicked.connect(self.clear)
        if copy:
            self._tools.addWidget(self.copy_button)
        if clear:
            self._tools.addWidget(self.clear_button)
        outer.addLayout(self.head)

        self.console = console if console is not None else QPlainTextEdit()
        self.console.setObjectName("Console")
        self.console.setReadOnly(True)
        if console is None:
            self.console.setMaximumBlockCount(20000)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.console.setFont(soc_theme.mono_font())
        self.console.setPlaceholderText(placeholder)
        self.console.setMinimumHeight(96)
        outer.addWidget(self.console, 1)
        soc_theme.on_change(lambda _t: self.console.setFont(soc_theme.mono_font()))

    def add_tool(self, widget: QWidget, before_copy: bool = True) -> None:
        """Add a control to the header, left of copy/clear by default."""
        if before_copy:
            self._tools.insertWidget(0, widget)
        else:
            self._tools.addWidget(widget)

    def set_status(self, text: str, tone: str = "") -> None:
        self.status.setText(text)
        set_tone(self.status, tone)

    def set_busy(self, busy: bool, cancellable: bool = False) -> None:
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy and cancellable)
        if busy:
            self.progress.setRange(0, 0)

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def append(self, text: str) -> None:
        self.console.appendPlainText(text)

    def clear(self) -> None:
        self.console.clear()

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.console.toPlainText())


# ---- zoom ---------------------------------------------------------------------

ZOOM_STEPS = (80, 90, 100, 110, 125, 150, 175, 200)
ZOOM_KEY = "view/zoom"


def restore_zoom(settings: QSettings) -> int:
    """Set the type scale from the saved zoom; call before building the UI."""
    try:
        percent = int(str(settings.value(ZOOM_KEY, 100)))
    except (TypeError, ValueError):
        percent = 100
    percent = min(ZOOM_STEPS, key=lambda step: abs(step - percent))
    soc_theme.set_scale(percent / 100)
    return percent


def set_zoom(settings: QSettings, percent: int) -> None:
    percent = min(ZOOM_STEPS, key=lambda step: abs(step - percent))
    settings.setValue(ZOOM_KEY, percent)
    soc_theme.set_scale(percent / 100)
    app = QApplication.instance()
    if app is not None:
        soc_theme.apply(app)


def add_zoom_actions(menu: QMenu, parent: QWidget, settings: QSettings) -> list[QAction]:
    """Zoom In (Ctrl++), Zoom Out (Ctrl+-) and Actual Size (Ctrl+0)."""
    def step(direction: int) -> None:
        current = round(soc_theme.scale() * 100)
        index = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - current))
        index = max(0, min(len(ZOOM_STEPS) - 1, index + direction))
        set_zoom(settings, ZOOM_STEPS[index])

    entries: list[tuple[str, list[QKeySequence], Callable[[], None]]] = [
        ("Zoom In", [QKeySequence(QKeySequence.StandardKey.ZoomIn), QKeySequence("Ctrl+=")],
         lambda: step(1)),
        ("Zoom Out", [QKeySequence(QKeySequence.StandardKey.ZoomOut)], lambda: step(-1)),
        ("Actual Size", [QKeySequence("Ctrl+0")], lambda: set_zoom(settings, 100)),
    ]
    actions: list[QAction] = []
    for text, keys, callback in entries:
        action = QAction(text, parent)
        action.setShortcuts(keys)
        action.triggered.connect(lambda _checked=False, c=callback: c())
        menu.addAction(action)
        actions.append(action)
    return actions


# ---- dialogs ------------------------------------------------------------------

def error(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def info(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def confirm(parent: QWidget | None, title: str, text: str, ok_text: str,
            destructive: bool = False) -> bool:
    """Ask before acting; the safe answer (Cancel) is the default."""
    box = QMessageBox(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question,
                      title, text, QMessageBox.StandardButton.NoButton, parent)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    if destructive:
        ok.setObjectName("Danger")
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def about(parent: QWidget | None, app_name: str, version: str, glyph: str,
          text: str, details: str = "") -> None:
    """The About dialog every SoCcentric app shows: badge, name, version, text."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"About {app_name}")
    dialog.setModal(True)
    column = QVBoxLayout(dialog)
    column.setContentsMargins(32, 28, 32, 20)
    column.setSpacing(6)
    column.addWidget(Badge(glyph, 64), 0, Qt.AlignmentFlag.AlignHCenter)
    column.addSpacing(10)
    name = label(app_name, "PageTitle")
    name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(name)
    ver = label(f"Version {version}", "Caption")
    ver.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    ver.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    column.addWidget(ver)
    column.addSpacing(10)
    body = label(text, "PageSubtitle")
    body.setWordWrap(True)
    body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    body.setFixedWidth(360)
    column.addWidget(body, 0, Qt.AlignmentFlag.AlignHCenter)
    if details:
        extra = label(details, "Caption")
        extra.setWordWrap(True)
        extra.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        extra.setFixedWidth(360)
        column.addWidget(extra, 0, Qt.AlignmentFlag.AlignHCenter)
    column.addSpacing(8)
    company = label("© 2026 SoCcentric", "Caption")
    company.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(company)
    column.addSpacing(12)
    ok = button("OK", "Accent")
    ok.setDefault(True)
    ok.setMinimumWidth(96)
    ok.clicked.connect(dialog.accept)
    column.addWidget(ok, 0, Qt.AlignmentFlag.AlignHCenter)
    dialog.exec()
