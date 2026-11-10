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


def test_signed_profile_simulator_passes(tmp_path: Path) -> None:
    """P0-09: the released signed profile runs Flow A to PASS in the simulator."""
    config = default_station(simulated=True, fixture_count=1)
    config.database = tmp_path / "station.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    config.profiles_dir = ROOT / "profiles"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "profiles/sc767-1.1.0")
    assert profile.app_image.image is not None and profile.app_image.image.is_signed
    station = Station(config, db, profile, simulation=True)
    station.auto_work_order(quantity=50)
    try:
        run_id = station.start_run("F1", pcb_serial="PCB-SIGNED", operator="pytest")
        assert station.wait("F1", timeout=120) == "PASS"
        run = db.run(run_id)
        assert run is not None and run["result"] == "PASS"
        steps = {row["name"]: row for row in db.steps(run_id)}
        detail = str(steps["Program production"]["detail"])
        assert "v0.1.0" in detail and ("factory HEX" in detail or " at " in detail)
        extras = db.run_extras(run_id)
        assert extras is not None
        import json

        metadata = json.loads(extras["metadata"] or "{}")
        assert metadata["expected_floor"] == 1
        assert metadata["security_counter"] == 1
        assert metadata["key_id"] == 0xFAA7_171B
    finally:
        station.close()
        db.close()


def test_signed_profile_boot_vcp_path_keeps_the_signature(tmp_path: Path) -> None:
    """P0-03/P0-07: a v2 image uploaded over BOOT keeps its extension bytes."""
    config = default_station(simulated=True, fixture_count=1)
    config.database = tmp_path / "station.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    config.profiles_dir = ROOT / "profiles"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "profiles/sc767-1.1.0")
    profile.manifest["app_load"] = "boot"
    station = Station(config, db, profile, simulation=True)
    station.auto_work_order(quantity=50)
    try:
        run_id = station.start_run("F1", pcb_serial="PCB-SIGNED-BOOT", operator="pytest")
        assert station.wait("F1", timeout=120) == "PASS"
        steps = {row["name"]: row for row in db.steps(run_id)}
        assert "loaded over VCP" in str(steps["Program production"]["detail"])
        extras = db.run_extras(run_id)
        assert extras is not None
        import json

        metadata = json.loads(extras["metadata"] or "{}")
        assert metadata["security_counter"] == 1
        assert metadata["expected_floor"] == 1
        # BOOT stored header bytes 88..251 verbatim, signature included.
        from hostcore import layout

        assert station.simulator is not None and profile.app_image.image is not None
        chip = station.simulator.bank("F1").device.chip
        header = chip.read(layout.META_A_BASE, layout.IMAGE_HEADER_SIZE)
        source = profile.app_image.image.header
        assert header[88:252] == source[88:252]
        assert header[128:192] == source[128:192]
    finally:
        station.close()
        db.close()


def test_eight_simulated_fixtures_run_in_parallel(tmp_path: Path) -> None:
    config = default_station(simulated=True, fixture_count=8)
    config.database = tmp_path / "parallel.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    config.profiles_dir = ROOT / "examples"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "examples/profile-sc767-demo")
    station = Station(config, db, profile, simulation=True)
    station.auto_work_order(quantity=100)
    try:
        run_ids = {
            name: station.start_run(name, pcb_serial=f"PCB-PAR-{name}", operator="pytest")
            for name in station.fixture_names()
        }
        for name in station.fixture_names():
            assert station.wait(name, timeout=180) == "PASS"
            assert db.run(run_ids[name]) is not None
    finally:
        station.close()
        db.close()


def test_flow_b_runs_under_boot_in_the_simulator(tmp_path: Path) -> None:
    """P3-04: Flow B programs both slots, tests HWVAL on trial, erases slot B."""
    config = default_station(simulated=True, fixture_count=1)
    config.database = tmp_path / "flowb.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    config.profiles_dir = ROOT / "profiles"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "profiles/sc767-1.2.0-flowb")
    assert profile.test_mode == "under_boot"
    station = Station(config, db, profile, simulation=True)
    station.auto_work_order(quantity=10)
    try:
        run_id = station.start_run("F1", pcb_serial="PCB-FLOWB", operator="pytest")
        assert station.wait("F1", timeout=180) == "PASS"
        steps = {row["name"]: row for row in db.steps(run_id)}
        assert steps["Link to HWVAL trial"]["result"] == "PASS"
        assert steps["End test image"]["result"] == "PASS"
        assert "slot B" in str(steps["Program Flow B slots"]["detail"])
        assert station.simulator is not None
        chip = station.simulator.bank("F1").device.chip
        # Slot B and metadata B are erased after the test.
        assert chip.read(0x0810C000, 16) == b"\xff" * 16
        assert chip.read(0x08108000, 4) == b"\xff" * 4
    finally:
        station.close()
        db.close()


def test_power_loss_during_option_bytes_is_unknown_config(tmp_path: Path) -> None:
    """P2-03: a failed option-byte write is routed to recovery, not retried blindly."""
    station, db, fixture = _station(tmp_path)
    assert station.simulator is not None
    station.simulator.bank(fixture).openocd.fail_options_once = True
    try:
        run_id = station.start_run(fixture, pcb_serial="PCB-PWR-LOSS", operator="pytest")
        assert station.wait(fixture, timeout=60) == "FAIL"
        extras = db.run_extras(run_id)
        assert extras is not None
        import json

        metadata = json.loads(extras["metadata"] or "{}")
        assert metadata["safe_state"] == "UNKNOWN_CONFIG"
        run = db.run(run_id)
        assert run is not None
        assert "recovery tool" in str(run["reason"])
    finally:
        station.close()
        db.close()
