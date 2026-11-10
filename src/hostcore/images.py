"""Firmware image parsing and BOOT metadata records.

Supported formats:

* Intel HEX (``.hex``) — records are parsed into a sparse address map.
* raw binary (``.bin``) — loaded at an explicit base address.
* ELF32/ELF64 little-endian (``.elf``) — PT_LOAD segments are extracted.
* IBWH application image (``.img``) — the 256-byte header built by
  ``bootloader/tools/mkimage.py`` followed by the raw application binary.

The metadata helpers implement Appendix B of the BOOT SRS: a 256-byte image
header at ``+0x000`` and 32-byte VERIFIED/ACTIVATED/CONFIRMED records at their
fixed offsets.  Nothing here talks to hardware.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from hostcore.layout import (
    APP_LOAD_ADDR,
    IMAGE_EXTENSION_OFFSET,
    IMAGE_EXTENSION_SIZE,
    IMAGE_HEADER_CRC_OFFSET,
    IMAGE_HEADER_SIZE,
    IMAGE_HEADER_VERSION,
    IMAGE_HEADER_VERSION_UNSIGNED,
    IMAGE_HEADER_VERSIONS,
    IMAGE_KEY_ID_OFFSET,
    IMAGE_MAGIC,
    IMAGE_SECURITY_COUNTER_OFFSET,
    IMAGE_SHA256_OFFSET,
    IMAGE_SHA256_SIZE,
    IMAGE_SIGNATURE_OFFSET,
    IMAGE_SIGNATURE_SIZE,
    IMAGE_SIGNED_SIZE,
    IMAGE_TYPE_APPLICATION,
    META_ACTIVATED_MAGIC,
    META_ACTIVATED_OFFSET,
    META_CONFIRMED_MAGIC,
    META_CONFIRMED_OFFSET,
    META_HEADER_SIZE,
    META_RECORD_SIZE,
    META_VERIFIED_MAGIC,
    META_VERIFIED_OFFSET,
    RAM_END,
    RAM_START,
    SLOT_SIZE,
)

DEFAULT_FLASH_BASE = 0x08000000
IMAGE_MAGIC_BYTES = b"IBWH"

# magic, version, header_size, type, version(int), image_size, image_crc32,
# load_addr, entry, build_timestamp
_IMAGE_FIELDS = struct.Struct("<IHHIIIIIII")
_COMMIT_OFFSET = 36
_COMMIT_SIZE = 20
_NAME_OFFSET = 56
_NAME_SIZE = 32
_HEADER_CRC_OFFSET = IMAGE_HEADER_CRC_OFFSET

# Backwards-compatible names used by older callers/tests.
METADATA_SIZE = META_RECORD_SIZE


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
    image_type: int = 0
    build_timestamp: int = 0
    git_commit: str = ""
    header: bytes = b""
    header_crc32: int = 0
    security_counter: int = 0
    key_id: int = 0
    image_sha256: bytes = b""
    signature: bytes = b""

    @property
    def is_signed(self) -> bool:
        return self.header_format == IMAGE_HEADER_VERSION

    @property
    def expected_floor(self) -> int:
        """The anti-rollback floor this image sets once CONFIRMED."""
        return self.security_counter if self.is_signed else 0

    def verify_body_sha256(self) -> bool:
        if not self.is_signed:
            return True
        return hashlib.sha256(self.data).digest() == self.image_sha256

    def verify_signature(self, public_key: Any) -> bool:
        """Check the v2 ECDSA P-256 signature with a public key or PEM."""
        if not self.is_signed or not self.header:
            return False
        return verify_header_signature(self.header, _coerce_public_key(public_key))

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
        return version_text(self.version)

    def to_metadata_records(self, slot: int = 0, sequence: int = 1) -> list[bytes]:
        """Return the Appendix B bytes to write into one metadata sector.

        Element 0 is the 256-byte image header at ``+0x000``.  The following
        elements are the 32-byte VERIFIED, ACTIVATED and CONFIRMED records at
        their fixed offsets (``0x100``, ``0x120`` and ``0x140``).
        """
        if not self.header:
            raise ImageError("metadata requires an IBWH image header")
        if len(self.header) != IMAGE_HEADER_SIZE:
            raise ImageError("IBWH header must be 256 bytes")
        records = [self.header]
        records.append(make_metadata_record(META_VERIFIED_MAGIC, self.crc32))
        records.append(make_metadata_record(META_ACTIVATED_MAGIC, sequence & 0xFFFFFFFF))
        records.append(make_metadata_record(META_CONFIRMED_MAGIC, 0))
        return records

    def header_dict(self) -> dict[str, int | str | bytes]:
        """Return the IBWH header fields for HWLINK UpdateBegin."""
        if not self.header:
            raise ImageError("image has no IBWH header")
        return {
            "magic": IMAGE_MAGIC,
            "header_version": int(self.header_format or IMAGE_HEADER_VERSION),
            "header_size": IMAGE_HEADER_SIZE,
            "image_type": int(self.image_type or IMAGE_TYPE_APPLICATION),
            "version": self.version,
            "image_size": len(self.data),
            "image_crc32": self.crc32,
            "load_address": self.load_address,
            "entry_address": self.entry,
            "build_timestamp": self.build_timestamp,
            "git_commit": bytes.fromhex(self.git_commit) if self.git_commit else b"",
            "name": self.name,
            "header_crc32": self.header_crc32,
            "extension": self.header[IMAGE_EXTENSION_OFFSET : IMAGE_EXTENSION_OFFSET + IMAGE_EXTENSION_SIZE],
        }

    def metadata_offsets(self) -> list[int]:
        return [
            0,
            META_VERIFIED_OFFSET,
            META_ACTIVATED_OFFSET,
            META_CONFIRMED_OFFSET,
        ]


def version_text(value: int) -> str:
    return f"{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{value & 0xFF}"


def make_metadata_record(magic: int, value: int) -> bytes:
    """Build one Appendix B record: magic, value, five zeros, CRC32."""
    body = struct.pack("<IIIIIII", magic, value & 0xFFFFFFFF, 0, 0, 0, 0, 0)
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def parse_metadata_record(blob: bytes) -> tuple[int, int, bool]:
    """Return ``(magic, value, present)``; ``present`` requires magic+CRC."""
    if len(blob) < META_RECORD_SIZE:
        return 0, 0, False
    magic, value = struct.unpack_from("<II", blob)
    (crc,) = struct.unpack_from("<I", blob, META_RECORD_SIZE - 4)
    present = zlib.crc32(blob[: META_RECORD_SIZE - 4]) & 0xFFFFFFFF == crc
    return magic, value, present


def _parse_ibwh_header(header: bytes, path: Path) -> dict[str, int | str | bytes]:
    if len(header) < IMAGE_HEADER_SIZE:
        raise ImageError(f"{path.name}: shorter than a 256-byte IBWH header")
    (
        magic,
        header_version,
        header_size,
        image_type,
        version,
        image_size,
        image_crc32,
        load_address,
        entry_address,
        build_timestamp,
    ) = _IMAGE_FIELDS.unpack_from(header)
    if magic != IMAGE_MAGIC:
        raise ImageError(f"{path.name}: bad image magic")
    if header_version not in IMAGE_HEADER_VERSIONS:
        raise ImageError(f"{path.name}: unsupported image header version {header_version}")
    if header_size != IMAGE_HEADER_SIZE:
        raise ImageError(f"{path.name}: bad image header size {header_size}")
    if image_type != IMAGE_TYPE_APPLICATION:
        raise ImageError(f"{path.name}: unsupported image type {image_type}")
    if load_address != APP_LOAD_ADDR:
        raise ImageError(
            f"{path.name}: load address 0x{load_address:08X} != 0x{APP_LOAD_ADDR:08X}"
        )
    if image_size > SLOT_SIZE:
        raise ImageError(f"{path.name}: image size {image_size} exceeds the slot")
    (header_crc,) = struct.unpack_from("<I", header, _HEADER_CRC_OFFSET)
    if zlib.crc32(header[:_HEADER_CRC_OFFSET]) & 0xFFFFFFFF != header_crc:
        raise ImageError(f"{path.name}: image header CRC32 mismatch")
    signed = header_version == IMAGE_HEADER_VERSION
    (security_counter,) = struct.unpack_from("<I", header, IMAGE_SECURITY_COUNTER_OFFSET)
    (key_id_value,) = struct.unpack_from("<I", header, IMAGE_KEY_ID_OFFSET)
    image_sha256 = header[IMAGE_SHA256_OFFSET : IMAGE_SHA256_OFFSET + IMAGE_SHA256_SIZE]
    signature = header[IMAGE_SIGNATURE_OFFSET : IMAGE_SIGNATURE_OFFSET + IMAGE_SIGNATURE_SIZE]
    if signed and not any(signature):
        raise ImageError(f"{path.name}: version 2 header has no signature")
    return {
        "header_version": header_version,
        "header_size": header_size,
        "image_type": image_type,
        "version": version,
        "image_size": image_size,
        "image_crc32": image_crc32,
        "load_address": load_address,
        "entry_address": entry_address,
        "build_timestamp": build_timestamp,
        "git_commit": header[_COMMIT_OFFSET : _COMMIT_OFFSET + _COMMIT_SIZE].hex(),
        "name": header[_NAME_OFFSET : _NAME_OFFSET + _NAME_SIZE]
        .rstrip(b"\0")
        .decode("ascii", "replace"),
        "header_crc32": header_crc,
        "security_counter": security_counter if signed else 0,
        "key_id": key_id_value if signed else 0,
        "image_sha256": image_sha256 if signed else b"",
        "signature": signature if signed else b"",
    }


def _crypto() -> tuple[Any, Any, Any, Any]:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ImageError("cryptography is required for signed images") from exc
    return hashes, serialization, ec, utils


def load_key(path: str | Path) -> Any:
    """Load a P-256 private or public key from a PEM file."""
    _hashes, serialization, ec, _utils = _crypto()
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ImageError(f"cannot read key {path}: {exc}") from exc
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (TypeError, ValueError):
        try:
            key = serialization.load_pem_public_key(data)
        except (TypeError, ValueError) as exc:
            raise ImageError(f"{path}: not a PEM private or public key") from exc
    if not isinstance(getattr(key, "curve", None), ec.SECP256R1):
        raise ImageError(f"{path}: not a P-256 (secp256r1) key")
    return key


def public_point(key: Any) -> bytes:
    """X || Y (64 bytes, big-endian), as BOOT stores its public key."""
    _hashes, serialization, _ec, _utils = _crypto()
    public = key.public_key() if hasattr(key, "public_key") else key
    raw = public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return bytes(raw[1:])


def key_id(point: bytes) -> int:
    """First four bytes of SHA-256(X||Y), little-endian."""
    return int.from_bytes(hashlib.sha256(point).digest()[:4], "little")


def public_key_pem(key: Any) -> str:
    _hashes, serialization, _ec, _utils = _crypto()
    public = key.public_key() if hasattr(key, "public_key") else key
    return public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def _coerce_public_key(key: Any) -> Any:
    if isinstance(key, (str, Path)):
        return load_key(key)
    return key


def sign_header(header: bytes, body: bytes, key: Any, security_counter: int = 0) -> bytes:
    """Return a version 2 (signed) header for ``body`` (BSAFE-006).

    Bytes 0..127 are signed with ECDSA P-256/SHA-256; the signature is stored
    as r || s, 32 bytes each, at offset 128.  The layout mirrors
    ``bootloader/tools/mkimage.py:sign_header``.
    """
    hashes, _serialization, ec, utils = _crypto()
    if not hasattr(key, "sign"):
        raise ImageError("signing needs a private P-256 key")
    if not 0 <= security_counter <= 0xFFFFFFFF:
        raise ImageError("security counter must fit in 32 bits")
    out = bytearray(header)
    struct.pack_into("<H", out, 4, IMAGE_HEADER_VERSION)
    struct.pack_into("<I", out, IMAGE_SECURITY_COUNTER_OFFSET, security_counter)
    struct.pack_into("<I", out, IMAGE_KEY_ID_OFFSET, key_id(public_point(key)))
    out[IMAGE_SHA256_OFFSET : IMAGE_SHA256_OFFSET + IMAGE_SHA256_SIZE] = hashlib.sha256(
        body
    ).digest()
    out[
        IMAGE_SIGNATURE_OFFSET + IMAGE_SIGNATURE_SIZE : IMAGE_HEADER_CRC_OFFSET
    ] = bytes(IMAGE_HEADER_CRC_OFFSET - IMAGE_SIGNATURE_OFFSET - IMAGE_SIGNATURE_SIZE)
    der = key.sign(bytes(out[:IMAGE_SIGNED_SIZE]), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    out[IMAGE_SIGNATURE_OFFSET : IMAGE_SIGNATURE_OFFSET + IMAGE_SIGNATURE_SIZE] = r.to_bytes(
        32, "big"
    ) + s.to_bytes(32, "big")
    struct.pack_into("<I", out, IMAGE_HEADER_CRC_OFFSET, zlib.crc32(out[:252]) & 0xFFFFFFFF)
    return bytes(out)


def verify_header_signature(header: bytes, key: Any) -> bool:
    """Check a version 2 header's signature the way BOOT does."""
    from cryptography.exceptions import InvalidSignature

    hashes, _serialization, ec, utils = _crypto()
    if len(header) < IMAGE_HEADER_SIZE:
        return False
    public = key.public_key() if hasattr(key, "public_key") else key
    raw = header[IMAGE_SIGNATURE_OFFSET : IMAGE_SIGNATURE_OFFSET + IMAGE_SIGNATURE_SIZE]
    if len(raw) != IMAGE_SIGNATURE_SIZE:
        return False
    der = utils.encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    try:
        public.verify(der, header[:IMAGE_SIGNED_SIZE], ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def validate_vectors(binary: bytes) -> list[str]:
    errors: list[str] = []
    if len(binary) < 8:
        errors.append("input binary is too small for a vector table")
        return errors
    sp, entry = struct.unpack_from("<II", binary, 0)
    if not (RAM_START < sp <= RAM_END) or (sp & 0x7):
        errors.append(f"initial SP 0x{sp:08x} is not in internal RAM")
    entry_addr = entry & ~1
    if not (entry & 1) or not (APP_LOAD_ADDR <= entry_addr < APP_LOAD_ADDR + SLOT_SIZE):
        errors.append(f"reset vector 0x{entry:08x} is not inside slot A")
    return errors


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
    load_address: int = APP_LOAD_ADDR,
    entry: int | None = None,
    build_timestamp: int = 0,
    git_commit: str = "",
    signing_key: Any = None,
    security_counter: int = 0,
) -> bytes:
    """Build an IBWH ``.img`` file from a raw application binary.

    Without ``signing_key`` the header is the unsigned version 1 format;
    with it the header is version 2 and signed (BSAFE-006).
    """
    errors = validate_vectors(payload)
    if errors:
        raise ImageError("; ".join(errors))
    if load_address != APP_LOAD_ADDR:
        raise ImageError(f"applications must load at 0x{APP_LOAD_ADDR:08X}")
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    reset_vector = struct.unpack_from("<I", payload, 4)[0] if entry is None else entry
    header = bytearray(IMAGE_HEADER_SIZE)
    _IMAGE_FIELDS.pack_into(
        header,
        0,
        IMAGE_MAGIC,
        IMAGE_HEADER_VERSION_UNSIGNED,
        IMAGE_HEADER_SIZE,
        IMAGE_TYPE_APPLICATION,
        version,
        len(payload),
        crc,
        load_address,
        reset_vector,
        build_timestamp & 0xFFFFFFFF,
    )
    raw_commit = bytes(20)
    text = git_commit.strip()
    if text:
        try:
            raw_commit = bytes.fromhex(text[:40]) if len(text) >= 40 else text[:20].encode()
        except ValueError:
            raw_commit = text[:20].encode("ascii", "replace")
        raw_commit = raw_commit[:20].ljust(20, b"\0")
    header[_COMMIT_OFFSET : _COMMIT_OFFSET + _COMMIT_SIZE] = raw_commit
    header[_NAME_OFFSET : _NAME_OFFSET + _NAME_SIZE] = (
        name.encode("ascii", "replace")[:32].ljust(32, b"\0")
    )
    struct.pack_into("<I", header, _HEADER_CRC_OFFSET, zlib.crc32(header[:252]) & 0xFFFFFFFF)
    if signing_key is not None:
        key = load_key(signing_key) if isinstance(signing_key, (str, Path)) else signing_key
        return sign_header(bytes(header), payload, key, security_counter) + payload
    return bytes(header) + payload


def _parse_img(path: Path) -> FirmwareImage:
    raw = path.read_bytes()
    header = raw[:IMAGE_HEADER_SIZE]
    fields = _parse_ibwh_header(header, path)
    payload_len = int(fields["image_size"])
    payload = raw[IMAGE_HEADER_SIZE : IMAGE_HEADER_SIZE + payload_len]
    if len(payload) != payload_len:
        raise ImageError(f"{path.name}: truncated image payload")
    if zlib.crc32(payload) & 0xFFFFFFFF != int(fields["image_crc32"]):
        raise ImageError(f"{path.name}: image payload CRC32 mismatch")
    if int(fields["header_version"]) == IMAGE_HEADER_VERSION and (
        hashlib.sha256(payload).digest() != bytes(fields["image_sha256"])
    ):
        raise ImageError(f"{path.name}: image SHA-256 mismatch")
    errors = validate_vectors(payload)
    if errors:
        raise ImageError(f"{path.name}: " + "; ".join(errors))
    if len(raw) > IMAGE_HEADER_SIZE + payload_len:
        raise ImageError(f"{path.name}: trailing bytes after the image payload")
    return FirmwareImage(
        path=path,
        kind="img",
        data=payload,
        sha256=hashlib.sha256(raw).hexdigest(),
        segments=[Segment(int(fields["load_address"]), payload)],
        entry=int(fields["entry_address"]),
        version=int(fields["version"]),
        name=str(fields["name"]),
        load_address=int(fields["load_address"]),
        header_format=int(fields["header_version"]),
        image_type=int(fields["image_type"]),
        build_timestamp=int(fields["build_timestamp"]),
        git_commit=str(fields["git_commit"]),
        header=header,
        header_crc32=int(fields["header_crc32"]),
        security_counter=int(fields["security_counter"]),
        key_id=int(fields["key_id"]),
        image_sha256=bytes(fields["image_sha256"]),
        signature=bytes(fields["signature"]),
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
        (phoff,) = struct.unpack_from("<I", raw, 28)
        phentsize, phnum = struct.unpack_from("<HH", raw, 42)
        for index in range(phnum):
            offset = phoff + index * phentsize
            p_type, p_offset, p_vaddr, p_paddr, p_filesz = struct.unpack_from("<IIIII", raw, offset)
            if p_type != 1 or p_filesz == 0:
                continue
            segments.append(Segment(p_paddr or p_vaddr, raw[p_offset : p_offset + p_filesz]))
    elif elf_class == 2:
        if len(raw) < 64:
            raise ImageError(f"{path.name}: truncated ELF64 header")
        (entry,) = struct.unpack_from("<Q", raw, 24)
        (phoff,) = struct.unpack_from("<Q", raw, 32)
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


def parse_image(
    path: str | Path,
    base_address: int = DEFAULT_FLASH_BASE,
    *,
    public_key: Any = None,
    require_signature: bool = False,
) -> FirmwareImage:
    """Parse an image; with a key, also verify the v2 signature (P0-04)."""
    image = _parse_image(path, base_address)
    if require_signature or public_key is not None:
        if not image.is_signed:
            raise ImageError(f"{image.path.name}: production images must be signed (v2)")
        if public_key is not None and not image.verify_signature(public_key):
            raise ImageError(f"{image.path.name}: image signature does not verify")
    return image


def _parse_image(path: str | Path, base_address: int) -> FirmwareImage:
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
    head = p.read_bytes()[:16]
    if head[:4] == b"\x7fELF":
        return _parse_elf(p)
    if head[:4] == IMAGE_MAGIC_BYTES:
        return _parse_img(p)
    raise ImageError(f"{p.name}: unsupported firmware image format")


def write_stream(path: Path) -> BinaryIO:
    return path.open("wb")


__all__ = [
    "DEFAULT_FLASH_BASE",
    "METADATA_SIZE",
    "META_HEADER_SIZE",
    "META_RECORD_SIZE",
    "FirmwareImage",
    "ImageError",
    "Segment",
    "build_image",
    "key_id",
    "load_key",
    "make_metadata_record",
    "parse_image",
    "parse_metadata_record",
    "public_key_pem",
    "public_point",
    "sha256_file",
    "sign_header",
    "validate_vectors",
    "verify_header_signature",
    "version_text",
    "write_stream",
]
