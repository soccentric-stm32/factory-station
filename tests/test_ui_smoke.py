from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_bench_window_constructs_offscreen(tmp_path: Path, qapp) -> None:
    from hwval_factory.ui.main_window import BenchWindow

    window = BenchWindow(tmp_path, simulate=True)
    window.show()
    assert list(window.bench_pages) == [
        "home", "sys", "cal", "diag", "inspect", "units", "settings"
    ]
    assert window.pages.currentWidget() is window.bench_pages["home"]
    assert len(window.bench_pages["home"].cards) == 8
    window.close()
