"""Firmware image parsing shared by Studio and Factory.

Supported formats:

* Intel HEX (``.hex``) — records are parsed into a sparse address map.
* raw binary (``.bin``) — loaded at an explicit base address.
* ELF32/ELF64 little-endian (``.elf``) — PT_LOAD segments are extracted.
* HWVAL application image (``.img``) — ``FactoryImageHeader`` followed by the
  payload.  The header carries the firmware version and name the factory needs
  to compose the bootloader metadata records (EOL-005, [BOOT] Appendix B).

Nothing here talks to hardware; OpenOCD receives the original file path for
``program`` and the parsed image is used for validation, CRC32 checks and the
metadata records.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

DEFAULT_FLASH_BASE = 0x08000000
IMAGE_MAGIC = b"SCFW"
IMAGE_HEADER = struct.Struct("<4sHHIIIII32s16s")
# magic, format, header_size, version, load_addr, entry, payload_len,
# payload_crc32, name[32], reserved[16]
IMAGE_HEADER_SIZE = IMAGE_HEADER.size

METADATA_MAGIC = b"BMET"
METADATA_HEADER = struct.Struct("<4sBBBBHIIII")
# magic, format, record_type, slot, reserved, sequence, firmware_version,
# image_size, image_crc32, header_crc32
METADATA_SIZE = METADATA_HEADER.size

METADATA_HEADER_RECORD = 1
METADATA_VERIFIED = 2
METADATA_ACTIVATED = 3
METADATA_CONFIRMED = 4

METADATA_NAMES = {
    METADATA_HEADER_RECORD: "HEADER",
    METADATA_VERIFIED: "VERIFIED",
    METADATA_ACTIVATED: "ACTIVATED",
    METADATA_CONFIRMED: "CONFIRMED",
}


class ImageError(RuntimeError):
    """Invalid or unsupported firmware image."""


@dataclass(slots=True)
class Segment:
    address: int
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(slots=True)
class FirmwareImage:
    path: Path
    kind: str
    data: bytes
    sha256: str
    segments: list[Segment] = field(default_factory=list)
    entry: int = 0
    version: int = 0
    name: str = ""
    load_address: int = 0
    header_format: int = 0

    @property
    def size(self) -> int:
        return sum(s.size for s in self.segments) if self.segments else len(self.data)

    @property
    def crc32(self) -> int:
        return zlib.crc32(self.data) & 0xFFFFFFFF

    @property
    def end_address(self) -> int:
        if not self.segments:
            return self.load_address + self.size
        return max(s.address + s.size for s in self.segments)

    def version_string(self) -> str:
        return f"{self.version >> 16}.{(self.version >> 8) & 0xFF}.{self.version & 0xFF}"

    def to_metadata_records(self, slot: int = 0, sequence: int = 1) -> list[bytes]:
        """Compose the four records BOOT expects for a factory-programmed slot."""
        image_crc = self.crc32
        records: list[bytes] = []
        for record_type in (
            METADATA_HEADER_RECORD,
            METADATA_VERIFIED,
            METADATA_ACTIVATED,
            METADATA_CONFIRMED,
        ):
            prefix = METADATA_HEADER.pack(
                METADATA_MAGIC,
                1,
                record_type,
                slot,
                0,
                sequence,
                self.version,
                len(self.data),
                image_crc,
                0,
            )
            crc = zlib.crc32(prefix[:-4]) & 0xFFFFFFFF
            records.append(prefix[:-4] + struct.pack("<I", crc))
        return records


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_image(
    payload: bytes,
    *,
    version: int,
    name: str,
    load_address: int = 0x08020000,
    entry: int | None = None,
) -> bytes:
    """Build a ``.img`` file with the factory header (used by the profile tool)."""
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = IMAGE_HEADER.pack(
        IMAGE_MAGIC,
        1,
        IMAGE_HEADER.size,
        version,
        load_address,
        entry if entry is not None else load_address,
        len(payload),
        crc,
        name.encode("ascii", "replace")[:32].ljust(32, b"\0"),
        b"\0" * 16,
    )
    return header + payload


def _parse_img(path: Path) -> FirmwareImage:
    raw = path.read_bytes()
    if len(raw) < IMAGE_HEADER_SIZE:
        raise ImageError(f"{path.name}: short application image header")
    (
        magic,
        fmt,
        header_size,
        version,
        load_address,
        entry,
        payload_len,
        payload_crc,
        name,
        _reserved,
    ) = IMAGE_HEADER.unpack_from(raw)
    if magic != IMAGE_MAGIC:
        raise ImageError(f"{path.name}: bad image magic")
    if fmt != 1 or header_size < IMAGE_HEADER_SIZE:
        raise ImageError(f"{path.name}: unsupported image format {fmt}")
    payload = raw[header_size : header_size + payload_len]
    if len(payload) != payload_len:
        raise ImageError(f"{path.name}: truncated image payload")
    if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
        raise ImageError(f"{path.name}: image payload CRC32 mismatch")
    return FirmwareImage(
        path=path,
        kind="img",
        data=payload,
        sha256=hashlib.sha256(raw).hexdigest(),
        segments=[Segment(load_address, payload)],
        entry=entry,
        version=version,
        name=name.rstrip(b"\0").decode("ascii", "replace"),
        load_address=load_address,
        header_format=fmt,
    )


def _parse_hex(path: Path) -> FirmwareImage:
    memory: dict[int, int] = {}
    upper = 0
    for lineno, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ImageError(f"{path.name}:{lineno}: not an Intel HEX record")
        try:
            raw = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise ImageError(f"{path.name}:{lineno}: invalid hex") from exc
        if len(raw) < 5:
            raise ImageError(f"{path.name}:{lineno}: short record")
        count, addr_hi, addr_lo, rectype = raw[0], raw[1], raw[2], raw[3]
        data = raw[4 : 4 + count]
        if len(data) != count or len(raw) != count + 5:
            raise ImageError(f"{path.name}:{lineno}: bad record length")
        if sum(raw) & 0xFF:
            raise ImageError(f"{path.name}:{lineno}: checksum mismatch")
        address = (addr_hi << 8) | addr_lo
        if rectype == 0x00:
            for offset, byte in enumerate(data):
                memory[upper + address + offset] = byte
        elif rectype == 0x01:
            break
        elif rectype == 0x02:
            upper = int.from_bytes(data, "big") << 4
        elif rectype == 0x04:
            upper = int.from_bytes(data, "big") << 16
        elif rectype in (0x03, 0x05):
            continue
        else:
            raise ImageError(f"{path.name}:{lineno}: unsupported record type {rectype}")
    if not memory:
        raise ImageError(f"{path.name}: empty HEX file")
    return _image_from_memory(path, "hex", memory)


def _parse_bin(path: Path, base_address: int = DEFAULT_FLASH_BASE) -> FirmwareImage:
    data = path.read_bytes()
    if not data:
        raise ImageError(f"{path.name}: empty binary")
    return FirmwareImage(
        path=path,
        kind="bin",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        segments=[Segment(base_address, data)],
        load_address=base_address,
    )


def _parse_elf(path: Path) -> FirmwareImage:
    raw = path.read_bytes()
    if len(raw) < 52 or raw[:4] != b"\x7fELF":
        raise ImageError(f"{path.name}: not an ELF file")
    elf_class = raw[4]
    endian = raw[5]
    if endian != 1:
        raise ImageError(f"{path.name}: only little-endian ELF is supported")
    segments: list[Segment] = []
    if elf_class == 1:
        if len(raw) < 52:
            raise ImageError(f"{path.name}: truncated ELF32 header")
        (entry,) = struct.unpack_from("<I", raw, 24)
        phoff, = struct.unpack_from("<I", raw, 28)
        phentsize, phnum = struct.unpack_from("<HH", raw, 42)
        for index in range(phnum):
            offset = phoff + index * phentsize
            p_type, p_offset, p_vaddr, p_paddr, p_filesz = struct.unpack_from(
                "<IIIII", raw, offset
            )
            if p_type != 1 or p_filesz == 0:
                continue
            segments.append(Segment(p_paddr or p_vaddr, raw[p_offset : p_offset + p_filesz]))
    elif elf_class == 2:
        if len(raw) < 64:
            raise ImageError(f"{path.name}: truncated ELF64 header")
        (entry,) = struct.unpack_from("<Q", raw, 24)
        phoff, = struct.unpack_from("<Q", raw, 32)
        phentsize, phnum = struct.unpack_from("<HH", raw, 54)
        for index in range(phnum):
            offset = phoff + index * phentsize
            p_type, _flags, p_offset, p_vaddr, p_paddr, p_filesz = struct.unpack_from(
                "<IIQQQQ", raw, offset
            )
            if p_type != 1 or p_filesz == 0:
                continue
            segments.append(Segment(p_paddr or p_vaddr, raw[p_offset : p_offset + p_filesz]))
    else:
        raise ImageError(f"{path.name}: unsupported ELF class {elf_class}")
    if not segments:
        raise ImageError(f"{path.name}: ELF has no loadable segments")
    data = b"".join(segment.data for segment in segments)
    return FirmwareImage(
        path=path,
        kind="elf",
        data=data,
        sha256=hashlib.sha256(raw).hexdigest(),
        segments=segments,
        entry=entry,
        load_address=segments[0].address,
    )


def _image_from_memory(path: Path, kind: str, memory: dict[int, int]) -> FirmwareImage:
    addresses = sorted(memory)
    start = addresses[0]
    end = addresses[-1]
    data = bytearray(end - start + 1)
    for address, byte in memory.items():
        data[address - start] = byte
    blob = bytes(data)
    return FirmwareImage(
        path=path,
        kind=kind,
        data=blob,
        sha256=hashlib.sha256(blob).hexdigest(),
        segments=[Segment(start, blob)],
        load_address=start,
    )


def parse_image(path: str | Path, base_address: int = DEFAULT_FLASH_BASE) -> FirmwareImage:
    p = Path(path)
    if not p.is_file():
        raise ImageError(f"image not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".img":
        return _parse_img(p)
    if suffix in (".hex", ".ihex"):
        return _parse_hex(p)
    if suffix == ".elf":
        return _parse_elf(p)
    if suffix in (".bin", ".binary"):
        return _parse_bin(p, base_address)
    # Sniff the content when the extension is unusual.
    head = p.read_bytes()[:16]
    if head[:4] == b"\x7fELF":
        return _parse_elf(p)
    if head[:4] == IMAGE_MAGIC:
        return _parse_img(p)
    raise ImageError(f"{p.name}: unsupported firmware image format")


def write_stream(path: Path) -> BinaryIO:
    return path.open("wb")
