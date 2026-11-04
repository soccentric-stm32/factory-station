"""Provisioning record, Appendix A (PRV-001…008).

The record lives at bank 2, sector 1 of the product flash (PRV-001):

* ``0x08104000`` when ``SWP_FB = 0``
* ``0x08004000`` when bank 2 is swapped in

Layout, little endian::

    magic       uint32   0x50525631 ("1VRP")
    format      uint16   1
    revision    uint16   +1 on every rework
    length      uint32   TLV area length
    tlv_crc32   uint32   CRC-32/ISO-HDLC over TLV area
    uid         12 bytes chip unique ID
    header_crc  uint32   CRC-32 over bytes 0..27
    tlvs        n bytes  type (u8), length (u8), value, … 0x00 terminator
"""

from __future__ import annotations

import struct
import time
import zlib
from dataclasses import dataclass, field

RECORD_MAGIC = 0x50525631
RECORD_FORMAT = 1
HEADER = struct.Struct("<IHHII12sI")
PROVISIONING_SECTOR = 0x08104000
PROVISIONING_SECTOR_SWAPPED = 0x08004000
MAX_TLV = 255

T_SERIAL = 0x01
T_PRODUCT = 0x02
T_HW_REV = 0x03
T_MFG_TIME = 0x04
T_STATION_FIXTURE = 0x05
T_PROFILE = 0x06
T_FIRMWARE_VERSION = 0x07
T_MAC = 0x10
T_CALIBRATION_MIN = 0x20
T_CALIBRATION_MAX = 0x2F
T_CUSTOMER_MIN = 0x40
T_CUSTOMER_MAX = 0x4F
T_CUSTOM_MIN = 0x80
T_CUSTOM_MAX = 0xFE

UNIT_CODES = {
    "": 0,
    "mV": 1,
    "V": 2,
    "kHz": 3,
    "Hz": 4,
    "C": 5,
    "raw": 6,
    "mA": 7,
    "%": 8,
    "ppm": 9,
    "s": 10,
    "ms": 11,
}
UNIT_NAMES = {value: key for key, value in UNIT_CODES.items()}

_SECRET_WORDS = ("secret", "private_key", "private key", "token", "password", "hsm")


class RecordError(RuntimeError):
    """Malformed or invalid provisioning record."""


@dataclass(slots=True)
class ProvisioningData:
    serial_number: str
    product_id: str
    hw_revision: str
    manufacturing_time: int
    station_fixture: str
    profile: str
    firmware_version: int
    uid: bytes
    mac: bytes | None = None
    calibration: dict[int, tuple[float, str]] = field(default_factory=dict)
    customer: dict[int, str] = field(default_factory=dict)
    custom: dict[str, str] = field(default_factory=dict)
    revision: int = 1

    @property
    def uid_hex(self) -> str:
        return self.uid.hex().upper()

    @property
    def mac_string(self) -> str:
        if not self.mac:
            return ""
        return ":".join(f"{b:02X}" for b in self.mac)


@dataclass(slots=True)
class ParsedRecord:
    magic: int
    format: int
    revision: int
    length: int
    tlv_crc32: int
    uid: bytes
    header_crc32: int
    fields: dict[int, bytes]
    blob: bytes = b""

    def text(self, field_type: int) -> str:
        raw = self.fields.get(field_type)
        if raw is None:
            return ""
        return raw.decode("utf-8", "replace").rstrip("\0")

    @property
    def serial_number(self) -> str:
        return self.text(T_SERIAL)

    @property
    def product_id(self) -> str:
        return self.text(T_PRODUCT)

    @property
    def hw_revision(self) -> str:
        return self.text(T_HW_REV)

    @property
    def profile(self) -> str:
        return self.text(T_PROFILE)

    @property
    def uid_hex(self) -> str:
        return self.uid.hex().upper()

    def uint32(self, field_type: int) -> int:
        raw = self.fields.get(field_type)
        if raw is None or len(raw) != 4:
            return 0
        return int.from_bytes(raw, "little")

    @property
    def manufacturing_time(self) -> int:
        return self.uint32(T_MFG_TIME)

    @property
    def firmware_version(self) -> int:
        return self.uint32(T_FIRMWARE_VERSION)

    @property
    def mac(self) -> str:
        raw = self.fields.get(T_MAC)
        if raw is None or len(raw) != 6:
            return ""
        return ":".join(f"{b:02X}" for b in raw)

    def calibration_value(self, field_type: int) -> tuple[float, str] | None:
        raw = self.fields.get(field_type)
        if raw is None or len(raw) < 6:
            return None
        value_kind, unit_code = raw[0], raw[1]
        if value_kind == 0:
            (value,) = struct.unpack("<i", raw[2:6])
            number = float(value)
        else:
            (number,) = struct.unpack("<f", raw[2:6])
        return number, UNIT_NAMES.get(unit_code, "")

    def custom_value(self, key: str) -> str | None:
        prefix = key.encode("utf-8") + b"\0"
        for field_type, value in self.fields.items():
            if T_CUSTOM_MIN <= field_type <= T_CUSTOM_MAX and value.startswith(prefix):
                return value.split(b"\0", 1)[1].decode("utf-8", "replace")
        return None


def _tlv(field_type: int, value: bytes) -> bytes:
    if not 1 <= field_type <= 0xFE:
        raise RecordError(f"invalid TLV type {field_type:#x}")
    if len(value) > MAX_TLV:
        raise RecordError(f"TLV {field_type:#x} exceeds {MAX_TLV} bytes")
    return bytes((field_type, len(value))) + value


def _ascii(value: str, limit: int, name: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        raise RecordError(f"{name} exceeds {limit} bytes")
    return encoded


def _unit_value(value: float, unit: str) -> bytes:
    code = UNIT_CODES.get(unit)
    if code is None:
        raise RecordError(f"unsupported calibration unit {unit!r}")
    if float(value).is_integer():
        return struct.pack("<BBi", 0, code, int(value))
    return struct.pack("<BBf", 1, code, float(value))


def _check_secret(key: str) -> None:
    lowered = key.lower()
    if any(word in lowered for word in _SECRET_WORDS):
        raise RecordError(
            "provisioning records must not contain secret keys in v0.1; "
            "see OPEN-F04 (PRV-007)"
        )


def build_record(data: ProvisioningData) -> bytes:
    if len(data.uid) != 12:
        raise RecordError("chip UID must be 12 bytes")
    if not data.serial_number:
        raise RecordError("serial number is mandatory")
    if not data.product_id:
        raise RecordError("product ID is mandatory")

    tlvs = bytearray()
    tlvs += _tlv(T_SERIAL, _ascii(data.serial_number, 32, "serial number"))
    tlvs += _tlv(T_PRODUCT, _ascii(data.product_id, 32, "product ID"))
    tlvs += _tlv(T_HW_REV, _ascii(data.hw_revision, 8, "hardware revision"))
    tlvs += _tlv(T_MFG_TIME, struct.pack("<I", data.manufacturing_time & 0xFFFFFFFF))
    tlvs += _tlv(
        T_STATION_FIXTURE,
        _ascii(data.station_fixture, 64, "station/fixture"),
    )
    tlvs += _tlv(T_PROFILE, _ascii(data.profile, 64, "profile"))
    tlvs += _tlv(T_FIRMWARE_VERSION, struct.pack("<I", data.firmware_version & 0xFFFFFFFF))
    if data.mac is not None:
        if len(data.mac) != 6:
            raise RecordError("MAC address must be 6 bytes")
        tlvs += _tlv(T_MAC, data.mac)
    for field_id, (calibration_value, calibration_unit) in sorted(
        data.calibration.items()
    ):
        if not T_CALIBRATION_MIN <= field_id <= T_CALIBRATION_MAX:
            raise RecordError(f"calibration field {field_id:#x} outside 0x20-0x2F")
        tlvs += _tlv(field_id, _unit_value(calibration_value, calibration_unit))
    for field_id, customer_value in sorted(data.customer.items()):
        if not T_CUSTOMER_MIN <= field_id <= T_CUSTOMER_MAX:
            raise RecordError(f"customer field {field_id:#x} outside 0x40-0x4F")
        tlvs += _tlv(
            field_id, _ascii(customer_value, 64, f"customer field {field_id:#x}")
        )
    for index, (key, custom_value) in enumerate(sorted(data.custom.items())):
        _check_secret(key)
        key_bytes = _ascii(key, 64, "custom key")
        value_bytes = _ascii(custom_value, 64, "custom value")
        custom_index = T_CUSTOM_MIN + index
        if custom_index > T_CUSTOM_MAX:
            raise RecordError("too many custom fields")
        tlvs += _tlv(custom_index, key_bytes + b"\0" + value_bytes)
    tlvs += b"\x00\x00"

    tlv_bytes = bytes(tlvs)
    tlv_crc = zlib.crc32(tlv_bytes) & 0xFFFFFFFF
    header_prefix = struct.pack(
        "<IHHII12s",
        RECORD_MAGIC,
        RECORD_FORMAT,
        data.revision & 0xFFFF,
        len(tlv_bytes),
        tlv_crc,
        data.uid,
    )
    header_crc = zlib.crc32(header_prefix) & 0xFFFFFFFF
    return header_prefix + struct.pack("<I", header_crc) + tlv_bytes


def parse_record(blob: bytes, *, expect_uid: bytes | None = None) -> ParsedRecord:
    if len(blob) < HEADER.size:
        raise RecordError("record is shorter than the header")
    magic, fmt, revision, length, tlv_crc, uid, header_crc = HEADER.unpack_from(blob)
    if magic != RECORD_MAGIC:
        raise RecordError("bad record magic")
    if fmt != RECORD_FORMAT:
        raise RecordError(f"unsupported record format {fmt}")
    if length > len(blob) - HEADER.size:
        raise RecordError("record TLV length exceeds data")
    end = HEADER.size + length
    tlv_bytes = blob[HEADER.size : end]
    actual_tlv_crc = zlib.crc32(tlv_bytes) & 0xFFFFFFFF
    if actual_tlv_crc != tlv_crc:
        raise RecordError("record TLV CRC32 mismatch")
    actual_header_crc = zlib.crc32(blob[:28]) & 0xFFFFFFFF
    if actual_header_crc != header_crc:
        raise RecordError("record header CRC32 mismatch")
    if expect_uid is not None and uid != expect_uid:
        raise RecordError("record UID does not match the chip")
    fields: dict[int, bytes] = {}
    offset = 0
    while offset < len(tlv_bytes):
        if offset + 2 > len(tlv_bytes):
            raise RecordError("truncated TLV header")
        field_type = tlv_bytes[offset]
        size = tlv_bytes[offset + 1]
        if field_type == 0:
            break
        offset += 2
        if offset + size > len(tlv_bytes):
            raise RecordError(f"truncated TLV {field_type:#x}")
        fields[field_type] = tlv_bytes[offset : offset + size]
        offset += size
    return ParsedRecord(
        magic=magic,
        format=fmt,
        revision=revision,
        length=length,
        tlv_crc32=tlv_crc,
        uid=uid,
        header_crc32=header_crc,
        fields=fields,
        blob=blob[:end],
    )


def build_and_validate(data: ProvisioningData) -> bytes:
    blob = build_record(data)
    parsed = parse_record(blob, expect_uid=data.uid)
    if parsed.serial_number != data.serial_number:
        raise RecordError("record read-back does not match")
    return blob


def now_utc() -> int:
    return int(time.time())
