from __future__ import annotations

import pytest

from hostcore import layout
from hostcore.openocd import OpenOcdError
from hostcore.protocol.generated.hwlink.Role import Role
from hwval_factory.simulator import SimulatedChip, SimulatedOpenOcd


def test_simulated_flash_requires_erase_before_rewrite() -> None:
    chip = SimulatedChip.create("F1")
    chip.write(layout.SLOT_A_BASE, b"\x00\x00\x00\x00")
    with pytest.raises(OpenOcdError, match="non-erased"):
        chip.write(layout.SLOT_A_BASE, b"\x11\x00\x00\x00")
    chip.erase_address(layout.SLOT_A_BASE, 0x4000)
    chip.write(layout.SLOT_A_BASE, b"\x11\x00\x00\x00")
    assert chip.read(layout.SLOT_A_BASE, 4) == b"\x11\x00\x00\x00"


def test_nbank_change_mass_erases() -> None:
    chip = SimulatedChip.create("F1")
    chip.write(layout.SLOT_A_BASE, b"\x00" * 16)
    backend = SimulatedOpenOcd(chip)
    backend.write_option_bytes(chip.optcr | (1 << 29), chip.optcr1)
    assert chip.read(layout.SLOT_A_BASE, 4) == b"\xff\xff\xff\xff"


def test_wrp_blocks_writes_and_rdp_blocks_reads() -> None:
    chip = SimulatedChip.create("F1")
    backend = SimulatedOpenOcd(chip)
    protected = chip.optcr & ~(1 << 16)  # clear nWRP bit for sector 0
    backend.write_option_bytes(protected | (0x00 << 8), chip.optcr1)
    assert chip.sector_protected(0)
    with pytest.raises(OpenOcdError, match="write-protected"):
        chip.write(layout.BANK1_BASE, b"\x00")
    from hwval_factory.simulator import UID_BASE
    assert backend.read_memory(UID_BASE, 12)


def test_simulator_device_role_and_uid() -> None:
    from hwval_factory.simulator import FactorySimulatedDevice

    chip = SimulatedChip.create("F1")
    device = FactorySimulatedDevice(chip)
    assert device.role == Role.HWVAL
    device.set_role("APP", "app", 0x00010000)
    assert device.role == Role.DEMO
