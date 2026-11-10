from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_bench_window_constructs_offscreen(tmp_path: Path, qapp, qtbot) -> None:
    from hwval_factory.ui.main_window import BenchWindow

    window = BenchWindow(tmp_path, simulate=True)
    qtbot.addWidget(window)
    window.show()
    assert len(window.bench_pages) == 8
    assert window.pages.currentWidget() is window.bench_pages["detect"]
    window.close()
