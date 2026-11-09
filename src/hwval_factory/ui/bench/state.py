"""Shared state of the bench pages: what is connected and which files are chosen."""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from hwval_factory.bench import csvdb, detect, flashing
from hwval_factory.bench.factorylink import (
    FactoryInfo,
    FactoryLink,
    SerialFactoryTransport,
)
from hwval_factory.bench.sim import SimFactoryDevice, SimFactoryTransport

SIM_PROBE_SERIAL = "SIMULATED-STLINK"
SIM_PORT = "simulated://uart"


def _first_existing(paths: list[Path]) -> str:
    for path in paths:
        if path.is_file():
            return str(path)
    return ""


class BenchState(QObject):
    """One place for the selected probe, port, key and files; pages listen to ``changed``."""

    changed = Signal()

    def __init__(
        self, data_dir: Path, *, simulate: bool = False, settings: QSettings | None = None
    ) -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.simulate = simulate
        self.settings = settings or QSettings("SoCcentric", "HWVAL Factory Bench")
        self.probe_serial = ""
        self.port = ""
        self.baud = 115200
        self.info: FactoryInfo | None = None
        self.sim_device = SimFactoryDevice()
        self.sim_boot_flashed = False
        self.detected = detect.DetectResult([], [])

        workspace = self._workspace()
        self.key_path = self._load(
            "key_path", _first_existing([workspace / "bootloader/keys/dev-signing-key.pem"])
        )
        self.boot_image = self._load(
            "boot_image", _first_existing([workspace / "bootloader/build/boot.elf"])
        )
        self.factory_image = self._load(
            "factory_image", _first_existing([workspace / "factory-firmware/build/factory.img"])
        )
        self.security_counter = int(self._load("security_counter", "1") or 1)
        self.csv_path = self._load("csv_path", str(self.data_dir / "factory.csv"))
        self.cal_path = self._load("cal_path", "")
        self.diag_path = self._load("diag_path", "")

    @staticmethod
    def _workspace() -> Path:
        root = flashing.bootloader_root()
        return root.parent if root else Path.cwd()

    def _load(self, name: str, default: str) -> str:
        try:
            value = self.settings.value(name, default)
        except Exception:
            return default
        return str(value) if value else default

    def remember(self, name: str, value: object) -> None:
        setattr(self, name, value)
        with contextlib.suppress(Exception):
            self.settings.setValue(name, value)
        self.changed.emit()

    # -- connection ---------------------------------------------------
    def refresh_detection(self) -> detect.DetectResult:
        if self.simulate:
            probe = detect.StLink(SIM_PROBE_SERIAL, 0x374B, "Simulated ST-LINK/V2-1", SIM_PORT)
            port = detect.UartPort(
                SIM_PORT, "Simulated virtual COM port", 0x0483, 0x374B, SIM_PROBE_SERIAL, True
            )
            self.detected = detect.DetectResult([probe], [port])
        else:
            self.detected = detect.detect()
        probes = {probe.serial for probe in self.detected.probes}
        if self.probe_serial not in probes:
            self.probe_serial = (
                self.detected.probes[0].serial if len(self.detected.probes) == 1 else ""
            )
        usable = {port.device for port in self.detected.ports}
        if self.port not in usable:
            stlink_ports = [port.device for port in self.detected.ports if port.is_stlink]
            self.port = stlink_ports[0] if len(stlink_ports) == 1 else ""
        self.changed.emit()
        return self.detected

    def set_simulate(self, value: bool) -> None:
        self.simulate = value
        self.probe_serial = ""
        self.port = ""
        self.info = None
        self.sim_device = SimFactoryDevice()
        self.sim_boot_flashed = False
        self.refresh_detection()

    def select(self, probe_serial: str | None = None, port: str | None = None) -> None:
        if probe_serial is not None:
            self.probe_serial = probe_serial
        if port is not None:
            self.port = port
        self.changed.emit()

    def open_link(self, timeout: float = 1.0) -> FactoryLink:
        if self.simulate:
            return FactoryLink(SimFactoryTransport(self.sim_device), timeout=timeout, retries=1)
        if not self.port:
            raise RuntimeError("choose a UART port on the Detect page first")
        return FactoryLink(SerialFactoryTransport(self.port, self.baud), timeout=timeout)

    def sim_device_info(self) -> FactoryInfo:
        device = self.sim_device
        return FactoryInfo(device.name, device.version, device.build, device.uid)

    def set_info(self, info: FactoryInfo | None) -> None:
        self.info = info
        self.changed.emit()

    def database(self) -> csvdb.FactoryCsv:
        return csvdb.FactoryCsv(self.csv_path)
