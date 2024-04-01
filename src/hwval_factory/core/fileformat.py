"""Build, parse and describe the .sys, .cal and .diag files (spec section 3).

The layout is the one the factory station and the factory firmware use: a
32-byte little-endian header (magic, format, revision, length, payload CRC,
chip UID, header CRC) followed by a TLV payload, 4096 bytes at most.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from . import spec


class FileFormatError(ValueError):
    """The data cannot be written as, or is not, a valid factory data file."""


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def tlv(type_id: int, value: bytes) -> bytes:
    if not 0 <= type_id <= 0xFF:
        raise FileFormatError(f"TLV type {type_id} out of range")
    if len(value) > 0xFFFF:
        raise FileFormatError("TLV value too long")
    return struct.pack("<BH", type_id, len(value)) + value


def ascii_bytes(text: str, what: str) -> bytes:
    try:
        return text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FileFormatError(f"{what} {text!r} must be ASCII") from exc


def parse_uid(text: str) -> bytes:
    """12-byte chip UID from 24 hex digits (':', '-' and spaces allowed)."""
    cleaned = "".join(ch for ch in text if ch not in ":- \t")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if not cleaned:
        return bytes(12)
    if len(cleaned) != 24:
        raise FileFormatError(f"UID {text!r} needs 24 hex digits (12 bytes)")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise FileFormatError(f"UID {text!r} is not hexadecimal") from exc


def parse_mac(text: str) -> bytes:
    cleaned = text.replace(":", "").replace("-", "").strip()
    if len(cleaned) != 12:
        raise FileFormatError(f"MAC address {text!r} needs 12 hex digits")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise FileFormatError(f"MAC address {text!r} is not hexadecimal") from exc


def build_file(kind: str, payload: bytes, *, revision: int = 1, uid: bytes | None = None) -> bytes:
    """Wrap ``payload`` in the 32-byte header for ``kind`` (sys, cal or diag)."""
    if kind not in spec.KIND_MAGIC:
        raise FileFormatError(f"unknown file kind {kind!r}")
    uid = uid or bytes(12)
    if len(uid) != 12:
        raise FileFormatError("uid must be 12 bytes")
    if not 0 <= revision <= 0xFFFF:
        raise FileFormatError(f"revision {revision} out of range 0..65535")
    total = spec.FILE_HEADER_SIZE + len(payload)
    if total > spec.MAX_FILE_SIZE:
        raise FileFormatError(f"file is {total} bytes; the limit is {spec.MAX_FILE_SIZE}")
    head = struct.pack(
        "<IHHII12s",
        spec.KIND_MAGIC[kind],
        spec.FILE_FORMAT,
        revision,
        len(payload),
        crc32(payload),
        uid,
    )
    return head + struct.pack("<I", crc32(head)) + payload


@dataclass(slots=True)
class FactoryFile:
    kind: str
    revision: int
    uid: bytes
    payload: bytes
    items: list[tuple[int, bytes]] = field(default_factory=list)

    @property
    def bound(self) -> bool:
        return any(self.uid)

    def get(self, type_id: int) -> bytes | None:
        """The first TLV value of ``type_id``, or None."""
        for item_type, value in self.items:
            if item_type == type_id:
                return value
        return None


def parse_tlv(payload: bytes) -> list[tuple[int, bytes]]:
    items: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(payload):
        if offset + 3 > len(payload):
            raise FileFormatError("truncated TLV header")
        type_id, length = struct.unpack_from("<BH", payload, offset)
        offset += 3
        if offset + length > len(payload):
            raise FileFormatError(f"TLV type {type_id:#04x} runs past the payload")
        items.append((type_id, payload[offset : offset + length]))
        offset += length
    return items


def parse_file(data: bytes, expected_kind: str | None = None) -> FactoryFile:
    """Validate the header and both CRCs and return the parsed file."""
    if len(data) < spec.FILE_HEADER_SIZE:
        raise FileFormatError("file is shorter than the 32-byte header")
    if len(data) > spec.MAX_FILE_SIZE:
        raise FileFormatError(f"file is larger than {spec.MAX_FILE_SIZE} bytes")
    magic, fmt, revision, length, payload_crc, uid, header_crc = spec.FILE_HEADER.unpack_from(data)
    kind = spec.MAGIC_KIND.get(magic)
    if kind is None:
        raise FileFormatError(f"unknown magic {magic:#010x}")
    if expected_kind and kind != expected_kind:
        raise FileFormatError(f"expected a .{expected_kind} file, found .{kind} content")
    if fmt != spec.FILE_FORMAT:
        raise FileFormatError(f"unsupported file format {fmt}")
    if crc32(data[:28]) != header_crc:
        raise FileFormatError("header CRC mismatch")
    if length != len(data) - spec.FILE_HEADER_SIZE:
        raise FileFormatError(
            f"header says {length} payload bytes, file has {len(data) - spec.FILE_HEADER_SIZE}"
        )
    payload = data[spec.FILE_HEADER_SIZE :]
    if crc32(payload) != payload_crc:
        raise FileFormatError("payload CRC mismatch")
    return FactoryFile(kind, revision, uid, payload, parse_tlv(payload))


# -- .sys -----------------------------------------------------------------

_SYS_FIELDS = (
    (spec.SYS_SERIAL, "serial"),
    (spec.SYS_PRODUCT, "product"),
    (spec.SYS_HW_VERSION, "hw_version"),
    (spec.SYS_FW_VERSION, "fw_version"),
    (spec.SYS_BATCH, "batch"),
    (spec.SYS_MFG_DATE, "mfg_date"),
    (spec.SYS_NOTES, "notes"),
)


def build_sys(record: dict[str, str], *, revision: int = 1, uid: bytes | None = None) -> bytes:
    """Compact one record into a .sys file."""
    payload = bytearray()
    for type_id, name in _SYS_FIELDS:
        value = (record.get(name) or "").strip()
        if not value:
            if name == "serial":
                raise FileFormatError("the record has no serial number")
            continue
        payload += tlv(type_id, ascii_bytes(value, name))
    mac = (record.get("mac") or "").strip()
    if mac:
        payload += tlv(spec.SYS_MAC, parse_mac(mac))
    return build_file("sys", bytes(payload), revision=revision, uid=uid)


def describe_sys(parsed: FactoryFile) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for type_id, value in parsed.items:
        name = spec.SYS_NAMES.get(type_id, f"type_{type_id:02x}")
        text = (
            ":".join(f"{b:02X}" for b in value)
            if type_id == spec.SYS_MAC
            else value.decode("ascii", "replace")
        )
        out.append((name, text))
    return out


# -- .cal -----------------------------------------------------------------


def build_cal(
    channels: list[tuple[int, float, float]],
    *,
    temp_ref: float | None = None,
    date: str = "",
    operator: str = "",
    revision: int = 1,
    uid: bytes | None = None,
) -> bytes:
    payload = bytearray()
    for channel, gain, offset in channels:
        payload += tlv(spec.CAL_CHANNEL, struct.pack("<Bff", channel, gain, offset))
    if temp_ref is not None:
        payload += tlv(spec.CAL_TEMP_REF, struct.pack("<f", temp_ref))
    if date:
        payload += tlv(spec.CAL_DATE, ascii_bytes(date, "date"))
    if operator:
        payload += tlv(spec.CAL_OPERATOR, ascii_bytes(operator, "operator"))
    return build_file("cal", bytes(payload), revision=revision, uid=uid)


def describe_cal(parsed: FactoryFile) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for type_id, value in parsed.items:
        if type_id == spec.CAL_CHANNEL and len(value) == 9:
            channel, gain, offset = struct.unpack("<Bff", value)
            out.append((f"channel {channel}", f"gain {gain:g}, offset {offset:g}"))
        elif type_id == spec.CAL_TEMP_REF and len(value) == 4:
            out.append(("temperature reference", f"{struct.unpack('<f', value)[0]:g} °C"))
        elif type_id == spec.CAL_DATE:
            out.append(("date", value.decode("ascii", "replace")))
        elif type_id == spec.CAL_OPERATOR:
            out.append(("operator", value.decode("ascii", "replace")))
    return out


# -- .diag ----------------------------------------------------------------


def build_diag(
    faults: list[tuple[int, int, int, str]] | None = None,
    counters: list[tuple[int, int]] | None = None,
    *,
    log_depth: int = 64,
    log_flags: int = 0,
    revision: int = 1,
    uid: bytes | None = None,
) -> bytes:
    payload = bytearray()
    for code, severity, flags, text in faults or []:
        payload += tlv(
            spec.DIAG_FAULT_MAP,
            struct.pack("<HBB", code, severity, flags) + ascii_bytes(text, "fault text"),
        )
    for counter_id, initial in counters or []:
        payload += tlv(spec.DIAG_COUNTER, struct.pack("<HI", counter_id, initial))
    payload += tlv(spec.DIAG_LOG_CFG, struct.pack("<HH", log_depth, log_flags))
    return build_file("diag", bytes(payload), revision=revision, uid=uid)


def describe_diag(parsed: FactoryFile) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for type_id, value in parsed.items:
        if type_id == spec.DIAG_FAULT_MAP and len(value) >= 4:
            code, severity, flags = struct.unpack_from("<HBB", value)
            text = value[4:].decode("ascii", "replace")
            out.append(
                (f"fault {code:#06x}", f"severity {severity}, flags {flags:#04x}, {text}")
            )
        elif type_id == spec.DIAG_COUNTER and len(value) == 6:
            counter_id, initial = struct.unpack("<HI", value)
            out.append((f"counter {counter_id}", f"initial {initial}"))
        elif type_id == spec.DIAG_LOG_CFG and len(value) == 4:
            depth, flags = struct.unpack("<HH", value)
            out.append(("log", f"depth {depth}, flags {flags:#06x}"))
    return out


def describe(parsed: FactoryFile) -> list[tuple[str, str]]:
    return {"sys": describe_sys, "cal": describe_cal, "diag": describe_diag}[parsed.kind](parsed)


# -- layout and hex view ----------------------------------------------------


def layout(data: bytes) -> list[tuple[int, int, str, str]]:
    """Every field of a valid file as (offset, size, name, value).

    The 32-byte header comes first, then one entry per TLV item.  The file
    must be valid; call :func:`parse_file` first.
    """
    parsed = parse_file(data)
    magic, fmt, revision, length, payload_crc, uid, header_crc = spec.FILE_HEADER.unpack_from(data)
    rows = [
        (0, 4, "magic", f"{magic:#010x}  {data[:4].decode('ascii', 'replace')}"),
        (4, 2, "format", str(fmt)),
        (6, 2, "revision", str(revision)),
        (8, 4, "payload length", f"{length} bytes"),
        (12, 4, "payload CRC-32", f"{payload_crc:#010x}"),
        (16, 12, "chip UID", uid.hex().upper() if any(uid) else "not bound"),
        (28, 4, "header CRC-32", f"{header_crc:#010x}"),
    ]
    names = spec.KIND_TLV_NAMES[parsed.kind]
    offset = spec.FILE_HEADER_SIZE
    for type_id, value in parsed.items:
        name = names.get(type_id, f"type {type_id:#04x}")
        rows.append(
            (offset, 3 + len(value), f"TLV {type_id:#04x} {name}",
             f"{len(value)} byte{'' if len(value) == 1 else 's'}")
        )
        offset += 3 + len(value)
    return rows


def hexdump(data: bytes, width: int = 16) -> str:
    """Classic hex dump: offset, hex bytes and printable ASCII."""
    lines = []
    for start in range(0, len(data), width):
        chunk = data[start : start + width]
        hexed = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{start:04x}  {hexed:<{width * 3 - 1}}  {text}")
    return "\n".join(lines)
