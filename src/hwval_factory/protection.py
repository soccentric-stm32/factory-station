"""Option bytes, BOR, WRP and RDP (PROT-*).

RDP level 2 is permanent and disables debug for good.  There is deliberately
no code path that can select it: :class:`ProtectionSpec` rejects level 2 when
the profile is loaded, and every write in this module validates again.
"""

from __future__ import annotations

from typing import Any

from hostcore.openocd import OpenOcdBackend, OptionBytes, option_bytes_to_dict
from hwval_factory.identity import PROVISIONING_SECTOR, PROVISIONING_SECTOR_SWAPPED
from hwval_factory.profile import ProductProfile, ProtectionSpec

DEFAULT_PROVISIONING_SECTOR = PROVISIONING_SECTOR


class ProtectionError(RuntimeError):
    """Option bytes could not be applied or read back."""


def provisioning_address(profile: ProductProfile) -> int:
    raw = profile.manifest.get("provisioning") or {}
    if isinstance(raw, dict) and raw.get("address"):
        return int(str(raw["address"]), 0)
    return DEFAULT_PROVISIONING_SECTOR


def swapped_provisioning_address(profile: ProductProfile) -> int:
    raw = profile.manifest.get("provisioning") or {}
    if isinstance(raw, dict) and raw.get("swapped_address"):
        return int(str(raw["swapped_address"]), 0)
    return PROVISIONING_SECTOR_SWAPPED


def _wrp_mask(sectors: list[int]) -> int:
    value = 0xFFF
    for sector in sectors:
        value &= ~(1 << sector)
    return value & 0xFFF


def expected_option_bytes(spec: ProtectionSpec) -> OptionBytes:
    if spec.rdp == 2:
        raise ProtectionError("RDP level 2 is never permitted (PROT-003)")
    return OptionBytes(optcr=spec.computed_optcr(), optcr1=spec.computed_optcr1())


def compare_option_bytes(spec: ProtectionSpec, actual: OptionBytes) -> list[str]:
    problems: list[str] = []
    expected = expected_option_bytes(spec)
    if actual.rdp != spec.rdp:
        problems.append(f"RDP {actual.rdp} != {spec.rdp}")
    if actual.bor_level != spec.bor_lev:
        problems.append(f"BOR_LEV {actual.bor_level} != {spec.bor_lev}")
    if actual.ndbank != spec.ndbank:
        problems.append(f"nDBANK {actual.ndbank} != {spec.ndbank}")
    if actual.ndboot != spec.ndboot:
        problems.append(f"nDBOOT {actual.ndboot} != {spec.ndboot}")
    expected_wrp1 = _wrp_mask(spec.wrp_bank1)
    expected_wrp2 = _wrp_mask(spec.wrp_bank2)
    expected_wrp = _wrp_mask(sorted(set(spec.wrp_bank1) | set(spec.wrp_bank2)))
    if actual.bank1_wrp() != expected_wrp:
        problems.append(
            f"bank1 nWRP {actual.bank1_wrp():#05x} != {expected_wrp:#05x} "
            f"(profile {expected_wrp1:#05x})"
        )
    if actual.bank2_wrp() != expected_wrp:
        problems.append(
            f"bank2 nWRP {actual.bank2_wrp():#05x} != {expected_wrp:#05x} "
            f"(profile {expected_wrp2:#05x})"
        )
    if spec.optcr is not None and actual.optcr != expected.optcr:
        problems.append(f"OPTCR {actual.optcr:#010x} != {expected.optcr:#010x}")
    if spec.optcr1 is not None and actual.optcr1 != expected.optcr1:
        problems.append(f"OPTCR1 {actual.optcr1:#010x} != {expected.optcr1:#010x}")
    return problems


def apply_protection(backend: OpenOcdBackend, spec: ProtectionSpec) -> dict[str, int]:
    spec.validate()
    if spec.rdp == 2:
        raise ProtectionError("RDP level 2 is never permitted (PROT-003)")
    expected = expected_option_bytes(spec)
    backend.halt()
    backend.write_option_bytes(expected.optcr, expected.optcr1)
    if spec.rdp == 0:
        backend.reset(halt=True)
    actual = backend.read_option_bytes()
    problems = compare_option_bytes(spec, actual)
    if problems:
        raise ProtectionError("option-byte read-back failed: " + "; ".join(problems))
    return option_bytes_to_dict(actual)


def read_options(backend: OpenOcdBackend) -> dict[str, int]:
    backend.halt()
    return option_bytes_to_dict(backend.read_option_bytes())


def lower_rdp_for_rework(
    backend: OpenOcdBackend,
    *,
    confirmed: bool,
    operator: str = "",
) -> dict[str, int]:
    """PROT-006: lower RDP to 0 (mass erase) and remove write protection.

    The caller must pass an explicit ``confirmed=True`` after a dialog that
    states all flash content is lost.
    """
    if not confirmed:
        raise ProtectionError("rework protection removal was not confirmed")
    spec = ProtectionSpec(
        ndbank=0,
        ndboot=0,
        bor_lev=3,
        wrp_bank1=[],
        wrp_bank2=[],
        rdp=0,
    )
    expected = expected_option_bytes(spec)
    current = backend.read_option_bytes()
    if current.rdp != 0:
        # OpenOCD's unlock path is the one that works on RDP 1 silicon; it
        # also mass-erases the chip.
        backend.unlock_rdp()
    else:
        backend.write_option_bytes(expected.optcr, expected.optcr1)
    backend.mass_erase()
    backend.reset(halt=True)
    actual = backend.read_option_bytes()
    if actual.rdp != 0:
        raise ProtectionError(f"RDP read-back {actual.rdp} after unlock")
    if actual.bank1_wrp() != 0xFFF or actual.bank2_wrp() != 0xFFF:
        raise ProtectionError("write protection is still active after rework unlock")
    return option_bytes_to_dict(actual)


def protection_summary(spec: ProtectionSpec) -> str:
    return (
        f"RDP{spec.rdp}, BOR{spec.bor_lev}, nDBANK={spec.ndbank}, "
        f"WRP1={spec.wrp_bank1}, WRP2={spec.wrp_bank2}"
    )


def protection_payload(profile: ProductProfile) -> dict[str, Any]:
    return {
        "protection": profile.protection.as_dict(),
        "address": provisioning_address(profile),
    }
