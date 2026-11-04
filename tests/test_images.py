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
