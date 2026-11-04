from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.auth import SessionInfo
from hwval_factory.config import default_station
from hwval_factory.database import Database
from hwval_factory.models import Role, User
from hwval_factory.profile import load_profile
from hwval_factory.station import Station

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_station_window_constructs_offscreen(tmp_path: Path, qapp, qtbot) -> None:
    config = default_station(simulated=True, fixture_count=2)
    config.database = tmp_path / "ui.sqlite"
    config.labels_dir = tmp_path / "labels"
    config.certificates_dir = tmp_path / "certificates"
    db = Database(config.database)
    db.initialize()
    profile = load_profile(ROOT / "examples/profile-sc767-demo")
    station = Station(config, db, profile, simulation=True)
    station.auto_work_order(quantity=10)
    from hwval_factory.auth import AuthService
    from hwval_factory.ui.main_window import StationWindow

    auth = AuthService(db)
    auth.ensure_default_admin()
    auth.session = SessionInfo(user=User("admin", Role.ADMIN))
    window = StationWindow(config, db, auth, station, profile, kiosk=False)
    qtbot.addWidget(window)
    window.show()
    assert len(window.tiles) == 2
    window.close()
    station.close()
    db.close()
