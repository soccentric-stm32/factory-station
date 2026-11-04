from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.profile import ProfileError, load_profile, validate_manifest_layout

ROOT = Path(__file__).resolve().parents[1]


def test_demo_and_released_profiles_load() -> None:
    demo = load_profile(ROOT / "examples/profile-sc767-demo")
    real = load_profile(ROOT / "profiles/sc767-1.0.0")
    assert demo.app_image.image is not None
    assert real.app_image.image.version_string() == "0.1.0"
    assert real.protection.rdp == 1
    assert real.protection.ndboot == 1


def test_layout_validation_rejects_wrong_slot() -> None:
    raw = {
        "layout": {"slot_a": "0x08020000"},
        "boot_metadata": {"address": "0x08008000"},
        "provisioning": {"address": "0x08104000"},
        "protection": {"nDBANK": 0, "nDBOOT": 1, "BOOT_ADD0": 0x0080, "rdp": 1},
    }
    with pytest.raises(ProfileError, match="slot_a"):
        validate_manifest_layout(raw)
    raw["layout"]["slot_a"] = "0x0800C000"
    raw["boot_metadata"]["address"] = "0x0801F000"
    with pytest.raises(ProfileError, match="boot_metadata"):
        validate_manifest_layout(raw)


def test_rdp2_is_rejected() -> None:
    raw = {
        "boot_metadata": {"address": "0x08008000"},
        "provisioning": {"address": "0x08104000"},
        "protection": {"nDBANK": 0, "nDBOOT": 1, "BOOT_ADD0": 0x0080, "rdp": 2},
    }
    with pytest.raises(ProfileError, match="RDP level 2"):
        validate_manifest_layout(raw)
