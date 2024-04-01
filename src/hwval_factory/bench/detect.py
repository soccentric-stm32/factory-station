"""Detect ST-LINK probes and UART ports."""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

ST_VENDOR_ID = 0x0483
# ST-LINK/V2-1 (0x374B), V3 (0x374E, 0x374F, 0x3753), V2 (0x3748), V3 mini (0x3752)
STLINK_PRODUCT_IDS = {0x3748, 0x374B, 0x374D, 0x374E, 0x374F, 0x3752, 0x3753}


@dataclass(slots=True)
class StLink:
    serial: str
    product_id: int
    description: str
    port: str | None  # the probe's virtual COM port, when it exposes one

    @property
    def label(self) -> str:
        name = self.description or f"ST-LINK {self.product_id:04X}"
        return f"{name} · {self.serial}" if self.serial else name


@dataclass(slots=True)
class UartPort:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial: str
    is_stlink: bool

    @property
    def label(self) -> str:
        suffix = " (ST-LINK VCP)" if self.is_stlink else ""
        return (
            f"{self.device} · {self.description}{suffix}"
            if self.description
            else self.device + suffix
        )


def _comports() -> list[object]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return list(list_ports.comports())


def list_uart_ports() -> list[UartPort]:
    """Serial ports on the PC, ST-LINK virtual COM ports first."""
    ports: list[UartPort] = []
    for info in _comports():
        vid = getattr(info, "vid", None)
        pid = getattr(info, "pid", None)
        ports.append(
            UartPort(
                device=str(getattr(info, "device", "")),
                description=str(getattr(info, "description", "") or ""),
                vid=vid,
                pid=pid,
                serial=str(getattr(info, "serial_number", "") or ""),
                is_stlink=vid == ST_VENDOR_ID,
            )
        )
    if not ports:
        for device in sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
            ports.append(UartPort(device, "serial port", None, None, "", False))
    ports.sort(key=lambda port: (not port.is_stlink, port.device))
    return ports


def _sysfs_probes(root: str = "/sys/bus/usb/devices") -> list[StLink]:
    """ST-LINK probes straight from the Linux USB tree (catches a probe with no VCP)."""
    probes: list[StLink] = []
    base = Path(root)
    if not base.is_dir():
        return probes
    for device in sorted(base.iterdir()):
        try:
            vid = int((device / "idVendor").read_text().strip(), 16)
            pid = int((device / "idProduct").read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        if vid != ST_VENDOR_ID or pid not in STLINK_PRODUCT_IDS:
            continue

        def read(name: str, folder: Path = device) -> str:
            try:
                return (folder / name).read_text().strip()
            except OSError:
                return ""

        probes.append(StLink(read("serial"), pid, read("product"), None))
    return probes


def list_stlinks() -> list[StLink]:
    """ST-LINK probes attached to this PC, with their virtual COM port if any."""
    by_serial: dict[str, StLink] = {}
    for probe in _sysfs_probes():
        by_serial[probe.serial or f"unknown-{len(by_serial)}"] = probe
    for port in list_uart_ports():
        if not port.is_stlink:
            continue
        key = port.serial or f"port-{port.device}"
        known = by_serial.get(key)
        if known is None:
            by_serial[key] = StLink(port.serial, port.pid or 0, port.description, port.device)
        else:
            known.port = port.device
    return list(by_serial.values())


@dataclass(slots=True)
class DetectResult:
    probes: list[StLink]
    ports: list[UartPort]

    @property
    def ready(self) -> bool:
        return bool(self.probes) and any(port.is_stlink for port in self.ports)


def detect() -> DetectResult:
    return DetectResult(list_stlinks(), list_uart_ports())
