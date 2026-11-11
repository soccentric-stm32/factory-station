"""Live debug-UART terminal, one file shared by the SoCcentric GUIs.

BOOT and every firmware print human-readable debug lines on USART6
(PG14, Arduino D1) at 115200 8N1.  The ST-LINK VCP (/dev/ttyACM*) carries
HWLINK, so the terminal has its own port selector and can read a USB-serial
adapter on D1 while the GUI owns the ST-LINK port.  It only pauses when the
selected debug port is also the GUI's board port.

This file depends on PySide6 and pyserial only and is copied unchanged into
bootloader-gui, validation-gui and factory-station; the widgets are styled by
the shared soc_theme style sheet (object names Card, CardTitle, Pill, Hint,
Link, IconButton, Console).  scripts/check-theme-sync.sh fails if a copy drifts.
"""

from __future__ import annotations

import re
import threading
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QGuiApplication,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BAUD_RATES = ("115200", "230400", "460800", "921600", "9600")


def label(text: str, name: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


def set_tone(widget: QLabel, tone: str) -> None:
    """Pill colour: '', accent, success, warning or danger."""
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def mono_font() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSizeF(max(font.pointSizeF() - 0.5, 8.0))
    return font


def serial_port_items() -> list[tuple[str, str]]:
    """Every serial port as (device, description), USB adapters first."""
    try:
        from serial.tools import list_ports
        items = [(p.device, p.description or p.device)
                 for p in list_ports.comports()]
    except Exception:  # noqa: BLE001 - pyserial may be missing
        import glob
        items = [(path, path) for path in
                 sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))]
    unique: dict[str, str] = {}
    for device, description in items:
        unique.setdefault(device, description)
    usb = [i for i in unique.items()
           if i[0].startswith(("/dev/ttyUSB", "/dev/ttyACM", "/dev/cu.usb"))
           or i[0].upper().startswith("COM")]
    items = usb or list(unique.items())
    return sorted(items, key=lambda i: (
        0 if i[0].startswith("/dev/ttyUSB") else 1, i[0]))


class SerialReader(QThread):
    """Read lines from the selected debug UART until stopped."""

    data = Signal(str)          # several complete lines per delivery
    failed = Signal(str)
    opened = Signal()

    def __init__(self, port: str, baud: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self._stop = threading.Event()
        self._link = None

    def stop(self) -> None:
        self._stop.set()
        if not self.wait(2000):
            # A driver that ignores the read timeout: closing the port
            # unblocks the reader (pyserial's cancel_read).
            link, self._link = self._link, None
            if link is not None:
                try:
                    link.close()
                except Exception:  # noqa: BLE001, S110 - already gone
                    pass
            self.wait(1000)

    def run(self) -> None:
        try:
            import serial
        except ImportError:
            self.failed.emit("pyserial is not installed")
            return
        try:
            link = serial.Serial(self.port, int(self.baud), timeout=0.1)
        except Exception as error:  # noqa: BLE001 - report, don't crash
            self.failed.emit(str(error))
            return
        self._link = link
        self.opened.emit()
        pending: list[str] = []
        last = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    raw = link.readline()
                except Exception as error:  # noqa: BLE001 - unplugged port
                    if not self._stop.is_set():
                        self.failed.emit(str(error))
                    break
                if raw:
                    text = ANSI_RE.sub("", raw.decode("utf-8", "replace"))
                    pending.append(text.rstrip("\r\n"))
                now = time.monotonic()
                if pending and (now - last >= 0.05 or len(pending) >= 200):
                    self.data.emit("\n".join(pending))
                    pending.clear()
                    last = now
            if pending:
                self.data.emit("\n".join(pending))
        finally:
            self._link = None
            try:
                link.close()
            except Exception:  # noqa: BLE001, S110 - already gone
                pass


class TerminalPanel(QFrame):
    """Card with the serial monitor, its port and a close button."""

    closed = Signal()
    baudChanged = Signal()

    def __init__(self, parent: QWidget | None = None,
                 title: str = "Debug UART") -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(340)
        self._port = ""
        self._baud = BAUD_RATES[0]
        self._board_port = ""
        self._board_busy = False
        self._reason = ""            # why the same-port monitor is paused
        self._preferred_port = ""    # remembered selection
        self._descriptions: dict[str, str] = {}
        self._error = ""
        self._user_paused = False
        self._reader: SerialReader | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 8, 4)
        head.setSpacing(4)
        head.addWidget(label(title, "CardTitle"))
        head.addStretch(1)
        self.pill = label("", "Pill")
        head.addWidget(self.pill)
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("IconButton")
        self.close_button.setToolTip("Close the debug terminal (Ctrl+J)")
        self.close_button.clicked.connect(self.closed)
        head.addWidget(self.close_button)
        outer.addLayout(head)

        port_row = QHBoxLayout()
        port_row.setContentsMargins(12, 0, 8, 2)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.lineEdit().setPlaceholderText("Debug port")
        self.port_combo.setToolTip(
            "Debug UART port: USART6 on Arduino D1 (PG14).  The ST-LINK "
            "VCP is normally HWLINK, not debug output.")
        self.port_combo.currentTextChanged.connect(self._on_port_changed)
        port_row.addWidget(self.port_combo, 1)
        outer.addLayout(port_row)

        controls = QHBoxLayout()
        controls.setContentsMargins(12, 0, 8, 4)
        controls.setSpacing(6)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setFixedWidth(110)
        self.baud_combo.setToolTip("Debug UART baud rate (firmware uses "
                                   "115200)")
        self.baud_combo.currentTextChanged.connect(self._on_baud_changed)
        controls.addWidget(self.baud_combo)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("Link")
        self.pause_button.setToolTip("Pause or resume the serial monitor")
        self.pause_button.clicked.connect(self._toggle_pause)
        controls.addWidget(self.pause_button)
        controls.addStretch(1)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("Link")
        self.clear_button.setToolTip("Clear the terminal")
        self.clear_button.clicked.connect(self.clear)
        controls.addWidget(self.clear_button)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("Link")
        self.copy_button.setToolTip("Copy the terminal")
        self.copy_button.clicked.connect(self.copy)
        controls.addWidget(self.copy_button)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("Link")
        self.save_button.setToolTip("Save the terminal text to a file")
        self.save_button.clicked.connect(self.save)
        controls.addWidget(self.save_button)
        outer.addLayout(controls)

        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(12, 0, 8, 4)
        self.detail = QLabel()
        self.detail.setObjectName("Hint")
        self.detail.setMinimumWidth(0)
        detail_row.addWidget(self.detail, 1)
        outer.addLayout(detail_row)

        self.view = QPlainTextEdit()
        # Reuse the output console's styling; both sit at the foot of a Card.
        self.view.setObjectName("Console")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(10000)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setWordWrapMode(QTextOption.NoWrap)
        self.view.setFont(mono_font())
        self.view.setPlaceholderText(
            "Debug output from USART6 (Arduino D1) appears here.\n"
            "Flashing only pauses the monitor when the debug port is also "
            "the bootctl port.")
        self.view.setMinimumHeight(120)
        outer.addWidget(self.view, 1)

        self._update()

    # ---- endpoint and lifecycle ----------------------------------------

    def port(self) -> str:
        return self.port_combo.currentText().strip()

    def baud(self) -> str:
        return self.baud_combo.currentText()

    def set_preferred_port(self, port: str) -> None:
        self._preferred_port = port.strip()

    def set_baud(self, baud: str) -> None:
        index = max(self.baud_combo.findText(baud), 0)
        if index == self.baud_combo.currentIndex():
            return
        self.baud_combo.blockSignals(True)
        self.baud_combo.setCurrentIndex(index)
        self.baud_combo.blockSignals(False)
        self._baud = self.baud()
        self._restart()

    def refresh_ports(self, board_port: str = "") -> None:
        """Rescan serial ports, keeping the current selection if it exists."""
        if board_port:
            self._board_port = board_port
        items = serial_port_items()
        devices = [device for device, _ in items]
        self._descriptions = dict(items)
        # Prefer an explicit choice, then the first (USB-serial) port.  Do
        # not default to the board port: that is HWLINK on the ST-LINK VCP.
        current = self.port() or self._preferred_port
        if not current:
            current = devices[0] if devices else ""
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for device, description in items:
            self.port_combo.addItem(device)
            self.port_combo.setItemData(self.port_combo.count() - 1,
                                        description, Qt.ToolTipRole)
        self.port_combo.setEditText(current)
        self.port_combo.blockSignals(False)
        self._port = current
        if not current:
            self._halt()
        else:
            self._restart()
        self._update()

    def set_board_port(self, port: str) -> None:
        if port == self._board_port:
            return
        self._board_port = port
        self._sync()

    def stop(self, reason: str = "") -> None:
        """The bootctl port is about to be used.

        Only pauses when the debug port is that same device; a separate
        USB-serial adapter keeps streaming through the flash.
        """
        self._board_busy = True
        self._reason = reason
        self._sync()

    def resume(self) -> None:
        self._board_busy = False
        self._reason = ""
        self._sync()

    def shutdown(self) -> None:
        self._halt()

    def _blocked_by_board(self) -> bool:
        return (self._board_busy and bool(self._board_port) and
                self._port == self._board_port)

    def _sync(self) -> None:
        paused = (self._user_paused or self._blocked_by_board() or
                  self._error)
        if not self.isVisible() or not self._port or paused:
            self._halt()
        else:
            self._spawn()
        self._update()

    def _restart(self) -> None:
        self._error = ""
        self._halt()
        self._sync()

    def _halt(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.stop()
            reader.deleteLater()
        self._update()

    def _spawn(self) -> None:
        if self._reader is not None:
            if self._reader.isRunning():
                self._update()
                return
            reader, self._reader = self._reader, None
            reader.deleteLater()
        if not self.isVisible() or not self._port or self._user_paused \
                or self._error or self._blocked_by_board():
            self._update()
            return
        reader = SerialReader(self._port, self._baud, self)
        reader.data.connect(self._on_data)
        reader.opened.connect(self._on_opened)
        reader.failed.connect(self._on_failed)
        self._reader = reader
        reader.start()
        self._update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event) -> None:
        try:
            self._halt()
        except RuntimeError:  # child widgets already deleted at shutdown
            pass
        super().hideEvent(event)

    # ---- reader slots ---------------------------------------------------

    def _on_opened(self) -> None:
        if self.sender() is not self._reader:
            return
        self._error = ""
        self._update()

    def _on_failed(self, message: str) -> None:
        if self.sender() is not self._reader:
            return
        # Keep the finished QThread until the next retry, where it is
        # deleted (deleting it from its own signal would race the shutdown).
        self._error = message
        self._update()

    def _on_data(self, text: str) -> None:
        self._append(text)

    # ---- controls -------------------------------------------------------

    def _toggle_pause(self) -> None:
        if self._error:
            self._restart()
            return
        self._user_paused = not self._user_paused
        self._sync()

    def _on_port_changed(self, text: str) -> None:
        port = text.strip()
        if port == self._port:
            self._update()
            return
        self._port = port
        self._preferred_port = port
        self._restart()

    def _on_baud_changed(self, _text: str) -> None:
        self._baud = self.baud()
        self._restart()
        self.baudChanged.emit()

    def clear(self) -> None:
        self.view.clear()

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.view.toPlainText())

    def save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save terminal log", "terminal.log", "Log (*.log *.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.view.toPlainText() + "\n")

    def text(self) -> str:
        return self.view.toPlainText()

    def feed(self, text: str) -> None:
        """Show lines that did not come from the serial port (tests, demo)."""
        self._append(text)

    # ---- appearance -----------------------------------------------------

    def _update(self) -> None:
        if self._reader is not None:
            description = self._descriptions.get(self._port, "")
            detail = f"{self._port} · {self._baud} baud"
            if description:
                detail = f"{detail} · {description}"
        else:
            detail = f"{self._port} · {self._baud} baud" if self._port else ""

        if not self._port:
            text, tone = "No port", "warning"
            detail = "Choose the debug UART port (USART6 on D1)"
        elif self._user_paused:
            text, tone = "Paused", "warning"
            detail = f"{self._port} · paused"
        elif self._blocked_by_board():
            text, tone = "Paused", "warning"
            detail = f"{self._reason or 'board port in use'} · {self._port}"
        elif self._error:
            text, tone = "Error", "danger"
            detail = f"{self._port} · {self._error}"
        elif self._reader is not None:
            text, tone = "Live", "success"
            if self._board_busy and self._port != self._board_port:
                detail = f"{detail} · board busy"
        else:
            text, tone = "Idle", ""
        self.pill.setText(text)
        set_tone(self.pill, tone)
        self.detail.setText(detail)
        self.detail.setToolTip(detail)
        self.pause_button.setText("Retry" if self._error else
                                  "Resume" if self._user_paused else "Pause")
        self.pause_button.setEnabled(bool(self._port))
        self.pause_button.setToolTip(
            "Try to open the port again" if self._error else
            "Resume the serial monitor" if self._user_paused else
            "Pause the serial monitor; the board keeps printing")

    def _append(self, text: str) -> None:
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.view.appendPlainText(text)
        if at_end:
            self.view.moveCursor(QTextCursor.End)
            bar.setValue(bar.maximum())
            self.view.horizontalScrollBar().setValue(0)
