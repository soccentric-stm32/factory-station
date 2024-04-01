#!/usr/bin/env python3
#
# mkimage.py - wrap an application binary in a BOOT image file
#
# Author:  Sandesh Ghimire <sandesh@soccentric.com>
# Date:    2026-11-03
# Target:  NUCLEO-F767ZI
# SRS:     BHOST-001, BHOST-002, BSAFE-006; SRS-BOOT-F767 section 5.1
#
# Usage:
#   tools/mkimage.py --input build/app.bin --output build/app.img \
#       --version 0.1.0 --commit <40-hex> --timestamp <unix> \
#       --name stm32-application --key keys/dev-signing-key.pem
#
#   tools/mkimage.py --sign build/app.img --key signing-key.pem \
#       --security-counter 3 --output build/app-signed.img
#   tools/mkimage.py --inspect build/app.img [--key signing-key.pem]
#   tools/mkimage.py --genkey signing-key.pem
#   tools/mkimage.py --export-pubkey signing-key.pem --output pubkey.c
#
#   tools/mkimage.py --image build/app.img --metadata A \
#       --output build/meta_a.bin
#   tools/mkimage.py --factory-hex build/factory.hex --boot build/boot.hex \
#       --slot-a build/app_a.img --slot-b build/app_b.img
#
# The header is 256 bytes; the binary follows unchanged.  BOOT stores this
# header in the slot metadata sector (IMG-006).
#
# BSAFE-006: with --key the header is version 2.  Bytes 88..251 then hold
# the anti-rollback security counter, the key id, the SHA-256 of the binary
# and an ECDSA P-256 signature (r || s, big-endian) over header bytes
# 0..127.  Without --key the header is the unsigned version 1, which only a
# BOOT built with BOOT_SIGNED_IMAGES=OFF accepts.  Signing needs the Python
# "cryptography" package; everything else uses the standard library.

import argparse
import hashlib
import pathlib
import struct
import sys
import zlib

MAGIC = 0x48574249          # "IBWH" little-endian
HEADER_VERSION = 2          # signed (BSAFE-006)
HEADER_VERSION_UNSIGNED = 1
HEADER_SIZE = 256
# Header version 2 fields (offsets inside the 256-byte header).
SECURITY_COUNTER_OFFSET = 88
KEY_ID_OFFSET = 92
IMAGE_SHA256_OFFSET = 96
SIGNATURE_OFFSET = 128
SIGNATURE_SIZE = 64
SIGNED_SIZE = 128           # the signature covers bytes 0..127
EXT_OFFSET = 88             # hwlink.ImageHeader.extension = bytes 88..251
EXT_SIZE = 164
IMAGE_TYPE_APPLICATION = 1
LOAD_ADDRESS = 0x0800C000
SLOT_SIZE = 0x000F4000      # 976 KB
RAM_START = 0x20000000
RAM_END = 0x20080000

# P1-4: physical layout used by the factory images.
BANK1_BASE = 0x08000000
BANK2_BASE = 0x08100000
BOOT_REGION_SIZE = 0x00008000
META_OFFSET = 0x00008000
META_SIZE = 0x00004000
SLOT_OFFSET = 0x0000C000
SLOT_A_BASE = BANK1_BASE + SLOT_OFFSET
SLOT_B_BASE = BANK2_BASE + SLOT_OFFSET
META_A_BASE = BANK1_BASE + META_OFFSET
META_B_BASE = BANK2_BASE + META_OFFSET
BOOT_BASE = BANK1_BASE

META_VERIFIED_OFFSET = 0x100
META_ACTIVATED_OFFSET = 0x120
META_CONFIRMED_OFFSET = 0x140
META_REJECTED_OFFSET = 0x160
META_RECORD_SIZE = 32
META_VERIFIED_MAGIC = 0x56455249
META_ACTIVATED_MAGIC = 0x41435456
META_CONFIRMED_MAGIC = 0x434F4E46
META_REJECTED_MAGIC = 0x52454A45

IMAGE_HEADER = struct.Struct("<IHHIIIIIII20s32s164sI")
assert EXT_OFFSET + EXT_SIZE == HEADER_SIZE - 4

HEADER_FIELDS = (
    ("magic", "<I"),
    ("header_version", "<H"),
    ("header_size", "<H"),
    ("image_type", "<I"),
    ("version", "<I"),
    ("image_size", "<I"),
    ("image_crc32", "<I"),
    ("load_address", "<I"),
    ("entry_address", "<I"),
    ("build_timestamp", "<I"),
)


def semver(value: str) -> int:
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError(f"version must be major.minor.patch, got {value!r}")
    major, minor, patch = (int(p) & 0xFF for p in parts)
    return (major << 24) | (minor << 16) | patch


def version_text(value: int) -> str:
    return f"{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{value & 0xFF}"


def commit_bytes(value: str) -> bytes:
    raw = bytes(20)
    text = value.strip()
    if len(text) >= 40:
        try:
            raw = bytes.fromhex(text[:40])
        except ValueError:
            raw = text[:20].encode("ascii", "replace").ljust(20, b"\0")
    elif text:
        raw = text[:20].encode("ascii", "replace").ljust(20, b"\0")
    return raw


def commit_text(raw: bytes) -> str:
    if all(b == 0 for b in raw):
        return ""
    return raw.hex()


# ------------------------------------------------------------- signing


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils
    except ImportError as error:
        raise ValueError("signing needs the Python 'cryptography' package "
                         "(pip install -r tools/requirements.txt)") from error
    return hashes, serialization, ec, utils


def load_key(path: pathlib.Path):
    """Load a P-256 private or public key from a PEM file."""
    _hashes, serialization, ec, _utils = _crypto()
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (TypeError, ValueError):
        key = serialization.load_pem_public_key(data)
    curve = key.curve if hasattr(key, "curve") else None
    if not isinstance(curve, ec.SECP256R1):
        raise ValueError(f"{path}: not a P-256 (secp256r1) key")
    return key


def public_point(key) -> bytes:
    """X || Y, 64 bytes big-endian, as BOOT stores the key."""
    _hashes, serialization, _ec, _utils = _crypto()
    public = key.public_key() if hasattr(key, "public_key") else key
    raw = public.public_bytes(serialization.Encoding.X962,
                              serialization.PublicFormat.UncompressedPoint)
    return raw[1:]


def key_id(point: bytes) -> int:
    return int.from_bytes(hashlib.sha256(point).digest()[:4], "little")


def sign_header(header: bytes, body: bytes, key,
                security_counter: int | None = None) -> bytes:
    """Return a version 2 header for body, signed with a private key."""
    hashes, _serialization, ec, utils = _crypto()
    if not hasattr(key, "sign"):
        raise ValueError("signing needs a private key")
    out = bytearray(header)
    if security_counter is None:
        security_counter, = struct.unpack_from(
            "<I", header, SECURITY_COUNTER_OFFSET)
    if not 0 <= security_counter <= 0xFFFFFFFF:
        raise ValueError("security counter must fit in 32 bits")
    struct.pack_into("<H", out, 4, HEADER_VERSION)
    struct.pack_into("<I", out, SECURITY_COUNTER_OFFSET, security_counter)
    struct.pack_into("<I", out, KEY_ID_OFFSET, key_id(public_point(key)))
    out[IMAGE_SHA256_OFFSET:IMAGE_SHA256_OFFSET + 32] = \
        hashlib.sha256(body).digest()
    out[SIGNATURE_OFFSET + SIGNATURE_SIZE:252] = bytes(252 - SIGNATURE_OFFSET
                                                      - SIGNATURE_SIZE)
    der = key.sign(bytes(out[:SIGNED_SIZE]), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    out[SIGNATURE_OFFSET:SIGNATURE_OFFSET + SIGNATURE_SIZE] = \
        r.to_bytes(32, "big") + s.to_bytes(32, "big")
    struct.pack_into("<I", out, 252, zlib.crc32(out[:252]) & 0xFFFFFFFF)
    return bytes(out)


def signature_valid(header: bytes, key) -> bool:
    """Check a version 2 header's signature the way BOOT does."""
    hashes, _serialization, ec, utils = _crypto()
    from cryptography.exceptions import InvalidSignature
    public = key.public_key() if hasattr(key, "public_key") else key
    raw = header[SIGNATURE_OFFSET:SIGNATURE_OFFSET + SIGNATURE_SIZE]
    der = utils.encode_dss_signature(int.from_bytes(raw[:32], "big"),
                                     int.from_bytes(raw[32:], "big"))
    try:
        public.verify(der, header[:SIGNED_SIZE], ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def generate_key(path: pathlib.Path) -> None:
    _hashes, serialization, ec, _utils = _crypto()
    if path.exists():
        raise ValueError(f"{path} already exists; refusing to overwrite")
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pem)
    path.chmod(0o600)


def pubkey_c_source(key, source: str) -> str:
    """C definition of boot_signing_pubkey / boot_signing_key_id."""
    point = public_point(key)
    rows = []
    for offset in range(0, len(point), 8):
        rows.append("    " + ", ".join(f"0x{b:02x}"
                                      for b in point[offset:offset + 8]))
    return (
        "/**\n"
        " * @file    dev_pubkey.c\n"
        " * @brief   Development signing public key (ECDSA P-256) baked into the bootloader.\n"
        " *\n"
        " * @details\n"
        " * @warning Generated by `tools/mkimage.py --export-pubkey`; **do not edit by\n"
        " *          hand.**  To change the trusted key pair, regenerate it from the\n"
        " *          authoritative PEM file (`dev-signing-key.pem`) and commit the new\n"
        " *          output of the export step.\n"
        " *\n"
        " * The array @ref boot_signing_pubkey is the raw 64-byte uncompressed\n"
        " * (well, actually 32-byte X \\| 32-byte Y, no 0x04 prefix) P-256 public\n"
        " * key.  The authenticator (micro-ecc `uECC_verify`) consumes exactly this\n"
        " * layout.  The companion @ref boot_signing_key_id is a 32-bit fingerprint of\n"
        " * the same key (stable across regenerations of *the same key*, different\n"
        " * for a new keypair) so the bootloader can refuse images signed by an older,\n"
        " * now-revoked development key even if the public-key array is patched in\n"
        " * flash.\n"
        " *\n"
        " * Shipping keys: production releases swap this file (via the build\n"
        " * system) for the production key; the development key is only present in\n"
        " * engineering builds.\n"
        " *\n"
        f" * @author  Generated by tools/mkimage.py (source: {source})\n"
        " * @srs     BSAFE-006 (image authentication)\n"
        " */\n\n"
        "#include <stdint.h>\n"
        "#include \"signing_key.h\"\n\n"
        "/**\n"
        " * @brief Trusted P-256 signing public key, raw X\\|Y (64 bytes).\n"
        " *\n"
        " * @details Byte layout (big-endian, per the SEC 1 curve encoding without\n"
        " * the 0x04 \"uncompressed point\" tag):\n"
        " * - bytes   0..31 -> X coordinate (256 bit)\n"
        " * - bytes  32..63 -> Y coordinate (256 bit)\n"
        " *\n"
        " * The array is `const` and placed in the bootloader's own flash segment,\n"
        " * so an updated app image (running in the app slot) cannot modify it\n"
        " * without re-flashing the bootloader region - it is effectively a\n"
        " * hardware-root-of-trust anchor for all image signatures.\n"
        " *\n"
        " * @note    64 elements; index `i` is the `i`-th byte of the big-endian\n"
        " *          X coordinate for `0 <= i < 32`, and of the Y coordinate for\n"
        " *          `32 <= i < 64`.\n"
        " *\n"
        " * @srs     BSAFE-006\n"
        " */\n"
        "const uint8_t boot_signing_pubkey[64] = {\n"
        + ",\n".join(rows) + "\n};\n\n"
        "/**\n"
        " * @brief 32-bit stable fingerprint of @ref boot_signing_pubkey.\n"
        " *\n"
        " * @details Computed at key-generation time by `tools/mkimage.py`.  A\n"
        " *          fresh keypair yields a new fingerprint; the *same* key\n"
        " *          re-exported yields the *same* fingerprint.  The image\n"
        " *          metadata carries the fingerprint the signer used; on\n"
        " *          verification the bootloader compares both the full 64-byte\n"
        " *          key material (via the signature check) and this id (cheap\n"
        " *          early rejection of stale keys).\n"
        " *\n"
        " * @srs     BSAFE-006\n"
        " */\n"
        f"const uint32_t boot_signing_key_id = 0x{key_id(point):08x}UL;\n")


# ---------------------------------------------------------------- images


def validate_binary(binary: bytes) -> list[str]:
    errors = []
    if len(binary) < 8:
        errors.append("input binary is too small for a vector table")
        return errors
    if len(binary) > SLOT_SIZE:
        errors.append(f"binary is {len(binary)} bytes; the slot is {SLOT_SIZE}")

    sp, entry = struct.unpack_from("<II", binary, 0)
    if not (RAM_START < sp <= RAM_END) or (sp & 0x7):
        errors.append(f"initial SP 0x{sp:08x} is not in internal RAM")
    entry_addr = entry & ~1
    if not (entry & 1) or not (LOAD_ADDRESS <= entry_addr <
                              LOAD_ADDRESS + SLOT_SIZE):
        errors.append(f"reset vector 0x{entry:08x} is not inside the slot")
    return errors


def build_header(binary: bytes, args) -> bytes:
    version = semver(args.version)
    image_crc = zlib.crc32(binary) & 0xFFFFFFFF
    timestamp = int(args.timestamp, 0) if args.timestamp else 0
    entry = struct.unpack_from("<I", binary, 4)[0]

    header = bytearray(HEADER_SIZE)
    struct.pack_into("<I", header, 0, MAGIC)
    struct.pack_into("<H", header, 4, HEADER_VERSION_UNSIGNED)
    struct.pack_into("<H", header, 6, HEADER_SIZE)
    struct.pack_into("<I", header, 8, IMAGE_TYPE_APPLICATION)
    struct.pack_into("<I", header, 12, version)
    struct.pack_into("<I", header, 16, len(binary))
    struct.pack_into("<I", header, 20, image_crc)
    struct.pack_into("<I", header, 24, LOAD_ADDRESS)
    struct.pack_into("<I", header, 28, entry)
    struct.pack_into("<I", header, 32, timestamp)
    header[36:56] = commit_bytes(args.commit)
    header[56:88] = args.name.encode("ascii", "replace")[:32].ljust(32, b"\0")
    struct.pack_into("<I", header, 252, zlib.crc32(header[:252]) & 0xFFFFFFFF)
    if getattr(args, "key", None) is not None:
        return sign_header(bytes(header), binary, load_key(args.key),
                           args.security_counter)
    return bytes(header)


def read_image(path: pathlib.Path) -> tuple[bytes, bytes, int]:
    """Return (header, body, image_crc32) after validating an .img file."""
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError(f"{path}: shorter than a {HEADER_SIZE}-byte header")
    fields = IMAGE_HEADER.unpack_from(data, 0)
    (magic, header_version, header_size, image_type, _version, image_size,
     image_crc32, load_address, _entry, _timestamp, _git, _name,
     _reserved, header_crc32) = fields
    if magic != MAGIC:
        raise ValueError(f"{path}: bad image magic")
    if header_version not in (HEADER_VERSION, HEADER_VERSION_UNSIGNED) or \
            header_size != HEADER_SIZE or \
            image_type != IMAGE_TYPE_APPLICATION:
        raise ValueError(f"{path}: bad image header version, size or type")
    if load_address != LOAD_ADDRESS:
        raise ValueError(f"{path}: bad load address")
    if image_size > SLOT_SIZE:
        raise ValueError(f"{path}: image is larger than the slot")
    if zlib.crc32(data[:252]) & 0xFFFFFFFF != header_crc32:
        raise ValueError(f"{path}: header CRC32 mismatch")
    body = data[HEADER_SIZE:]
    if len(body) != image_size:
        raise ValueError(f"{path}: image_size does not match the body")
    if zlib.crc32(body) & 0xFFFFFFFF != image_crc32:
        raise ValueError(f"{path}: image CRC32 mismatch")
    if header_version == HEADER_VERSION and \
            hashlib.sha256(body).digest() != \
            data[IMAGE_SHA256_OFFSET:IMAGE_SHA256_OFFSET + 32]:
        raise ValueError(f"{path}: image SHA-256 mismatch")
    return data[:HEADER_SIZE], body, image_crc32


def metadata_record(magic: int, value: int) -> bytes:
    record = bytearray(META_RECORD_SIZE)
    struct.pack_into("<II", record, 0, magic, value)
    struct.pack_into("<I", record, META_RECORD_SIZE - 4,
                     zlib.crc32(record[:META_RECORD_SIZE - 4]) & 0xFFFFFFFF)
    return bytes(record)


def build_metadata(image_header: bytes, image_crc32: int, sequence: int,
                   confirmed: bool = True) -> bytes:
    """Build one 16 KB Appendix B metadata sector (P1-4)."""
    if len(image_header) != HEADER_SIZE:
        raise ValueError("metadata needs a 256-byte image header")
    sector = bytearray(b"\xff" * META_SIZE)
    sector[0:HEADER_SIZE] = image_header
    sector[META_VERIFIED_OFFSET:META_VERIFIED_OFFSET + META_RECORD_SIZE] = \
        metadata_record(META_VERIFIED_MAGIC, image_crc32)
    sector[META_ACTIVATED_OFFSET:META_ACTIVATED_OFFSET + META_RECORD_SIZE] = \
        metadata_record(META_ACTIVATED_MAGIC, sequence)
    if confirmed:
        sector[META_CONFIRMED_OFFSET:
               META_CONFIRMED_OFFSET + META_RECORD_SIZE] = \
            metadata_record(META_CONFIRMED_MAGIC, 0)
    return bytes(sector)


def read_ihex(path: pathlib.Path) -> list[tuple[int, bytes]]:
    """Minimal Intel HEX reader for the BOOT image."""
    segments: list[tuple[int, bytes]] = []
    upper = 0
    chunk = bytearray()
    chunk_addr = 0
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"{path}:{number}: not an Intel HEX record")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5 or (sum(raw) & 0xFF) != 0:
            raise ValueError(f"{path}:{number}: bad Intel HEX checksum")
        length, address, kind = raw[0], (raw[1] << 8) | raw[2], raw[3]
        payload = raw[4:4 + length]
        if kind == 0x00:
            absolute = upper + address
            if chunk and absolute == chunk_addr + len(chunk):
                chunk.extend(payload)
            else:
                if chunk:
                    segments.append((chunk_addr, bytes(chunk)))
                chunk, chunk_addr = bytearray(payload), absolute
        elif kind == 0x01:
            break
        elif kind == 0x04:
            upper = ((payload[0] << 8) | payload[1]) << 16
        elif kind == 0x02:
            upper = ((payload[0] << 8) | payload[1]) << 4
        else:
            continue
    if chunk:
        segments.append((chunk_addr, bytes(chunk)))
    if not segments:
        raise ValueError(f"{path}: no data records")
    return segments


def write_ihex(path: pathlib.Path, segments: list[tuple[int, bytes]]) -> None:
    """Write sorted address/data segments as Intel HEX."""
    lines: list[str] = []
    current_upper: int | None = None

    def record(address: int, kind: int, payload: bytes) -> None:
        data = bytearray([len(payload), (address >> 8) & 0xFF,
                          address & 0xFF, kind]) + payload
        data.append((-sum(data)) & 0xFF)
        lines.append(":" + data.hex().upper())

    for address, data in sorted(segments, key=lambda item: item[0]):
        upper = address >> 16
        if upper != current_upper:
            record(0, 0x04, bytes([upper >> 8, upper & 0xFF]))
            current_upper = upper
        offset = 0
        while offset < len(data):
            take = min(16, len(data) - offset)
            record(address + offset, 0x00, data[offset:offset + take])
            offset += take
    record(0, 0x01, b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def factory_segments(boot: pathlib.Path, image_a: pathlib.Path,
                     image_b: pathlib.Path | None, seq_a: int,
                     seq_b: int, confirmed_a: bool = True,
                     confirmed_b: bool = True) -> list[tuple[int, bytes]]:
    """BOOT + slot A (and optionally slot B) at physical addresses.

    ``image_b=None`` leaves slot B and metadata B erased, which is the
    shipped Flow-A layout (P3-02).  ``confirmed_b=False`` writes an
    ACTIVATED-but-not-CONFIRMED test image into slot B so BOOT boots it on
    trial and falls back to the CONFIRMED slot A (Flow B).
    """
    if boot.suffix.lower() == ".hex":
        segments = read_ihex(boot)
    elif boot.suffix.lower() == ".bin":
        segments = [(BOOT_BASE, boot.read_bytes())]
    else:
        raise ValueError("--boot must be a .hex or .bin file")
    for address, data in segments:
        if address < BOOT_BASE or \
                address + len(data) > BOOT_BASE + BOOT_REGION_SIZE:
            raise ValueError(
                f"BOOT data at 0x{address:08x} exceeds the 32 KB region")

    planned = [
        ("A", image_a, seq_a, SLOT_A_BASE, META_A_BASE, confirmed_a),
        ("B", image_b, seq_b, SLOT_B_BASE, META_B_BASE, confirmed_b),
    ]
    for slot, image, sequence, slot_base, meta_base, confirmed in planned:
        if image is None:
            continue
        header, body, image_crc = read_image(image)
        metadata = build_metadata(header, image_crc, sequence, confirmed)
        if len(body) > SLOT_SIZE:
            raise ValueError(f"slot {slot}: image exceeds the slot")
        segments.append((slot_base, body))
        segments.append((meta_base, metadata))
    return segments


def inspect(path: pathlib.Path, key_path: pathlib.Path | None = None) -> int:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        print(f"{path}: shorter than a 256-byte header", file=sys.stderr)
        return 1

    header = data[:HEADER_SIZE]
    fields = {}
    offset = 0
    for name, fmt in HEADER_FIELDS:
        fields[name], = struct.unpack_from(fmt, header, offset)
        offset += struct.calcsize(fmt)
    git_commit = header[36:56]
    name = header[56:88].rstrip(b"\0").decode("ascii", "replace")
    header_crc, = struct.unpack_from("<I", header, 252)
    body = data[HEADER_SIZE:]

    errors = []
    if fields["magic"] != MAGIC:
        errors.append("bad magic")
    signed = fields["header_version"] == HEADER_VERSION
    if fields["header_version"] not in (HEADER_VERSION,
                                        HEADER_VERSION_UNSIGNED):
        errors.append("bad header version")
    if fields["header_size"] != HEADER_SIZE:
        errors.append("bad header size")
    if fields["image_type"] != IMAGE_TYPE_APPLICATION:
        errors.append("bad image type")
    if fields["load_address"] != LOAD_ADDRESS:
        errors.append("bad load address")
    if fields["image_size"] > SLOT_SIZE:
        errors.append("image size exceeds the slot")
    if fields["image_size"] != len(body):
        errors.append("image_size does not match the file body")
    if zlib.crc32(header[:252]) & 0xFFFFFFFF != header_crc:
        errors.append("header CRC32 mismatch")
    if zlib.crc32(body) & 0xFFFFFFFF != fields["image_crc32"]:
        errors.append("image CRC32 mismatch")
    errors.extend(validate_binary(body))
    security_counter, image_key_id = struct.unpack_from(
        "<II", header, SECURITY_COUNTER_OFFSET)
    image_sha = header[IMAGE_SHA256_OFFSET:IMAGE_SHA256_OFFSET + 32]
    signature = "unsigned (version 1)"
    if signed:
        if hashlib.sha256(body).digest() != image_sha:
            errors.append("image SHA-256 mismatch")
        signature = "present, not checked (pass --key)"
        if key_path is not None:
            try:
                key = load_key(key_path)
            except (OSError, ValueError) as error:
                errors.append(str(error))
            else:
                if key_id(public_point(key)) != image_key_id:
                    signature = "signed with a different key"
                    errors.append(signature)
                elif signature_valid(header, key):
                    signature = "valid"
                else:
                    signature = "INVALID"
                    errors.append("signature is invalid")
    elif key_path is not None:
        errors.append("image is not signed")

    print(f"file          : {path}")
    print(f"magic         : 0x{fields['magic']:08x}")
    print(f"header        : version {fields['header_version']}, "
          f"{fields['header_size']} bytes")
    print(f"image type    : {fields['image_type']}")
    print(f"version       : {version_text(fields['version'])}")
    print(f"image size    : {fields['image_size']} bytes")
    print(f"image CRC32   : 0x{fields['image_crc32']:08x}")
    print(f"load address  : 0x{fields['load_address']:08x}")
    print(f"entry address : 0x{fields['entry_address']:08x}")
    print(f"build time    : {fields['build_timestamp']}")
    print(f"git commit    : {commit_text(git_commit)}")
    print(f"name          : {name}")
    print(f"header CRC32  : 0x{header_crc:08x}")
    if signed:
        print(f"security ctr  : {security_counter}")
        print(f"key id        : 0x{image_key_id:08x}")
        print(f"image SHA-256 : {image_sha.hex()}")
    print(f"signature     : {signature}")

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--commit", default="")
    parser.add_argument("--timestamp", default="0")
    parser.add_argument("--name", default="stm32-application")
    parser.add_argument("--key", type=pathlib.Path,
                        help="P-256 PEM key: sign the image (private key), "
                             "or check --inspect (private or public key)")
    parser.add_argument("--security-counter", type=lambda v: int(v, 0),
                        default=None,
                        help="anti-rollback counter for a signed image "
                             "(default 0, or kept by --sign)")
    parser.add_argument("--sign", type=pathlib.Path, metavar="IMG",
                        help="sign an existing .img with --key into --output")
    parser.add_argument("--genkey", type=pathlib.Path, metavar="PEM",
                        help="generate a new P-256 private key")
    parser.add_argument("--export-pubkey", type=pathlib.Path, metavar="PEM",
                        help="write the C definition of BOOT's public key "
                             "for this PEM to --output")
    parser.add_argument("--inspect", type=pathlib.Path,
                        help="print and check an existing image file")
    parser.add_argument("--metadata", choices=("A", "B"),
                        help="build the 16 KB metadata sector for a slot "
                             "from --image (P1-4)")
    parser.add_argument("--image", type=pathlib.Path,
                        help="validated .img used by --metadata")
    parser.add_argument("--sequence", type=int, default=None,
                        help="ACTIVATED sequence for --metadata "
                             "(default: A=1, B=2)")
    parser.add_argument("--no-confirmed", action="store_true",
                        help="leave the CONFIRMED record erased")
    parser.add_argument("--factory-hex", type=pathlib.Path, metavar="OUT",
                        help="write BOOT + both slots + both metadata "
                             "sectors as one Intel HEX file")
    parser.add_argument("--boot", type=pathlib.Path,
                        help=".hex or .bin BOOT image for --factory-hex")
    parser.add_argument("--slot-a", type=pathlib.Path,
                        help="slot A .img for --factory-hex")
    parser.add_argument("--slot-b", type=pathlib.Path, default=None,
                        help="slot B .img for --factory-hex (omit to leave "
                             "slot B empty)")
    parser.add_argument("--slot-b-unconfirmed", action="store_true",
                        help="--factory-hex: write slot B ACTIVATED but not "
                             "CONFIRMED (a trial test image for Flow B)")
    parser.add_argument("--seq-a", type=int, default=1)
    parser.add_argument("--seq-b", type=int, default=2)
    args = parser.parse_args()

    if args.inspect is not None:
        return inspect(args.inspect, args.key)

    if args.genkey is not None:
        try:
            generate_key(args.genkey)
            point = public_point(load_key(args.genkey))
        except (OSError, ValueError) as error:
            print(f"mkimage: {error}", file=sys.stderr)
            return 1
        print(f"mkimage: {args.genkey} (P-256 private key, "
              f"key id 0x{key_id(point):08x})")
        return 0

    if args.export_pubkey is not None:
        if args.output is None:
            parser.error("--export-pubkey needs --output")
        try:
            key = load_key(args.export_pubkey)
            source = pubkey_c_source(key, args.export_pubkey.name)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(source)
        except (OSError, ValueError) as error:
            print(f"mkimage: {error}", file=sys.stderr)
            return 1
        print(f"mkimage: {args.output} (key id "
              f"0x{key_id(public_point(key)):08x})")
        return 0

    if args.sign is not None:
        if args.key is None or args.output is None:
            parser.error("--sign needs --key and --output")
        try:
            header, body, _crc = read_image(args.sign)
            signed = sign_header(header, body, load_key(args.key),
                                 args.security_counter)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(signed + body)
        except (OSError, ValueError) as error:
            print(f"mkimage: {error}", file=sys.stderr)
            return 1
        counter, ident = struct.unpack_from("<II", signed,
                                            SECURITY_COUNTER_OFFSET)
        print(f"mkimage: {args.output} (signed, key id 0x{ident:08x}, "
              f"security counter {counter})")
        return 0

    if args.factory_hex is not None:
        if not (args.boot and args.slot_a):
            parser.error("--factory-hex needs --boot and --slot-a")
        try:
            segments = factory_segments(
                args.boot, args.slot_a, args.slot_b, args.seq_a, args.seq_b,
                confirmed_b=not args.slot_b_unconfirmed)
            write_ihex(args.factory_hex, segments)
        except (OSError, ValueError) as error:
            print(f"mkimage: {error}", file=sys.stderr)
            return 1
        total = sum(len(data) for _address, data in segments)
        print(f"mkimage: {args.factory_hex} (factory image, "
              f"{total} bytes in {len(segments)} segments)")
        return 0

    if args.metadata is not None:
        if args.image is None or args.output is None:
            parser.error("--metadata needs --image and --output")
        try:
            header, _body, image_crc = read_image(args.image)
            sequence = args.sequence
            if sequence is None:
                sequence = 1 if args.metadata == "A" else 2
            sector = build_metadata(header, image_crc, sequence,
                                    not args.no_confirmed)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.output.suffix.lower() == ".hex":
                base = META_A_BASE if args.metadata == "A" else META_B_BASE
                write_ihex(args.output, [(base, sector)])
            else:
                args.output.write_bytes(sector)
        except (OSError, ValueError) as error:
            print(f"mkimage: {error}", file=sys.stderr)
            return 1
        print(f"mkimage: {args.output} (metadata slot {args.metadata}, "
              f"sequence {sequence}, {len(sector)} bytes)")
        return 0

    if args.input is None or args.output is None:
        parser.error("--input and --output are required unless --inspect")

    binary = args.input.read_bytes()
    errors = validate_binary(binary)
    if errors:
        for error in errors:
            print(f"mkimage: {error}", file=sys.stderr)
        return 1

    try:
        header = build_header(binary, args)
    except (OSError, ValueError) as error:
        print(f"mkimage: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(header + binary)

    image_crc = zlib.crc32(binary) & 0xFFFFFFFF
    state = "signed" if args.key is not None else "unsigned"
    print(f"mkimage: {args.output} ({HEADER_SIZE + len(binary)} bytes, "
          f"version {args.version}, crc 0x{image_crc:08x}, {state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
