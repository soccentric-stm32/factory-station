from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from hostcore import layout
from hostcore.images import (
    ImageError,
    build_image,
    make_metadata_record,
    parse_image,
    parse_metadata_record,
    validate_vectors,
)

ROOT = Path(__file__).resolve().parents[1]


def _valid_payload(size: int = 256) -> bytes:
    payload = bytearray(size)
    struct.pack_into("<I", payload, 0, 0x20020000)
    struct.pack_into("<I", payload, 4, layout.SLOT_A_BASE + 1)
    return bytes(payload)


def test_ibwh_build_and_parse(tmp_path: Path) -> None:
    image = build_image(
        _valid_payload(), version=0x01020003, name="app", build_timestamp=1234, git_commit="ab" * 20
    )
    path = tmp_path / "app.img"
    path.write_bytes(image)
    parsed = parse_image(path)
    assert parsed.kind == "img"
    assert parsed.version_string() == "1.2.3"
    assert parsed.load_address == layout.SLOT_A_BASE
    assert parsed.header_crc32 == struct.unpack_from("<I", image, 252)[0]
    assert parsed.crc32 == zlib.crc32(parsed.data) & 0xFFFFFFFF


def test_real_app_image_is_accepted() -> None:
    path = ROOT / "profiles/sc767-1.0.0/app-0.1.0.img"
    parsed = parse_image(path)
    assert parsed.version_string() == "0.1.0"
    assert parsed.load_address == layout.SLOT_A_BASE
    assert len(parsed.header) == layout.IMAGE_HEADER_SIZE


def test_corrupt_ibwh_is_rejected(tmp_path: Path) -> None:
    image = bytearray(build_image(_valid_payload(), version=0x00010000, name="app"))
    image[252] ^= 0xFF
    path = tmp_path / "bad.img"
    path.write_bytes(image)
    with pytest.raises(ImageError, match="header CRC32"):
        parse_image(path)


def test_metadata_records_use_appendix_b_offsets() -> None:
    image = build_image(_valid_payload(), version=0x00010000, name="app")
    path = Path("/tmp/does-not-matter.img")
    parsed = parse_image_from_bytes(path, image)
    records = parsed.to_metadata_records(sequence=9)
    offsets = parsed.metadata_offsets()
    assert offsets == [
        0,
        layout.META_VERIFIED_OFFSET,
        layout.META_ACTIVATED_OFFSET,
        layout.META_CONFIRMED_OFFSET,
    ]
    assert records[0] == image[: layout.IMAGE_HEADER_SIZE]
    verified = parse_metadata_record(records[1])
    assert verified == (layout.META_VERIFIED_MAGIC, parsed.crc32, True)
    activated = parse_metadata_record(records[2])
    assert activated == (layout.META_ACTIVATED_MAGIC, 9, True)
    assert parse_metadata_record(make_metadata_record(layout.META_CONFIRMED_MAGIC, 0))[2]


def test_vector_validation() -> None:
    assert not validate_vectors(_valid_payload())
    bad = bytearray(_valid_payload())
    struct.pack_into("<I", bad, 0, 0x30000000)
    assert any("SP" in error for error in validate_vectors(bytes(bad)))


def parse_image_from_bytes(path: Path, blob: bytes):
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as handle:
        handle.write(blob)
        name = handle.name
    try:
        parsed = parse_image(name)
        parsed.path = path
        return parsed
    finally:
        Path(name).unlink(missing_ok=True)


def _p256_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


def test_signed_v2_header_round_trip(tmp_path: Path) -> None:
    from hostcore.images import key_id, load_key, public_point, sign_header

    key = _p256_key()
    payload = _valid_payload()
    image = build_image(
        payload,
        version=0x01020003,
        name="app",
        signing_key=key,
        security_counter=7,
    )
    path = tmp_path / "signed.img"
    path.write_bytes(image)
    parsed = parse_image(path, public_key=key)
    assert parsed.header_format == 2
    assert parsed.is_signed
    assert parsed.security_counter == 7
    assert parsed.key_id == key_id(public_point(key))
    assert parsed.image_sha256
    assert parsed.verify_body_sha256()
    assert parsed.verify_signature(key)
    assert parsed.expected_floor == 7
    # The header's bytes 88..251 travel verbatim over HWLINK.
    assert parsed.header_dict()["extension"] == image[88:252]
    assert len(parsed.header_dict()["extension"]) == layout.IMAGE_EXTENSION_SIZE


def test_signed_image_rejects_wrong_key_and_tampering(tmp_path: Path) -> None:
    key = _p256_key()
    blob = bytearray(
        build_image(_valid_payload(), version=0x10000, name="app", signing_key=key)
    )
    path = tmp_path / "signed.img"
    path.write_bytes(blob)
    parsed = parse_image(path)
    assert not parsed.verify_signature(_p256_key())

    flipped = bytearray(blob)
    flipped[layout.IMAGE_SIGNATURE_OFFSET] ^= 0x01
    struct.pack_into(
        "<I", flipped, layout.IMAGE_HEADER_CRC_OFFSET,
        zlib.crc32(flipped[:252]) & 0xFFFFFFFF,
    )
    path.write_bytes(flipped)
    parsed = parse_image(path)  # header CRC repaired; signature must still fail
    assert not parsed.verify_signature(key)

    body_changed = bytearray(blob)
    body_changed[-1] ^= 0xFF
    # Repair the payload CRC and header CRC so the signed-image SHA-256 check
    # is the check that fails.
    struct.pack_into(
        "<I", body_changed, 20, zlib.crc32(body_changed[layout.IMAGE_HEADER_SIZE:]) & 0xFFFFFFFF
    )
    struct.pack_into(
        "<I", body_changed, layout.IMAGE_HEADER_CRC_OFFSET,
        zlib.crc32(body_changed[:252]) & 0xFFFFFFFF,
    )
    path.write_bytes(body_changed)
    with pytest.raises(ImageError, match="SHA-256"):
        parse_image(path)


def test_signed_header_crc_is_enforced(tmp_path: Path) -> None:
    key = _p256_key()
    blob = bytearray(
        build_image(_valid_payload(), version=0x10000, name="app", signing_key=key)
    )
    blob[layout.IMAGE_HEADER_CRC_OFFSET] ^= 0xFF
    path = tmp_path / "bad.img"
    path.write_bytes(blob)
    with pytest.raises(ImageError, match="header CRC32"):
        parse_image(path)


def test_production_parse_requires_a_signature(tmp_path: Path) -> None:
    unsigned = tmp_path / "unsigned.img"
    unsigned.write_bytes(build_image(_valid_payload(), version=0x10000, name="app"))
    assert parse_image(unsigned).header_format == layout.IMAGE_HEADER_VERSION_UNSIGNED
    with pytest.raises(ImageError, match="must be signed"):
        parse_image(unsigned, require_signature=True)


def test_sign_header_matches_mkimage_layout(tmp_path: Path) -> None:
    from hostcore.images import sign_header

    key = _p256_key()
    body = _valid_payload()
    image = build_image(body, version=0x10000, name="app")
    signed = sign_header(image[:256], body, key, 3)
    assert signed[4:6] == struct.pack("<H", 2)
    assert struct.unpack_from("<I", signed, 88)[0] == 3
    assert signed[96:128] == __import__("hashlib").sha256(body).digest()
    assert signed[128:192] != bytes(64)
