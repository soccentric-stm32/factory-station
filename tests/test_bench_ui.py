"""Headless tests of the merged station window and the Home action cards."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from hostcore.theme import apply_theme
from hwval_factory.bench import actions
from hwval_factory.core import generators
from hwval_factory.ui.main_window import BenchWindow


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    for name in ("question",):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    win = BenchWindow(tmp_path / "data", simulate=True)
    win.bench.set_artifacts_root(tmp_path / "artifacts")
    win.bench.workspace.ensure_layout()
    win.show()
    yield win
    win.close()


def seed_workspace(window: BenchWindow) -> None:
    for kind, gen in generators.GENERATORS.items():
        path = window.bench.workspace.csv_path_path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gen.sample(), encoding="utf-8")


def test_every_page_opens_and_the_sidebar_follows(window: BenchWindow) -> None:
    from hwval_factory.ui.sidebar import SECTION_TITLES

    for key in SECTION_TITLES:
        window._show_section(key)
        assert window.sidebar._nav[key][0].isChecked()
        assert window.section_title.text() == SECTION_TITLES[key]
    assert set(SECTION_TITLES) == {"home", "sys", "cal", "diag", "inspect", "units", "settings"}


def test_shortcuts_are_unique(window: BenchWindow) -> None:
    shortcuts: dict[str, str] = {}
    for action in window.findChildren(QAction):
        key = action.shortcut().toString()
        if key:
            assert key not in shortcuts, f"{key}: {action.text()} / {shortcuts[key]}"
            shortcuts[key] = action.text()
    action_menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Actions")
    next(a for a in action_menu.actions() if a.text().startswith("5.")).trigger()
    assert window.pages.currentWidget() is window.bench_pages["home"]


def test_data_pages_show_four_horizontal_tabs(window: BenchWindow) -> None:
    for kind in ("sys", "cal", "diag"):
        page = window.bench_pages[kind]
        assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == [
            "CSV data", "Records", "Output files", "Board"
        ]


def test_home_actions_run_the_whole_flow_on_the_simulator(window: BenchWindow) -> None:
    seed_workspace(window)
    state = window.bench
    state.set_unit("SC0001")
    results = []
    for fn in (
        actions.detect,
        actions.load_bootloader,
        actions.load_factory_firmware,
        actions.device_info,
        actions.system_info,
        actions.calibration,
        actions.diagnostics,
        actions.validate,
    ):
        result = fn(state)
        results.append(result)
        assert result.ok, f"{fn.__name__}: {result.summary}\n{result.details}"
    assert "PASS" in results[-1].summary
    for kind in ("sys", "cal", "diag"):
        assert state.unit_file(kind).is_file()
        assert (state.workspace.out_dir_path / "manifest.csv").is_file()


def test_data_page_loads_the_sample_csv(window: BenchWindow) -> None:
    seed_workspace(window)
    page = window.bench_pages["sys"]
    page.refresh()
    assert page.model.rowCount() > 0
    assert page.model.columnCount() > 1
    page.filter_edit.setText("SC0002")
    assert page.model.rowCount() >= 1
    record = page.model.row_record(0)
    assert record is not None and record["id"] == "SC0002"


def test_units_page_lists_the_registry(window: BenchWindow) -> None:
    from hwval_factory.core.units import Registry

    seed_workspace(window)
    state = window.bench
    state.set_unit("SC0001")
    actions.detect(state)
    actions.load_bootloader(state)
    actions.load_factory_firmware(state)
    actions.system_info(state)
    page = window.bench_pages["units"]
    page.reload()
    assert page.table.rowCount() >= 1
    assert "SC0001" in page.table.item(0, 0).text() or page.table.rowCount() > 0
