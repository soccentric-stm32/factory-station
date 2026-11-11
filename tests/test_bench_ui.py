"""Headless run of the real station window through the whole simulated bench workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from hostcore.theme import apply_theme
from hwval_factory.bench import csvdb, flashing
from hwval_factory.ui.main_window import BenchWindow
from hwval_factory.ui.sidebar import SECTION_TITLES

ROOT = Path(__file__).resolve().parents[1]
BOOTLOADER = flashing.bootloader_root()


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    for name in ("question",):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    win = BenchWindow(tmp_path / "data", simulate=True)
    win.show()
    yield win
    for page in win.bench_pages.values():
        page.wait_for_worker()
    win.close()


def settle(window: BenchWindow, page, timeout: float = 20.0) -> None:
    import time

    end = time.time() + timeout
    app = QApplication.instance()
    while page.busy and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not page.busy


def test_every_page_opens_and_the_sidebar_follows(window: BenchWindow) -> None:
    for key in SECTION_TITLES:
        window._show_section(key)
        assert window.sidebar._nav[key][0].isChecked()
        assert window.section_title.text() == SECTION_TITLES[key]
    assert set(SECTION_TITLES) == {
        "detect",
        "bootloader",
        "firmware",
        "device",
        "sysinfo",
        "calibration",
        "diagnostics",
        "validation",
    }


def test_menu_actions_switch_pages_and_shortcuts_are_unique(window: BenchWindow) -> None:
    bench_menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Bench")
    next(a for a in bench_menu.actions() if a.text() == "Calibration").trigger()
    assert window.pages.currentWidget() is window.bench_pages["calibration"]
    assert window.sidebar._nav["calibration"][0].isChecked()
    shortcuts: dict[str, str] = {}
    for action in window.findChildren(QAction):
        key = action.shortcut().toString()
        if key:
            assert key not in shortcuts, f"{key}: {action.text()} / {shortcuts[key]}"
            shortcuts[key] = action.text()


@pytest.mark.skipif(BOOTLOADER is None, reason="needs the bootloader checkout for the key and bootctl")
def test_full_simulated_bench_run(window: BenchWindow, tmp_path: Path) -> None:
    factory_image = ROOT.parent / "factory-firmware" / "build" / "factory.img"
    if not factory_image.is_file():
        pytest.skip("build the factory firmware first (scripts/build.sh)")
    bench = window.bench
    pages = window.bench_pages
    bench.set_simulate(True)
    assert bench.probe_serial and bench.port

    boot = pages["bootloader"]
    bench.remember("boot_image", str(factory_image))  # any existing file stands in for BOOT
    boot.flash()
    settle(window, boot)
    assert bench.sim_boot_flashed

    firmware = pages["firmware"]
    firmware.flash()
    settle(window, firmware)
    assert bench.info is not None and bench.info.is_factory_firmware

    sysinfo = pages["sysinfo"]
    csvdb.write_sample(tmp_path / "factory.csv", 4)
    bench.remember("csv_path", str(tmp_path / "factory.csv"))
    sysinfo.csv_row.set_text(bench.csv_path)
    sysinfo._load_db()
    sysinfo.search.setText("SC0002")
    assert sysinfo.selected_id() == "SC0002" and sysinfo.blob is not None
    sysinfo.write()
    settle(window, sysinfo)
    assert csvdb.FactoryCsv(tmp_path / "factory.csv").get("SC0002")["status"] == "programmed"

    calibration = pages["calibration"]
    (tmp_path / "unit.cal").write_bytes(calibration.sample_blob())
    calibration._path_changed(str(tmp_path / "unit.cal"))
    calibration.write()
    settle(window, calibration)

    diagnostics = pages["diagnostics"]
    (tmp_path / "unit.diag").write_bytes(diagnostics.sample_blob())
    diagnostics._path_changed(str(tmp_path / "unit.diag"))
    diagnostics.write()
    settle(window, diagnostics)
    diagnostics.initialize()
    settle(window, diagnostics)

    device = pages["device"]
    device.read(reset=True)
    settle(window, device)
    states = [device.blocks.item(row, 2).text() for row in range(3)]
    assert states == ["written", "written", "written"]


def test_a_bad_address_is_refused_without_touching_the_board(window: BenchWindow, tmp_path: Path) -> None:
    window.bench.set_simulate(True)
    page = window.bench_pages["diagnostics"]
    (tmp_path / "x.diag").write_bytes(page.sample_blob())
    page._path_changed(str(tmp_path / "x.diag"))
    page.address.setText("0x08104000")
    page.write()
    assert page.result_chip.text() == "Bad address"
    assert window.bench.sim_device.flash[: 16] == b"\xff" * 16


def test_validation_page_runs_on_the_simulated_board(window: BenchWindow, tmp_path: Path) -> None:
    window.bench.set_simulate(True)
    page = window.bench_pages["validation"]
    page.run()
    settle(window, page)
    assert page.report is not None
    # A fresh simulated board has blank blocks: incomplete, but nothing failed.
    assert page.verdict_chip.text() == "Incomplete"
    assert page.table.rowCount() == len(page.report.checks)
    window.bench.sim_device.ram_fault = True
    page.run()
    settle(window, page)
    assert page.verdict_chip.text() == "Fail"
    assert page.save_button.isEnabled()


def test_validation_page_draws_results_by_group(window: BenchWindow) -> None:
    from hwval_factory.bench import validation as v
    from hwval_factory.ui.bench.charts import count_by_group

    page = window.bench_pages["validation"]
    assert page.chart.groups() == []
    report = v.ValidationReport(
        checks=[
            v.Check("Identity", "uid", v.PASS, ""),
            v.Check("Identity", "serial", v.FAIL, "blank"),
            v.Check("Peripherals", "adc", v.PASS, ""),
            v.Check("Peripherals", "can", v.SKIP, ""),
        ]
    )
    page._show_report(report)
    groups = page.chart.groups()
    assert [g.name for g in groups] == ["Identity", "Peripherals"]
    assert (groups[0].pass_, groups[0].fail) == (1, 1)
    assert (groups[1].pass_, groups[1].skip) == (1, 1)
    assert not page.chart.grab().isNull()
    assert count_by_group([("a", "bogus")])[0].skip == 1
