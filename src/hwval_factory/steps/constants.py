"""Constants shared by the step modules."""

from __future__ import annotations

from hostcore import layout

DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FF0F442
UID_BASE = 0x1FF0F420
DEFAULT_SLOT_A = layout.SLOT_A_BASE
DEFAULT_METADATA_BASE = layout.META_A_BASE
SCAN_TIMEOUT_S = 120.0
