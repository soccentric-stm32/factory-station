"""Fixture simulator: OpenOCD and HWLINK without hardware (ARCH-005).

Each fixture gets a :class:`SimulationBank` containing a simulated STM32F767ZI
chip and an HWLINK device.  ``SimulatedOpenOcd`` speaks the same interface as
the real OpenOCD controller, and the HWLINK device is the shared
``hostcore.hwlink.SimulatedDevice``.  The simulator is deterministic but can
inject USB faults and test failures for demonstrations and robustness runs.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hostcore.hwlink import SimulatedDevice
from hostcore.images import ImageError, parse_image
from hostcore.openocd import OpenOcdError, OptionBytes
from hwval_factory.identity import (
    PROVISIONING_SECTOR,
    PROVISIONING_SECTOR_SWAPPED,
    ParsedRecord,
    RecordError,
    parse_record,
)

FLASH_BASE = 0x08000000
FLASH_SIZE = 2 * 1024 * 1024
DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FF0F442
UID_BASE = 0x1FF0F420
OPTCR = 0x40023C14
OPTCR1 = 0x40023C18


class SimulatedFault(OpenOcdError):
    """An injected USB/probe/power fault."""


@dataclass(slots=True)
class SimulatedChip:
    name: str = "F1"
    dev_id: int = 0x451
    rev_id: int = 0x1001
    flash_kb: int = 2048
    uid: bytes = b"\x00" * 12
    flash: dict[int, int] = field(default_factory=dict)
    optcr: int = 0x0FFF00FC
    optcr1: int = 0x00000FFF
    mode: str = "blank"
    test_image_sha: str = ""
    test_image_name: str = ""
    test_image_version: int = 0
    app_image_sha: str = ""
    app_image_name: str = ""
    app_image_version: int = 0
    record: ParsedRecord | None = None
    record_address: int = PROVISIONING_SECTOR
    probe_serial: str = ""
    inserted: bool = True
    board_serial: str = ""

    @classmethod
    def create(cls, name: str, seed: str = "") -> SimulatedChip:
        digest = hashlib.sha256((seed or name).encode("utf-8")).digest()
        return cls(name=name, uid=digest[:12])

    def offset_ok(self, address: int) -> bool:
        return FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE

    def write(self, address: int, data: bytes) -> None:
        if not self.offset_ok(address):
            raise OpenOcdError(f"write outside flash: {address:#010x}")
        for index, byte in enumerate(data):
            self.flash[address + index] = byte

    def read(self, address: int, length: int) -> bytes:
        return bytes(self.flash.get(address + index, 0xFF) for index in range(length))

    def erase(self) -> None:
        self.flash.clear()
        self.mode = "blank"
        self.test_image_sha = ""
        self.test_image_name = ""
        self.test_image_version = 0
        self.app_image_sha = ""
        self.app_image_name = ""
        self.app_image_version = 0
        self.record = None

    def option_bytes(self) -> OptionBytes:
        return OptionBytes(optcr=self.optcr, optcr1=self.optcr1)

    def parse_record(self) -> ParsedRecord | None:
        candidates = [self.record_address, PROVISIONING_SECTOR, PROVISIONING_SECTOR_SWAPPED]
        seen: set[int] = set()
        for address in candidates:
            if address in seen:
                continue
            seen.add(address)
            raw = self.read(address, 4096)
            try:
                record = parse_record(raw, expect_uid=self.uid)
            except RecordError:
                continue
            if record.serial_number:
                self.record = record
                return record
        return None

    def identity(self) -> dict[str, Any]:
        record = self.parse_record()
        if self.mode != "app":
            return {
                "uid": self.uid.hex().upper(),
                "serial": "",
                "slot": "",
                "confirmed": False,
            }
        if record is None:
            return {
                "uid": self.uid.hex().upper(),
                "serial": "",
                "slot": "A",
                "confirmed": False,
            }
        return {
            "uid": self.uid.hex().upper(),
            "serial": record.serial_number,
            "product_id": record.product_id,
            "hw_revision": record.hw_revision,
            "mac": record.mac,
            "record_revision": record.revision,
            "slot": "A",
            "confirmed": True,
            "firmware_name": self.app_image_name,
            "firmware_version": self.app_image_version,
        }


class SimulatedOpenOcd:
    def __init__(
        self,
        chip: SimulatedChip,
        *,
        log_callback: Any | None = None,
        fault_rate: float = 0.0,
        seed: int = 0,
    ) -> None:
        self.chip = chip
        self.log: list[str] = []
        self.log_callback = log_callback
        self.fault_rate = fault_rate
        self.random = random.Random(seed or hash(chip.name) & 0xFFFF)
        self._started = False

    def _log(self, text: str) -> None:
        self.log.append(text)
        if self.log_callback:
            self.log_callback(text)

    def _maybe_fault(self) -> None:
        if self.fault_rate and self.random.random() < self.fault_rate:
            self.chip.inserted = False
            raise SimulatedFault("simulated USB disconnect")

    def start(self) -> None:
        if not self.chip.inserted:
            raise SimulatedFault("simulated probe is not connected")
        self._started = True
        self._log(f"OpenOCD (simulated) attached to {self.chip.name}")

    def stop(self) -> None:
        self._started = False
        self._log("OpenOCD (simulated) stopped")

    close = stop

    def command(self, command: str, timeout: float = 20.0) -> str:
        self._maybe_fault()
        self._log("> " + command)
        text = command.strip()
        if text.startswith("mdw "):
            match = re.match(r"mdw\s+(0x[0-9A-Fa-f]+)(?:\s+(\d+))?", text)
            if not match:
                raise OpenOcdError("bad mdw command")
            address = int(match.group(1), 16)
            count = int(match.group(2) or "1")
            values = [self.read_u32(address + 4 * i) for i in range(count)]
            words = " ".join(f"0x{value:08x}" for value in values)
            return f"0x{address:08x}: {words}"
        if text.startswith("reset"):
            return ""
        if text.startswith("halt"):
            return ""
        if text.startswith("flash erase_sector") or text.startswith("flash erase_address"):
            self.chip.erase()
            return "erased"
        if text.startswith("stm32f2x options_write"):
            parts = text.split()
            if len(parts) >= 4:
                self.write_option_bytes(int(parts[2], 16), int(parts[3], 16))
            return "options written"
        if text.startswith("shutdown"):
            self.stop()
            return ""
        if text.startswith("verify_image") or text.startswith("program") or text.startswith(
            "load_image"
        ):
            raise OpenOcdError("use the image methods in the simulator")
        return ""

    def halt(self) -> None:
        self._maybe_fault()

    def reset(self, halt: bool = False) -> None:
        self._maybe_fault()
        if not halt and self.chip.mode == "app":
            self._log("simulated reset: booting slot A")

    def mass_erase(self) -> None:
        self._maybe_fault()
        self.chip.erase()

    def _program_file(self, path: str | Path, address: int | None) -> tuple[str, int]:
        try:
            image = parse_image(path)
        except ImageError as exc:
            raise OpenOcdError(str(exc)) from exc
        if address is not None:
            self.chip.write(address, image.data)
        else:
            for segment in image.segments:
                self.chip.write(segment.address, segment.data)
        return image.sha256, image.version

    def program(self, path: str | Path, address: int | None = None, verify: bool = True) -> None:
        self._maybe_fault()
        self._program_file(path, address)
        if verify:
            self.verify_image(path, address)

    def verify_image(self, path: str | Path, address: int | None = None) -> None:
        self._maybe_fault()
        try:
            image = parse_image(path)
        except ImageError as exc:
            raise OpenOcdError(str(exc)) from exc
        if address is not None:
            actual = self.chip.read(address, len(image.data))
            if actual != image.data:
                raise OpenOcdError(f"verify failed at {address:#010x}")
            return
        for segment in image.segments:
            if self.chip.read(segment.address, len(segment.data)) != segment.data:
                raise OpenOcdError(f"verify failed at {segment.address:#010x}")

    def read_u32(self, address: int) -> int:
        self._maybe_fault()
        if address == DBGMCU_IDCODE:
            return (self.chip.rev_id << 16) | (self.chip.dev_id & 0xFFF)
        if address == FLASH_SIZE_REG:
            return self.chip.flash_kb
        if address == OPTCR:
            return self.chip.optcr
        if address == OPTCR1:
            return self.chip.optcr1
        if UID_BASE <= address < UID_BASE + 12:
            offset = address - UID_BASE
            return int.from_bytes(self.chip.uid[offset : offset + 4], "little")
        if self.chip.offset_ok(address):
            return int.from_bytes(self.chip.read(address, 4), "little")
        return 0

    def read_u16(self, address: int) -> int:
        self._maybe_fault()
        if address == FLASH_SIZE_REG:
            return self.chip.flash_kb
        return self.read_u32(address & ~1) & 0xFFFF

    def read_memory(self, address: int, length: int) -> bytes:
        self._maybe_fault()
        if UID_BASE <= address < UID_BASE + 12:
            start = address - UID_BASE
            return self.chip.uid[start : start + length].ljust(length, b"\0")
        if self.chip.offset_ok(address):
            return self.chip.read(address, length)
        return b"\0" * length

    def write_memory(self, address: int, data: bytes) -> None:
        self._maybe_fault()
        self.chip.write(address, data)

    def read_option_bytes(self) -> OptionBytes:
        self._maybe_fault()
        return self.chip.option_bytes()

    def write_option_bytes(self, optcr: int, optcr1: int) -> None:
        self._maybe_fault()
        rdp = (optcr >> 8) & 0xFF
        if rdp == 2:
            raise OpenOcdError("RDP level 2 is never permitted")
        previous_rdp = (self.chip.optcr >> 8) & 0xFF
        self.chip.optcr = optcr & 0xFFFFFFFF
        self.chip.optcr1 = optcr1 & 0xFFFFFFFF
        if previous_rdp == 1 and rdp == 0:
            self.chip.erase()
            self._log("RDP regression mass-erased the simulated chip")

    # Simulation-only helpers -----------------------------------------
    def mark_test_image(self, path: str | Path, sha256: str, name: str, version: int) -> None:
        self.chip.test_image_sha = sha256
        self.chip.test_image_name = name
        self.chip.test_image_version = version
        self.chip.mode = "test"

    def mark_app_image(self, path: str | Path, sha256: str, name: str, version: int) -> None:
        self.chip.app_image_sha = sha256
        self.chip.app_image_name = name
        self.chip.app_image_version = version
        self.chip.mode = "app"

    def mark_record_address(self, address: int) -> None:
        self.chip.record_address = address
        self.chip.record = None


class SimulationBank:
    """One simulated fixture: chip + OpenOCD + HWLINK firmware."""

    def __init__(
        self,
        fixture: str,
        *,
        fault_rate: float = 0.0,
        fail_tests: set[str] | None = None,
        prompt_tests: set[str] | None = None,
        seed: int = 0,
    ) -> None:
        self.fixture = fixture
        self.chip = SimulatedChip.create(fixture)
        self.openocd = SimulatedOpenOcd(
            self.chip, fault_rate=fault_rate, seed=seed or hash(fixture) & 0xFFFF
        )
        self.device = SimulatedDevice(
            role="HWVAL",
            firmware="hwval",
            version=0,
            image_hash="",
            identity=self.chip.identity,
            fail_tests=fail_tests or set(),
            prompt_tests=prompt_tests or set(),
        )
        self.device.logs = self.openocd.log

    def prepare_test_image(self, sha256: str, name: str, version: int, path: str | Path) -> None:
        self.openocd.mark_test_image(path, sha256, name, version)
        self.device.set_role("HWVAL", name or "hwval", version, sha256)

    def prepare_app_image(self, sha256: str, name: str, version: int, path: str | Path) -> None:
        self.openocd.mark_app_image(path, sha256, name, version)
        self.device.set_role("APP", name or "app", version, sha256)

    def select_board(self, pcb_serial: str) -> None:
        """Simulate a different physical board in this fixture.

        The UID is stable for the same PCB serial (retest/rework) and changes
        for a different board (so serial numbers are not reused across units).
        """
        if self.chip.board_serial == pcb_serial:
            return
        self.chip.board_serial = pcb_serial
        digest = hashlib.sha256(f"{self.fixture}:{pcb_serial}".encode()).digest()
        self.chip.uid = digest[:12]
        self.chip.erase()
        self.chip.optcr = 0x0FFF00FC
        self.chip.optcr1 = 0x00000FFF
        self.chip.record = None
        self.device.set_role("HWVAL", "hwval", 0, "")

    def power_cycle(self) -> None:
        self.chip.inserted = True

    def disconnect(self) -> None:
        self.chip.inserted = False


class SimulatedStation:
    def __init__(
        self,
        fixtures: list[str],
        *,
        fault_rate: float = 0.0,
        fail_tests: set[str] | None = None,
        prompt_tests: set[str] | None = None,
    ) -> None:
        self.banks: dict[str, SimulationBank] = {
            name: SimulationBank(
                name,
                fault_rate=fault_rate,
                fail_tests=fail_tests,
                prompt_tests=prompt_tests,
                seed=index + 1,
            )
            for index, name in enumerate(fixtures)
        }

    def bank(self, fixture: str) -> SimulationBank:
        return self.banks[fixture]

    def disconnect_all(self) -> None:
        for bank in self.banks.values():
            bank.disconnect()
