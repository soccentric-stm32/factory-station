"""Standalone tools and recovery on the simulator (P1-06/P1-08)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.config import default_station
from hwval_factory.database import Database
from hwval_factory.models import Role
from hwval_factory.profile import load_profile
from hwval_factory.recovery import RecoveryError, RecoveryService
from hwval_factory.station import Station
from hwval_factory.tools import ToolError, ToolRunner

ROOT = Path(__file__).resolve().parents[1]


def _station(tmp_path: Path) -> tuple[Station, Database]:
    config = default_station(simulated=True, fixture_count=1)
    config.database = tmp_path / "station.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    config.printer.output_dir = str(tmp_path / "labels")
    config.profiles_dir = ROOT / "profiles"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "profiles/sc767-1.1.0")
    station = Station(config, db, profile, simulation=True)
    station.auto_work_order(quantity=20)
    return station, db


def test_identify_and_role_checks(tmp_path: Path) -> None:
    station, db = _station(tmp_path)
    try:
        runner = ToolRunner(station, "admin")
        result = runner.identify("F1")
        assert result.ok
        assert result.data["matches_profile"] is True
        assert result.data["uid"]
        with pytest.raises(ToolError, match="supervisor"):
            runner.check_role("erase", Role.OPERATOR)
        runner.check_role("erase", Role.SUPERVISOR)
        with pytest.raises(ToolError, match="confirm"):
            runner.erase("F1", confirm=False)
    finally:
        station.close()
        db.close()


def test_erase_and_program_boot_on_the_simulator(tmp_path: Path) -> None:
    station, db = _station(tmp_path)
    try:
        runner = ToolRunner(station)
        assert runner.erase("F1", confirm=True).ok
        assert runner.program_boot("F1").ok
    finally:
        station.close()
        db.close()


def test_identity_read_and_write_on_the_simulator(tmp_path: Path) -> None:
    station, db = _station(tmp_path)
    try:
        runner = ToolRunner(station)
        written = runner.identity(
            "F1", action="write", serial="SC767-2645-000123-4", mac="02:00:00:00:00:2A",
            hw_revision="B",
        )
        assert written.ok
        read = runner.identity("F1", action="read")
        assert read.data["serial"] == "SC767-2645-000123-4"
        assert read.data["mac"].upper() == "02:00:00:00:00:2A"
        verified = runner.identity(
            "F1", action="verify", serial="SC767-2645-000123-4",
            mac="02:00:00:00:00:2A",
        )
        assert verified.ok
        with pytest.raises(ToolError, match="serial"):
            runner.identity("F1", action="verify", serial="WRONG")
    finally:
        station.close()
        db.close()


def test_boot_check_and_protect_after_a_full_run(tmp_path: Path) -> None:
    station, db = _station(tmp_path)
    try:
        run_id = station.start_run("F1", pcb_serial="PCB-TOOLS", operator="pytest")
        assert station.wait("F1", timeout=120) == "PASS"
        runner = ToolRunner(station)
        assert runner.boot_check("F1").ok
        result = runner.protect("F1")
        assert result.ok
        assert result.data["option_bytes"]["RDP"] == 1
        assert db.run(run_id) is not None
    finally:
        station.close()
        db.close()


def test_recovery_catch_and_unstick(tmp_path: Path) -> None:
    station, db = _station(tmp_path)
    try:
        service = RecoveryService(station)
        bank = station.simulator.bank("F1")
        bank.device.set_role("BOOT", "BOOT", 0)
        caught = service.catch("F1", timeout=5)
        assert caught["hello"]["role"] in ("BOOT", "BOOTLOADER")
        bank.device.set_role("APP", "app", 0x10000)
        unstuck = service.unstick_trial("F1", timeout=20)
        assert unstuck["hello"]["role"] in ("APP", "DEMO")
        bank.device.chip.optcr = (1 << 8) | 0xFFF0000  # RDP 1
        with pytest.raises(RecoveryError, match=r"RDP|failed"):
            service.unlock_rdp("F1", operator="admin", reason="test", confirmed=False)
        result = service.unlock_rdp("F1", operator="admin", reason="recover", confirmed=True)
        assert result["after"]["RDP"] == 0
    finally:
        station.close()
        db.close()


def test_capture_log_requires_a_configured_uart(tmp_path: Path) -> None:
    station, db = _station(tmp_path)
    try:
        with pytest.raises(ToolError, match="debug UART"):
            ToolRunner(station).capture_log("F1", seconds=0.1)
    finally:
        station.close()
        db.close()
