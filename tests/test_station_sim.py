from __future__ import annotations

from pathlib import Path

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
