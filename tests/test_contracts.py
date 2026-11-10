"""Cross-repo contract checks (J-02, P0-08).

These tests are deliberately strict: the station's BOOT contract must come
from the bootloader repository and its HWVAL contract from
validation-firmware.  If the firmware changes a header version, a field
offset, the protocol minor or a layout constant, this file fails instead of
letting the station drift (R7).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from hostcore import layout
from hostcore.protocol.link import PROTOCOL_MINOR

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"contract source not present: {path}")
    return path.read_text(encoding="utf-8")


def _define(text: str, name: str) -> int:
    match = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+|\d+)", text)
    assert match, f"{name} missing from boot_layout.h"
    token = match.group(1)
    return int(token, 16) if token.startswith("0x") else int(token)


def _boot_schema() -> Path:
    return WORKSPACE / "bootloader/protocol/hwlink.fbs"


def _hwval_schema() -> Path:
    return WORKSPACE / "validation-firmware/protocol/hwlink.fbs"


def _union_members(text: str) -> list[str]:
    match = re.search(r"union\s+Body\s*\{(.*?)\}", text, re.S)
    assert match, "schema has no union Body"
    return [line.strip().rstrip(",") for line in match.group(1).splitlines() if line.strip()]


def test_station_schema_is_the_boot_contract_plus_hwval() -> None:
    boot = _read(_boot_schema())
    station = _read(ROOT / "src/hostcore/protocol/hwlink.fbs")
    marker = "// The union order is part of the wire format. Append new members only."
    boot_prefix = boot[: boot.find(marker)].rstrip()
    station_prefix = station[: station.find(marker)].rstrip()
    # The BOOT tables/enums in the station must be byte-identical to BOOT's.
    assert station_prefix.startswith(boot_prefix)

    # The appended HWVAL-only tables must match validation-firmware exactly.
    hwval_marker = "// ------------------------------------------------------------------- HWVAL"
    hwval = _read(_hwval_schema())
    hwval_tables = hwval[hwval.find(hwval_marker) : hwval.find(marker)].strip()
    station_tables = station[station.find(hwval_marker) : station.find(marker)].strip()
    assert station_tables == hwval_tables

    # Append-only union: BOOT's members are the prefix of the station union.
    assert _union_members(station)[: len(_union_members(boot))] == _union_members(boot)


def test_vendored_bundle_schema_and_hash_are_current() -> None:
    vendored = ROOT / "src/hostcore/vendor/protocol/hwlink.fbs"
    if not vendored.is_file():
        pytest.skip("vendor bundle not installed")
    assert vendored.read_text(encoding="utf-8") == _read(_boot_schema())

    record = json.loads((ROOT / "src/hostcore/BUNDLE").read_text(encoding="utf-8"))
    composed = (ROOT / "src/hostcore/protocol/hwlink.fbs").read_bytes()
    assert record["schema_sha256"] == hashlib.sha256(composed).hexdigest()
    assert (ROOT / "src/hostcore/vendor/SHA256SUMS").is_file()


def test_protocol_minor_matches_boot() -> None:
    header = _read(WORKSPACE / "bootloader/src/protocol/hwlink.h")
    match = re.search(r"HWLINK_PROTOCOL_MINOR\s+(\d+)U", header)
    assert match, "HWLINK_PROTOCOL_MINOR missing from boot hwlink.h"
    boot_minor = int(match.group(1))
    bootctl = _read(WORKSPACE / "bootloader/tools/bootctl.py")
    python_match = re.search(r"PROTOCOL_MINOR\s*=\s*(\d+)", bootctl)
    assert python_match, "PROTOCOL_MINOR missing from bootctl.py"
    assert boot_minor == int(python_match.group(1)) == PROTOCOL_MINOR == 3


def test_image_header_v2_offsets_match_mkimage() -> None:
    mkimage = _read(WORKSPACE / "bootloader/tools/mkimage.py")
    expected = {
        "SECURITY_COUNTER_OFFSET": layout.IMAGE_SECURITY_COUNTER_OFFSET,
        "KEY_ID_OFFSET": layout.IMAGE_KEY_ID_OFFSET,
        "IMAGE_SHA256_OFFSET": layout.IMAGE_SHA256_OFFSET,
        "SIGNATURE_OFFSET": layout.IMAGE_SIGNATURE_OFFSET,
        "EXT_OFFSET": layout.IMAGE_EXTENSION_OFFSET,
        "EXT_SIZE": layout.IMAGE_EXTENSION_SIZE,
        "SIGNATURE_SIZE": layout.IMAGE_SIGNATURE_SIZE,
        "SIGNED_SIZE": layout.IMAGE_SIGNED_SIZE,
    }
    for name, value in expected.items():
        match = re.search(rf"^{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", mkimage, re.M)
        assert match, f"{name} missing from mkimage.py"
        token = match.group(1)
        assert int(token, 0) == value, f"{name} drifted from the station"

    match = re.search(r"^HEADER_VERSION\s*=\s*(\d+)", mkimage, re.M)
    assert match and int(match.group(1)) == layout.IMAGE_HEADER_VERSION == 2
    match = re.search(r"^HEADER_VERSION_UNSIGNED\s*=\s*(\d+)", mkimage, re.M)
    assert match and int(match.group(1)) == layout.IMAGE_HEADER_VERSION_UNSIGNED
    assert re.search(r"HEADER_SIZE\s*=\s*256", mkimage)
    assert re.search(r"MAGIC\s*=\s*0x48574249", mkimage)
    assert re.search(r"LOAD_ADDRESS\s*=\s*0x0800C000", mkimage)
    # header_crc32 is over bytes 0..251.
    assert re.search(r"struct\.pack_into\(\"<I\", out, 252, zlib\.crc32\(out\[:252\]\)", mkimage)


def test_boot_layout_constants_match_firmware() -> None:
    text = _read(WORKSPACE / "bootloader/src/common/boot_layout.h")

    assert _define(text, "BOOT_BANK1_BASE") == layout.BANK1_BASE
    assert _define(text, "BOOT_BANK2_BASE") == layout.BANK2_BASE
    assert _define(text, "BOOT_SLOT_OFFSET") == layout.SLOT_OFFSET
    assert _define(text, "BOOT_META_OFFSET") == layout.META_OFFSET
    assert _define(text, "BOOT_BANK1_BASE") + _define(text, "BOOT_SLOT_OFFSET") == layout.SLOT_A_BASE
    assert _define(text, "BOOT_BANK1_BASE") + _define(text, "BOOT_META_OFFSET") == layout.META_A_BASE
    assert _define(text, "BOOT_META_HEADER_OFFSET") == layout.META_HEADER_OFFSET
    assert _define(text, "BOOT_META_VERIFIED_OFFSET") == layout.META_VERIFIED_OFFSET
    assert _define(text, "BOOT_META_ACTIVATED_OFFSET") == layout.META_ACTIVATED_OFFSET
    assert _define(text, "BOOT_META_CONFIRMED_OFFSET") == layout.META_CONFIRMED_OFFSET
    assert _define(text, "BOOT_META_REJECTED_OFFSET") == layout.META_REJECTED_OFFSET
    assert _define(text, "BOOT_META_RECORD_SIZE") == layout.META_RECORD_SIZE
    assert _define(text, "BOOT_META_HEADER_SIZE") == layout.META_HEADER_SIZE
    assert _define(text, "BOOT_SLOT_SIZE") == layout.SLOT_SIZE
    assert _define(text, "BOOT_META_SIZE") == layout.META_SIZE
    assert _define(text, "BOOT_IDENTITY_OFFSET") == 0x4000
    assert _define(text, "BOOT_IDENTITY_SIZE") == layout.IDENTITY_SIZE
    assert _define(text, "BOOT_BANK2_BASE") + _define(text, "BOOT_IDENTITY_OFFSET") == layout.IDENTITY_BASE
    assert (
        _define(text, "BOOT_BANK1_BASE") + _define(text, "BOOT_IDENTITY_OFFSET")
        == layout.IDENTITY_BASE_SWAPPED
    )


def test_generated_code_exposes_minor_3_fields() -> None:
    from hostcore.protocol.generated.hwlink import BootInfo, BootSlotInfo, Error, ImageHeader, UpdateBegin
    from hostcore.protocol.generated.hwlink.Body import Body

    assert Error.Error.AUTH_FAILED == 14
    assert Error.Error.ROLLBACK == 15
    for method in ("HeaderCrc32", "Extension"):
        assert hasattr(ImageHeader.ImageHeader, method), method
    assert hasattr(BootSlotInfo.BootSlotInfo, "SecurityCounter")
    for method in (
        "ActiveValid",
        "TargetSlot",
        "BootVersion",
        "BootBuild",
        "SignedImages",
        "KeyId",
        "SecurityFloor",
    ):
        assert hasattr(BootInfo.BootInfo, method), method
    assert hasattr(UpdateBegin.UpdateBegin, "Force")
    # HWVAL bodies must still be present after the append-only union update.
    assert Body.GetDeviceInfo and Body.OperatorPromptDone


def test_firmware_schema_minor_is_not_older_than_used_messages() -> None:
    """HWVAL's own schema must contain the messages the station sends it."""
    schema = _read(_hwval_schema())
    for table in (
        "GetDeviceInfo",
        "ListTests",
        "Run",
        "OperatorAnswer",
        "GetResults",
        "GetBootInfo",
    ):
        assert re.search(rf"table\s+{table}\b", schema), f"HWVAL schema lacks {table}"
    header = _read(WORKSPACE / "validation-firmware/src/hwlink.h")
    match = re.search(r"HWLINK_PROTOCOL_MINOR\s+(\d+)U", header)
    assert match, "HWVAL protocol minor is missing"
    # HWVAL answers at minor 1; the station only relies on its HWVAL messages,
    # never on BOOT's minor 2/3 fields over the HWVAL connection.
    assert int(match.group(1)) >= 1
