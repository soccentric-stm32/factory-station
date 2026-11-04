"""Fixture simulator with the real flash layout and HWLINK protocol.

The simulated chip enforces sector erase granularity, write-only-after-erase,
write protection, RDP and the nDBANK mass-erase rule.  The HWLINK device is a
subclass of Studio's FlatBuffers simulator, so the host protocol path has no
``if simulation`` branch.
"""

from __future__ import annotations

import hashlib
import random
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hostcore import layout
from hostcore.images import ImageError, make_metadata_record, parse_image, parse_metadata_record
from hostcore.openocd import OpenOcdError, OptionBytes
from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.BootOutcome import BootOutcome
from hostcore.protocol.generated.hwlink.FlashMode import FlashMode
from hostcore.protocol.generated.hwlink.HealthState import HealthState
from hostcore.protocol.generated.hwlink.Role import Role
from hostcore.protocol.generated.hwlink.Slot import Slot
from hostcore.protocol.generated.hwlink.TestVerdict import TestVerdict
from hostcore.protocol.generated.hwlink.TrialState import TrialState
from hostcore.simulator_device import SimulatedDevice as BaseSimulatedDevice
from hwval_factory.identity import (
    PROVISIONING_SECTOR,
    PROVISIONING_SECTOR_SWAPPED,
    ParsedRecord,
    RecordError,
    parse_record,
)

FLASH_BASE = layout.BANK1_BASE
FLASH_SIZE = layout.FLASH_SIZE
DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FF0F442
UID_BASE = 0x1FF0F420
OPTCR = 0x40023C14
OPTCR1 = 0x40023C18

DEFAULT_OPTCR = 0x0FFFAAE4
DEFAULT_OPTCR1 = 0x00400080

# F76x 1 MB bank: 4 x 16 KB, 1 x 64 KB, 7 x 128 KB (matches boot_layout.h).
_SECTOR_SIZES = [0x4000] * 4 + [0x10000] + [0x20000] * 7


def sector_table() -> list[tuple[int, int, int]]:
    """Return ``(physical_sector, address, size)`` for both banks."""
    table: list[tuple[int, int, int]] = []
    for bank in range(2):
        address = layout.BANK1_BASE + bank * layout.BANK_SIZE
        for index, size in enumerate(_SECTOR_SIZES):
            table.append((bank * 12 + index, address, size))
            address += size
    return table


SECTORS = sector_table()


def sector_for(address: int) -> tuple[int, int, int] | None:
    for sector, start, size in SECTORS:
        if start <= address < start + size:
            return sector, start, size
    return None


def _rdp_level(optcr: int) -> int:
    raw = (optcr >> 8) & 0xFF
    if raw == 0xCC:
        return 2
    if raw == 0xAA:
        return 0
    return 1


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
    written: set[int] = field(default_factory=set)
    optcr: int = DEFAULT_OPTCR
    optcr1: int = DEFAULT_OPTCR1
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

    # -- flash state ---------------------------------------------------
    def _wrp_mask(self) -> int:
        return (self.optcr >> 16) & 0xFFF

    def sector_protected(self, sector: int) -> bool:
        index = sector % 12
        return not bool(self._wrp_mask() & (1 << index))

    def _check_writable(self, address: int, data: bytes) -> None:
        found = sector_for(address)
        if found is None:
            raise OpenOcdError(f"write outside flash: {address:#010x}")
        if self.sector_protected(found[0]):
            raise OpenOcdError(f"flash sector {found[0]} is write-protected")
        for index, byte in enumerate(data):
            target = address + index
            if target in self.written and byte != 0xFF:
                raise OpenOcdError(
                    f"write to non-erased flash at {target:#010x}; erase the sector first"
                )

    def offset_ok(self, address: int) -> bool:
        return FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE

    def write(self, address: int, data: bytes) -> None:
        if not self.offset_ok(address) or not self.offset_ok(address + max(0, len(data) - 1)):
            raise OpenOcdError(f"write outside flash: {address:#010x}")
        self._check_writable(address, data)
        for index, byte in enumerate(data):
            target = address + index
            self.flash[target] = byte
            self.written.add(target)

    def read(self, address: int, length: int) -> bytes:
        return bytes(self.flash.get(address + index, 0xFF) for index in range(length))

    def erase_sector(self, sector: int) -> None:
        for number, start, size in SECTORS:
            if number != sector:
                continue
            for address in range(start, start + size):
                self.flash.pop(address, None)
                self.written.discard(address)
            return
        raise OpenOcdError(f"unknown flash sector {sector}")

    def erase_address(self, address: int, length: int) -> None:
        if length <= 0:
            return
        touched: set[int] = set()
        for current in range(address, address + length):
            found = sector_for(current)
            if found is None:
                raise OpenOcdError(f"erase outside flash: {current:#010x}")
            touched.add(found[0])
        for sector in touched:
            self.erase_sector(sector)

    def erase(self) -> None:
        self.flash.clear()
        self.written.clear()
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

    # -- metadata / identity ------------------------------------------
    def boot_metadata(self) -> dict[str, Any]:
        header = self.read(layout.META_A_BASE, layout.META_HEADER_SIZE)
        magic = struct.unpack_from("<I", header)[0] if len(header) >= 4 else 0
        present = magic == layout.IMAGE_MAGIC
        verified = parse_metadata_record(
            self.read(layout.META_A_BASE + layout.META_VERIFIED_OFFSET, layout.META_RECORD_SIZE)
        )
        activated = parse_metadata_record(
            self.read(layout.META_A_BASE + layout.META_ACTIVATED_OFFSET, layout.META_RECORD_SIZE)
        )
        confirmed = parse_metadata_record(
            self.read(layout.META_A_BASE + layout.META_CONFIRMED_OFFSET, layout.META_RECORD_SIZE)
        )
        image_crc = struct.unpack_from("<I", header, 20)[0] if present else 0
        version = struct.unpack_from("<I", header, 12)[0] if present else 0
        image_size = struct.unpack_from("<I", header, 16)[0] if present else 0
        name = header[56:88].rstrip(b"\0").decode("ascii", "replace") if present else ""
        valid = (
            present
            and verified == (layout.META_VERIFIED_MAGIC, image_crc, True)
            and activated[0] == layout.META_ACTIVATED_MAGIC
            and activated[2]
        )
        return {
            "present": present,
            "valid": valid,
            "confirmed": confirmed[0] == layout.META_CONFIRMED_MAGIC and confirmed[2],
            "version": version,
            "image_size": image_size,
            "image_crc32": image_crc,
            "sequence": activated[1],
            "name": name,
        }

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
        boot = self.boot_metadata()
        if self.mode != "app":
            return {
                "uid": self.uid.hex().upper(),
                "serial": "",
                "slot": "",
                "confirmed": False,
            }
        return {
            "uid": self.uid.hex().upper(),
            "serial": "" if record is None else record.serial_number,
            "product_id": "" if record is None else record.product_id,
            "hw_revision": "" if record is None else record.hw_revision,
            "mac": "" if record is None else record.mac,
            "record_revision": 0 if record is None else record.revision,
            "slot": "A" if boot["valid"] else "",
            "confirmed": bool(boot["valid"] and boot["confirmed"]),
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
        self._erased_mass = False

    def _log(self, text: str) -> None:
        self.log.append(text)
        if self.log_callback:
            self.log_callback(text)

    def _maybe_fault(self) -> None:
        if self.fault_rate and self.random.random() < self.fault_rate:
            self.chip.inserted = False
            raise SimulatedFault("simulated USB disconnect")

    def _check_debug_read(self, address: int) -> None:
        if _rdp_level(self.chip.optcr) >= 1 and self.chip.offset_ok(address):
            raise OpenOcdError("flash is read-protected (RDP level 1)")

    def start(self) -> None:
        if not self.chip.inserted:
            raise SimulatedFault("simulated probe is not connected")
        if _rdp_level(self.chip.optcr) == 2:
            raise SimulatedFault("probe cannot connect: RDP level 2")
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
            return f"0x{address:08x}: " + " ".join(f"0x{value:08x}" for value in values)
        if text.startswith("mdh "):
            match = re.match(r"mdh\s+(0x[0-9A-Fa-f]+)(?:\s+(\d+))?", text)
            if not match:
                raise OpenOcdError("bad mdh command")
            address = int(match.group(1), 16)
            count = int(match.group(2) or "1")
            values = [self.read_u16(address + 2 * i) for i in range(count)]
            return f"0x{address:08x}: " + " ".join(f"0x{value:04x}" for value in values)
        if text.startswith("flash erase_address"):
            parts = text.split()
            self.erase_address(int(parts[2], 16), int(parts[3], 16))
            return "erased"
        if text.startswith("flash erase_sector"):
            parts = text.split()
            bank, first, last = int(parts[2]), int(parts[3]), int(parts[4])
            if last > 11:
                last = 11
            for index in range(first, last + 1):
                self.chip.erase_sector(bank * 12 + index)
            return "erased"
        if text.startswith("stm32f2x options_write"):
            parts = text.split()
            if len(parts) >= 6:
                user = int(parts[3], 16)
                boot0 = int(parts[4], 16)
                boot1 = int(parts[5], 16)
                self.write_option_bytes(user, (boot0 & 0xFFFF) | ((boot1 & 0xFFFF) << 16))
            return "write options complete"
        if text.startswith("reset") or text.startswith("halt"):
            return ""
        if text.startswith("shutdown"):
            self.stop()
            return ""
        if text.startswith(("verify_image", "program", "load_image", "flash write_image")):
            raise OpenOcdError("use the image methods in the simulator")
        return ""

    def halt(self) -> None:
        self._maybe_fault()

    def reset(self, halt: bool = False) -> None:
        self._maybe_fault()
        if not halt and self.chip.mode == "app":
            self._log("simulated reset: BOOT selects slot A")

    def mass_erase(self) -> None:
        self._maybe_fault()
        self.chip.erase()

    def erase_address(self, address: int, length: int) -> None:
        self._maybe_fault()
        self.chip.erase_address(address, length)

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
            if self.chip.read(address, len(image.data)) != image.data:
                raise OpenOcdError(f"verify failed at {address:#010x}")
            return
        for segment in image.segments:
            if self.chip.read(segment.address, len(segment.data)) != segment.data:
                raise OpenOcdError(f"verify failed at {segment.address:#010x}")

    def read_u32(self, address: int) -> int:
        self._maybe_fault()
        if address == DBGMCU_IDCODE:
            return (self.chip.rev_id << 16) | (self.chip.dev_id & 0xFFF)
        if address == OPTCR:
            return self.chip.optcr
        if address == OPTCR1:
            return self.chip.optcr1
        if UID_BASE <= address < UID_BASE + 12:
            offset = address - UID_BASE
            return int.from_bytes(self.chip.uid[offset : offset + 4], "little")
        if self.chip.offset_ok(address):
            self._check_debug_read(address)
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
            self._check_debug_read(address)
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
        new_rdp = _rdp_level(optcr)
        if new_rdp == 2:
            raise OpenOcdError("RDP level 2 is never permitted")
        previous = self.chip.optcr
        previous_rdp = _rdp_level(previous)
        bank_changed = ((previous >> 29) & 1) != ((optcr >> 29) & 1)
        self.chip.optcr = optcr & 0xFFFFFFFF
        self.chip.optcr1 = optcr1 & 0xFFFFFFFF
        if bank_changed or (previous_rdp == 1 and new_rdp == 0):
            self.chip.erase()
            self._log("option-byte bank/RDP change mass-erased the simulated chip")

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


class FactorySimulatedDevice(BaseSimulatedDevice):
    """HWLINK role simulator backed by a :class:`SimulatedChip`."""

    def __init__(self, chip: SimulatedChip, **kwargs: Any) -> None:
        super().__init__(role="hwval", **kwargs)
        self.chip = chip
        self._override_version = 0

    def set_role(self, role: str, firmware: str, version: int, image_hash: str = "") -> None:
        key = role.strip().upper()
        self.role = {
            "HWVAL": Role.HWVAL,
            "APP": Role.DEMO,
            "DEMO": Role.DEMO,
            "BOOT": Role.BOOTLOADER,
            "BOOTLOADER": Role.BOOTLOADER,
        }.get(key, Role.HWVAL)
        self.role_name = key.lower()
        self._override_version = int(version or 0)

    def _firmware_for_role(self) -> tuple[str, str]:
        if self.role == Role.BOOTLOADER:
            return "BOOT", "0.1.0"
        if self.role == Role.DEMO:
            name = self.chip.app_image_name or "stm32-application"
            return name, _version_text(self.chip.app_image_version or self._override_version)
        name = self.chip.test_image_name or "HWVAL"
        return name, _version_text(self.chip.test_image_version or self._override_version)

    def _hello_reply(self) -> bytes:
        name, version = self._firmware_for_role()
        flash_mode = FlashMode.DUAL_BANK if (self.chip.optcr >> 29) & 1 == 0 else FlashMode.SINGLE_BANK
        return codec.build_hello_reply(
            role=self.role,
            firmware_name=name,
            version=version,
            commit="simulated",
            build_time="simulated",
            uid=(int.from_bytes(self.chip.uid[0:4], "little") << 32) | int.from_bytes(self.chip.uid[4:8], "little"),
            dev_id=self.chip.dev_id,
            rev_id=self.chip.rev_id,
            flash_mode=flash_mode,
            detail="simulated device",
        )

    def _on_26(self, table: Any, seq: int) -> list[bytes]:  # GetDeviceInfo
        name, version = self._firmware_for_role()
        info = {
            "firmware_name": name,
            "version": version,
            "commit": "simulated",
            "build_time": "simulated",
            "srs_id": "SRS-HWVAL-F767",
            "dev_id": self.chip.dev_id,
            "rev_id": self.chip.rev_id,
            "cpuid": 0x411FC271,
            "flash_size_kb": self.chip.flash_kb,
            "package": "LQFP144",
            "uid": _uid_word_hex(self.chip.uid),
            "optcr": self.chip.optcr,
            "optcr1": self.chip.optcr1,
            "sysclk_hz": 216_000_000,
            "hclk_hz": 216_000_000,
            "pclk1_hz": 54_000_000,
            "pclk2_hz": 108_000_000,
            "apb1_tim_hz": 108_000_000,
            "apb2_tim_hz": 216_000_000,
            "pllq_hz": 48_000_000,
            "reset_cause": 0x04000000,
            "reset_cause_text": "power-on reset",
            "uptime_ms": self.uptime_ms(),
        }
        return self._reply(codec.build_device_info(info), seq)

    def _on_15(self, table: Any, seq: int) -> list[bytes]:  # UpdateEnd
        """Program the uploaded image into the simulated slot A flash."""
        if not self.update.active:
            return super()._on_15(table, seq)
        header = self.update.header
        data = bytes(self.update.data)

        if len(data) != int(header.get("image_size", 0)) or (
            zlib.crc32(data) & 0xFFFFFFFF
        ) != int(header.get("image_crc32", 0)):
            return super()._on_15(table, seq)
        self.chip.erase_address(layout.SLOT_A_BASE, max(1, len(data)))
        self.chip.write(layout.SLOT_A_BASE, data)
        self.chip.erase_address(layout.META_A_BASE, layout.META_SIZE)
        self.chip.write(layout.META_A_BASE, _ibwh_header_bytes(header))
        self.chip.write(
            layout.META_A_BASE + layout.META_VERIFIED_OFFSET,
            make_metadata_record(layout.META_VERIFIED_MAGIC, int(header.get("image_crc32", 0))),
        )
        self.chip.app_image_name = str(header.get("name", "")) or "stm32-application"
        self.chip.app_image_version = int(header.get("version", 0))
        return super()._on_15(table, seq)

    def _on_17(self, table: Any, seq: int) -> list[bytes]:  # Activate
        result = super()._on_17(table, seq)
        if self.chip.boot_metadata()["present"]:
            self.chip.write(
                layout.META_A_BASE + layout.META_ACTIVATED_OFFSET,
                make_metadata_record(layout.META_ACTIVATED_MAGIC, 1),
            )
            self.chip.write(
                layout.META_A_BASE + layout.META_CONFIRMED_OFFSET,
                make_metadata_record(layout.META_CONFIRMED_MAGIC, 0),
            )
            self.chip.mode = "app"
            self.set_role(
                "APP",
                self.chip.app_image_name or "stm32-application",
                self.chip.app_image_version,
            )
        return result

    def _boot_payload(self) -> dict[str, Any]:
        boot = self.chip.boot_metadata()
        active = Slot.A if boot["valid"] else Slot.UNKNOWN
        slots = [
            {
                "slot": Slot.A,
                "present": bool(boot["present"]),
                "valid": bool(boot["valid"]),
                "pending": False,
                "confirmed": bool(boot["confirmed"]),
                "rejected": False,
                "sequence": int(boot["sequence"]),
                "trial_count": 1,
                "version": int(boot["version"]),
                "build_timestamp": 0,
                "git_commit": "",
                "name": str(boot["name"]),
                "image_size": int(boot["image_size"]),
                "detail": "confirmed" if boot["valid"] else "no valid image",
            },
            {
                "slot": Slot.B,
                "present": False,
                "valid": False,
                "pending": False,
                "confirmed": False,
                "rejected": False,
                "sequence": 0,
                "trial_count": 0,
                "version": 0,
                "build_timestamp": 0,
                "git_commit": "",
                "name": "",
                "image_size": 0,
                "detail": "empty",
            },
        ]
        return {
            "active_slot": active,
            "last_outcome": BootOutcome.BOOTED if boot["valid"] else BootOutcome.UNKNOWN,
            "rejected_slot": Slot.UNKNOWN,
            "flash_mode": FlashMode.DUAL_BANK if (self.chip.optcr >> 29) & 1 == 0 else FlashMode.SINGLE_BANK,
            "slots": slots,
        }

    def _on_11(self, table: Any, seq: int) -> list[bytes]:  # GetBootInfo
        return self._reply(codec.build_boot_info(self._boot_payload()), seq)

    def _on_23(self, table: Any, seq: int) -> list[bytes]:  # GetStatus
        boot = self.chip.boot_metadata()
        name, version = self._firmware_for_role()
        status = {
            "firmware_name": name,
            "version": version,
            "commit": "simulated",
            "build_time": "simulated",
            "uid": (int.from_bytes(self.chip.uid[0:4], "little") << 32) | int.from_bytes(self.chip.uid[4:8], "little"),
            "uptime_ms": self.uptime_ms(),
            "slot": Slot.A if boot["valid"] else Slot.UNKNOWN,
            "trial": TrialState.CONFIRMED if boot["confirmed"] else TrialState.PENDING,
            "trial_count": 1,
            "health": HealthState.PASSED,
            "reset_cause": 0x04000000,
            "reset_cause_text": "power-on reset",
            "fault": {
                "present": False,
                "reason": "",
                "cfsr": 0,
                "hfsr": 0,
                "mmfar": 0,
                "bfar": 0,
                "pc": 0,
                "lr": 0,
                "task": "",
                "file": "",
                "line": 0,
            },
            "leds": [],
            "tasks": [],
            "heap_free": 0,
            "heap_min_free": 0,
            "protocol_major": 1,
            "protocol_minor": 1,
        }
        return self._reply(codec.build_status(status), seq)

    def _sample_result(self, test: dict[str, Any], iteration: int = 1) -> dict[str, Any]:
        result = super()._sample_result(test, iteration)
        test_id = str(test["id"])
        if test_id == "PWR.001":
            result["measured"] = {
                "value": 3265.0,
                "unit": "mV",
                "text": "vdda=3265 mV vbat=3260 mV",
                "valid": True,
            }
            result["limits"] = {
                "min": 3135.0,
                "max": 3465.0,
                "unit": "mV",
                "text": "3.3 V +-5 %",
                "valid": True,
            }
            result["result"] = TestVerdict.PASS
            result["reason"] = ""
        elif test_id == "ADC.002":
            result["measured"] = {
                "value": 1212.0,
                "unit": "mV",
                "text": "VREFINT=1212 mV temp=25.0 C",
                "valid": True,
            }
        return result


def _ibwh_header_bytes(header: dict[str, Any]) -> bytes:
    raw = bytearray(layout.IMAGE_HEADER_SIZE)
    struct.pack_into(
        "<IHHIIIIIII",
        raw,
        0,
        int(header.get("magic", layout.IMAGE_MAGIC)),
        int(header.get("header_version", layout.IMAGE_HEADER_VERSION)),
        int(header.get("header_size", layout.IMAGE_HEADER_SIZE)),
        int(header.get("image_type", layout.IMAGE_TYPE_APPLICATION)),
        int(header.get("version", 0)),
        int(header.get("image_size", 0)),
        int(header.get("image_crc32", 0)),
        int(header.get("load_address", layout.APP_LOAD_ADDR)),
        int(header.get("entry_address", 0)),
        int(header.get("build_timestamp", 0)),
    )
    commit = bytes(header.get("git_commit") or b"")[:20].ljust(20, b"\0")
    raw[36:56] = commit
    name = str(header.get("name", "")).encode("ascii", "replace")[:32].ljust(32, b"\0")
    raw[56:88] = name
    struct.pack_into("<I", raw, 252, zlib.crc32(raw[:252]) & 0xFFFFFFFF)
    return bytes(raw)


def _uid_word_hex(uid: bytes) -> str:
    if len(uid) < 12:
        return uid.hex().upper()
    return "".join(f"{int.from_bytes(uid[i : i + 4], 'little'):08X}" for i in (0, 4, 8))


def _version_text(value: int) -> str:
    return f"{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{value & 0xFF}"


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
        self.device = FactorySimulatedDevice(
            self.chip,
            auto_answer=False,
            tick=0.005,
        )
        self.fail_tests = fail_tests or set()
        self.prompt_tests = prompt_tests or set()

    def _mark_failures(self) -> None:
        # Studio's simulator has no fail-injection hook; patch the sample
        # function for this fixture by wrapping the device method.
        base = self.device._sample_result

        def sample(test: dict[str, Any], iteration: int = 1) -> dict[str, Any]:
            result = base(test, iteration)
            if str(test.get("id")) in self.fail_tests:
                result["result"] = TestVerdict.FAIL
                result["reason"] = f"simulated failure of {test.get('id')}"
            return result

        self.device._sample_result = sample  # type: ignore[method-assign]

    def prepare_test_image(self, sha256: str, name: str, version: int, path: str | Path) -> None:
        self._mark_failures()
        self.openocd.mark_test_image(path, sha256, name, version)
        self.device.set_role("HWVAL", name or "hwval", version, sha256)

    def prepare_app_image(self, sha256: str, name: str, version: int, path: str | Path) -> None:
        self.openocd.mark_app_image(path, sha256, name, version)
        self.device.set_role("APP", name or "app", version, sha256)

    def select_board(self, pcb_serial: str) -> None:
        if self.chip.board_serial == pcb_serial:
            return
        self.chip.board_serial = pcb_serial
        digest = hashlib.sha256(f"{self.fixture}:{pcb_serial}".encode()).digest()
        self.chip.uid = digest[:12]
        self.chip.erase()
        self.chip.optcr = DEFAULT_OPTCR
        self.chip.optcr1 = DEFAULT_OPTCR1
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
