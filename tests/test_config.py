from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.config import ConfigError, load_station_config


def test_serial_scanner_config(tmp_path: Path) -> None:
    path = tmp_path / "station.yaml"
    path.write_text(
        """
station: {id: STN-1, name: Test}
simulation: true
fixtures:
  - {name: F1, probe_serial: SIM-1}
scanner: {mode: serial, serial_port: /dev/ttyUSB0, baudrate: 9600, prefix: "SCAN:", suffix: "\\r"}
""",
        encoding="utf-8",
    )
    config = load_station_config(path)
    assert config.scanner.mode == "serial"
    assert config.scanner.serial_port == "/dev/ttyUSB0"
    assert config.scanner.prefix == "SCAN:"


def test_serial_scanner_requires_port(tmp_path: Path) -> None:
    path = tmp_path / "station.yaml"
    path.write_text(
        """
station: {id: STN-1, name: Test}
simulation: true
fixtures:
  - {name: F1, probe_serial: SIM-1}
scanner: {mode: serial}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="serial_port"):
        load_station_config(path)
