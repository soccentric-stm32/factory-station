"""Central serial/MAC pool (P4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hwval_factory.central import CentralPoolError, PostgresPool
from hwval_factory.config import default_station, load_station_config
from hwval_factory.database import Database
from hwval_factory.models import WorkOrder
from hwval_factory.profile import load_profile
from hwval_factory.serials import SerialService

ROOT = Path(__file__).resolve().parents[1]


class FakeCentral:
    station_id = "STN-01"

    def __init__(self) -> None:
        self.serials: list[str] = []
        self.macs: list[str] = []
        self.released: list[tuple[str, str]] = []

    def load_serials(self, work_order: str, serials: Any) -> None:
        self.serials.extend(serials)

    def load_macs(self, work_order: str, macs: Any) -> None:
        self.macs.extend(macs)

    def reserve_serial(self, work_order: str, *, station: str, uid: str = "", run_id: int | None = None) -> str | None:
        return self.serials.pop(0) if self.serials else None

    def reserve_mac(self, work_order: str, *, station: str, uid: str = "", run_id: int | None = None) -> str | None:
        return self.macs.pop(0) if self.macs else None

    def release(self, *, serial: str = "", mac: str = "") -> None:
        self.released.append((serial, mac))

    def usage(self, work_order: str) -> dict[str, int]:
        return {"TOTAL": len(self.serials), "FREE": len(self.serials), "FREE_PERCENT": 100}


def test_serial_service_uses_the_central_pool(tmp_path: Path) -> None:
    config = default_station(simulated=True, fixture_count=1)
    config.database = tmp_path / "central.sqlite"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "examples/profile-sc767-demo")
    central = FakeCentral()
    service = SerialService(db, central=central)
    wo = WorkOrder(
        name="WO-CENTRAL",
        profile_id=profile.profile_id,
        quantity=2,
        serial_start=1,
        serial_end=2,
        mac_start="02:00:00:00:10:00",
        mac_count=2,
    )
    service.prepare_work_order(wo, profile)
    assert central.serials
    allocation = service.allocate(wo.name, uid="A" * 24, run_id=1, need_mac=True)
    assert allocation is not None
    service.release(allocation)
    assert central.released == [(allocation.serial_number, allocation.mac)]
    db.close()


def test_central_pool_url_is_validated(tmp_path: Path) -> None:
    raw = {
        "station": {"id": "STN-01", "name": "Line 1"},
        "simulation": True,
        "fixtures": [{"name": "F1", "probe_serial": "SIM"}],
        "central_pool": {"url": "sqlite:///local.db"},
    }
    path = tmp_path / "station.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception, match="PostgreSQL"):
        load_station_config(path)
    raw["central_pool"]["url"] = "postgresql://line:pw@db.example/hwval"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loaded = load_station_config(path)
    assert loaded.central_pool.enabled
    with pytest.raises(CentralPoolError):
        PostgresPool("sqlite:///x")
