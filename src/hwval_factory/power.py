"""Pluggable fixture power control (STN-005, OPEN-F06).

Supported drivers:

* ``none`` — no power control.
* ``usb-hub`` — per-port power with ``uhubctl`` (Linux).
* ``relay`` — a serial relay board, commands ``RELAY <n> ON|OFF|PULSE``.
* ``supply`` — a programmable supply speaking SCPI over serial.

The driver interface is deliberately small so a fixture vendor can plug in
another implementation without touching the station engine.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any


class PowerError(RuntimeError):
    """Power-control driver failed."""


class PowerDriver(ABC):
    @abstractmethod
    def on(self) -> None: ...

    @abstractmethod
    def off(self) -> None: ...

    def cycle(self, off_seconds: float = 1.0) -> None:
        self.off()
        time.sleep(off_seconds)
        self.on()

    def close(self) -> None:
        return None


class NullPower(PowerDriver):
    def on(self) -> None:
        return None

    def off(self) -> None:
        return None


class UsbHubPower(PowerDriver):
    def __init__(self, location: str, port: int) -> None:
        self.location = location
        self.port = port
        if shutil.which("uhubctl") is None:
            raise PowerError("uhubctl is not installed")

    def _run(self, action: str) -> None:
        proc = subprocess.run(
            ["uhubctl", "-l", self.location, "-p", str(self.port), "-a", action],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise PowerError(proc.stderr.decode("utf-8", "replace").strip())

    def on(self) -> None:
        self._run("on")

    def off(self) -> None:
        self._run("off")

    def cycle(self, off_seconds: float = 1.0) -> None:
        self._run("cycle")


class RelayPower(PowerDriver):
    def __init__(self, port: str, channel: int = 1, baudrate: int = 9600) -> None:
        self.port = port
        self.channel = channel
        self.baudrate = baudrate
        self._serial: Any = None

    def _connection(self) -> Any:
        if self._serial is None:
            try:
                import serial  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover
                raise PowerError("pyserial is required for relay power control") from exc
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
        return self._serial

    def _send(self, command: str) -> None:
        conn = self._connection()
        conn.write((command + "\r\n").encode("ascii"))
        conn.flush()

    def on(self) -> None:
        self._send(f"RELAY {self.channel} ON")

    def off(self) -> None:
        self._send(f"RELAY {self.channel} OFF")

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None


class SupplyPower(PowerDriver):
    def __init__(self, port: str, baudrate: int = 9600, channel: int = 1) -> None:
        self.port = port
        self.baudrate = baudrate
        self.channel = channel
        self._serial: Any = None

    def _connection(self) -> Any:
        if self._serial is None:
            try:
                import serial  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover
                raise PowerError("pyserial is required for supply power control") from exc
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
        return self._serial

    def _send(self, command: str) -> None:
        conn = self._connection()
        conn.write((command + "\n").encode("ascii"))
        conn.flush()

    def on(self) -> None:
        self._send(f"INST CH{self.channel}")
        self._send("OUTP ON")

    def off(self) -> None:
        self._send(f"INST CH{self.channel}")
        self._send("OUTP OFF")

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None


def create_power_driver(config: dict[str, Any]) -> PowerDriver:
    kind = str(config.get("driver") or config.get("type") or "none").lower()
    if kind in ("", "none", "null"):
        return NullPower()
    if kind in ("usb-hub", "usb_hub", "uhubctl"):
        return UsbHubPower(str(config.get("location") or ""), int(config.get("port") or 1))
    if kind == "relay":
        return RelayPower(
            str(config.get("port") or ""),
            int(config.get("channel") or 1),
            int(config.get("baudrate") or 9600),
        )
    if kind in ("supply", "programmable-supply"):
        return SupplyPower(
            str(config.get("port") or ""),
            int(config.get("baudrate") or 9600),
            int(config.get("channel") or 1),
        )
    raise PowerError(f"unknown power driver {kind!r}")
