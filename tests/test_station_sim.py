from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.config import default_station
from hwval_factory.database import Database
from hwval_factory.profile import load_profile
from hwval_factory.station import Station

ROOT = Path(__file__).resolve().parents[1]


def _station(tmp_path: Path, *, fail_tests: set[str] | None = None) -> tuple[Station, Database, str]:
    config = default_station(simulated=True, fixture_count=1)
    config.database = tmp_path / "station.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    config.profiles_dir = ROOT / "examples"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "examples/profile-sc767-demo")
    station = Station(config, db, profile, simulation=True, fail_tests=fail_tests)
    station.auto_work_order(quantity=50)
    return station, db, "F1"


def test_simulator_full_run_passes(tmp_path: Path) -> None:
    station, db, fixture = _station(tmp_path)
    try:
        run_id = station.start_run(fixture, pcb_serial="PCB-E2E-PASS", operator="pytest")
        assert station.wait(fixture, timeout=60) == "PASS"
        run = db.run(run_id)
        assert run is not None and run["result"] == "PASS"
        assert len(db.tests(run_id)) >= 20
        assert run["uid"]
    finally:
        station.close()
        db.close()


def test_simulator_failure_is_recorded(tmp_path: Path) -> None:
    station, db, fixture = _station(tmp_path, fail_tests={"CORE.001"})
    try:
        run_id = station.start_run(fixture, pcb_serial="PCB-E2E-FAIL", operator="pytest")
        assert station.wait(fixture, timeout=60) == "FAIL"
        run = db.run(run_id)
        assert run is not None
        assert run["failed_step"] == "Run EOL test plan"
        assert "CORE.001" in str(run["reason"])
    finally:
        station.close()
        db.close()


def test_boot_vcp_app_load_path(tmp_path: Path) -> None:
    station, db, fixture = _station(tmp_path)
    assert station.profile is not None
    station.profile.manifest["app_load"] = "boot"
    try:
        run_id = station.start_run(fixture, pcb_serial="PCB-E2E-BOOT", operator="pytest")
        assert station.wait(fixture, timeout=60) == "PASS"
        steps = {row["name"]: row for row in db.steps(run_id)}
        assert "loaded over VCP" in str(steps["Program production"]["detail"])
    finally:
        station.close()
        db.close()


def test_rework_reuses_serial_and_mac_for_same_uid(tmp_path: Path) -> None:
    from hwval_factory.models import RunKind

    station, db, fixture = _station(tmp_path)
    try:
        first = station.start_run(fixture, pcb_serial="PCB-REWORK", operator="pytest")
        assert station.wait(fixture, timeout=60) == "PASS"
        unit = db.get_unit(str(db.run(first)["uid"]))
        assert unit is not None
        serial = str(unit["serial_number"])
        mac = str(unit["mac"])

        second = station.start_run(
            fixture,
            pcb_serial="PCB-REWORK",
            operator="pytest",
            kind=RunKind.REWORK,
            rework_approved=True,
        )
        assert station.wait(fixture, timeout=60) == "PASS"
        after = db.get_unit(str(db.run(second)["uid"]))
        assert after is not None
        assert str(after["serial_number"]) == serial
        assert str(after["mac"]) == mac
        revisions = sorted(int(row["revision"]) for row in db.records_for_uid(str(after["uid"])))
        assert revisions == list(range(1, len(revisions) + 1))
        assert len(revisions) >= 2
    finally:
        station.close()
        db.close()


def test_production_blocks_without_self_test(tmp_path: Path) -> None:
    from hwval_factory.config import default_station
    from hwval_factory.station import StationError

    config = default_station(simulated=False, fixture_count=1)
    config.fixtures[0].probe_serial = "SIM-FAKE-PROBE"
    config.database = tmp_path / "prod.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    db = Database(config.database)
    db.initialize()
    try:
        station = Station(
            config,
            db,
            load_profile(ROOT / "examples/profile-sc767-demo"),
            simulation=False,
        )
        with pytest.raises(StationError, match="self-test is due"):
            station.start_run("F1", pcb_serial="PCB-NO-SELFTEST", operator="pytest")
        station.close()
    finally:
        db.close()
