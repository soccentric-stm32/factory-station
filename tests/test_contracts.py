"""Cross-repo contract checks (J-02).

The sibling firmware repositories are optional on a developer PC and absent on
a factory-only CI node.  When they are present, a changed flash address,
image magic or protocol version fails this test instead of silently drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hostcore import layout

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"contract source not present: {path}")
    return path.read_text(encoding="utf-8")


def test_boot_layout_constants_match_firmware() -> None:
    text = _read(WORKSPACE / "soccentric-stm32-bootloader/src/common/boot_layout.h")

    def define(name: str) -> int:
        match = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+|\d+)", text)
        assert match, f"{name} missing from boot_layout.h"
        token = match.group(1)
        return int(token, 16) if token.startswith("0x") else int(token)

    assert define("BOOT_BANK1_BASE") == layout.BANK1_BASE
    assert define("BOOT_BANK2_BASE") == layout.BANK2_BASE
    assert define("BOOT_SLOT_OFFSET") == layout.SLOT_OFFSET
    assert define("BOOT_META_OFFSET") == layout.META_OFFSET
    assert define("BOOT_BANK1_BASE") + define("BOOT_SLOT_OFFSET") == layout.SLOT_A_BASE
    assert define("BOOT_BANK1_BASE") + define("BOOT_META_OFFSET") == layout.META_A_BASE
    assert define("BOOT_META_VERIFIED_OFFSET") == layout.META_VERIFIED_OFFSET
    assert define("BOOT_META_ACTIVATED_OFFSET") == layout.META_ACTIVATED_OFFSET
    assert define("BOOT_META_CONFIRMED_OFFSET") == layout.META_CONFIRMED_OFFSET
    assert define("BOOT_META_RECORD_SIZE") == layout.META_RECORD_SIZE
    assert define("BOOT_META_HEADER_SIZE") == layout.META_HEADER_SIZE
    assert define("BOOT_SLOT_SIZE") == layout.SLOT_SIZE
    assert define("BOOT_META_SIZE") == layout.META_SIZE
    assert define("BOOT_IDENTITY_OFFSET") == 0x4000
    assert define("BOOT_IDENTITY_SIZE") == layout.IDENTITY_SIZE
    assert define("BOOT_BANK2_BASE") + define("BOOT_IDENTITY_OFFSET") == layout.IDENTITY_BASE
    assert define("BOOT_BANK1_BASE") + define("BOOT_IDENTITY_OFFSET") == layout.IDENTITY_BASE_SWAPPED


def test_image_header_magic_matches_mkimage() -> None:
    text = _read(WORKSPACE / "soccentric-stm32-bootloader/tools/mkimage.py")
    match = re.search(r"MAGIC\s*=\s*(0x[0-9A-Fa-f]+)", text)
    assert match and int(match.group(1), 16) == layout.IMAGE_MAGIC
    assert re.search(r"HEADER_SIZE\s*=\s*256", text)
    assert re.search(r"LOAD_ADDRESS\s*=\s*0x0800C000", text)


def test_hwlink_schema_snapshot_matches_firmware() -> None:
    firmware = _read(WORKSPACE / "soccentric-stm32-validation-firmware/protocol/hwlink.fbs")
    snapshot = _read(ROOT / "src/hostcore/protocol/hwlink.fbs")
    assert snapshot == firmware
    header = _read(WORKSPACE / "soccentric-stm32-validation-firmware/src/hwlink.h")
    assert re.search(r"HWLINK_PROTOCOL_MAJOR\s+1U", header)
    assert re.search(r"HWLINK_MAX_FRAME\s+2048U", header)
