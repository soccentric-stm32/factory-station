"""The debug UART side terminal in the bench window."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hwval_factory.ui.main_window import BenchWindow


def test_terminal_shows_lines_and_toggles(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = BenchWindow(tmp_path, simulate=True)
    window.resize(1400, 900)
    window.show()
    app.processEvents()
    assert window.terminal.isVisible()
    assert window.terminal.width() >= 330
    window.terminal.feed("task 1 LED: blinks")
    assert "task 1 LED" in window.terminal.text()
    window.terminal_action.setChecked(False)
    assert not window.terminal.isVisible()
    window.terminal_action.setChecked(True)
    assert window.terminal.isVisible()
    window.close()
