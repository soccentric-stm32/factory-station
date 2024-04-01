"""One source of truth for the STM32F767ZI flash layout (B-08).

The constants mirror ``bootloader/src/common/boot_layout.h``.  The factory,
the profile loader and the simulator all import them from here so a profile
cannot silently program a different address than the firmware expects.
"""

from __future__ import annotations

BANK1_BASE = 0x0800_0000
BANK2_BASE = 0x0810_0000
BANK_SIZE = 0x0010_0000

BOOT_REGION_OFFSET = 0x0000_0000
BOOT_REGION_SIZE = 0x0000_8000
META_OFFSET = 0x0000_8000
META_SIZE = 0x0000_4000
SLOT_OFFSET = 0x0000_C000
SLOT_SIZE = 0x000F_4000

META_A_BASE = BANK1_BASE + META_OFFSET          # 0x08008000
META_B_BASE = BANK2_BASE + META_OFFSET          # 0x08108000
SLOT_A_BASE = BANK1_BASE + SLOT_OFFSET          # 0x0800C000
SLOT_B_BASE = BANK2_BASE + SLOT_OFFSET          # 0x0810C000
APP_LOAD_ADDR = SLOT_A_BASE

RAM_START = 0x2000_0000
RAM_END = 0x2008_0000

# Appendix B metadata record offsets in a metadata sector.
META_HEADER_OFFSET = 0x000
META_VERIFIED_OFFSET = 0x100
META_ACTIVATED_OFFSET = 0x120
META_CONFIRMED_OFFSET = 0x140
META_REJECTED_OFFSET = 0x160
META_RECORD_SIZE = 32
META_HEADER_SIZE = 256

META_VERIFIED_MAGIC = 0x5645_5249   # "VERI"
META_ACTIVATED_MAGIC = 0x4143_5456  # "ACTV"
META_CONFIRMED_MAGIC = 0x434F_4E46  # "CONF"
META_REJECTED_MAGIC = 0x5245_4A45   # "REJE"

# A-01 identity/provisioning area: bank 2 sector 13 (16 KB).
IDENTITY_BASE = 0x0810_4000
IDENTITY_BASE_SWAPPED = 0x0800_4000
IDENTITY_SIZE = 0x4000

# Sector map (physical HAL sector numbers).
BANK1_SECTORS = 12
FLASH_SIZE = BANK_SIZE * 2

# Image header constants (IBWH).  The bootloader builds version 2 (signed)
# headers; version 1 is the pre-signing format and is refused by a production
# profile.  Offsets follow bootloader/tools/mkimage.py.
IMAGE_MAGIC = 0x4857_4249       # "IBWH" little-endian
IMAGE_HEADER_VERSION = 2
IMAGE_HEADER_VERSION_UNSIGNED = 1
IMAGE_HEADER_VERSIONS = (IMAGE_HEADER_VERSION_UNSIGNED, IMAGE_HEADER_VERSION)
IMAGE_HEADER_SIZE = 256
IMAGE_TYPE_APPLICATION = 1

IMAGE_SECURITY_COUNTER_OFFSET = 88
IMAGE_KEY_ID_OFFSET = 92
IMAGE_SHA256_OFFSET = 96
IMAGE_SHA256_SIZE = 32
IMAGE_SIGNATURE_OFFSET = 128
IMAGE_SIGNATURE_SIZE = 64
IMAGE_SIGNED_SIZE = 128         # signature covers header bytes 0..127
IMAGE_EXTENSION_OFFSET = 88     # hwlink.ImageHeader.extension = bytes 88..251
IMAGE_EXTENSION_SIZE = 164
IMAGE_HEADER_CRC_OFFSET = 252

# The published development key, allowed only in non-production profiles
# (P0-09 / section 6.3 item 2).
DEVELOPMENT_KEY_ID = 0xFAA7_171B

# BOOT settle rules (ENT-001, P0-06): after a reset stay off the port until
# the 500 ms listen window and the image checks are over.  Shared with
# bootctl.BOOT_SETTLE_S.
BOOT_LISTEN_WINDOW_S = 0.5
BOOT_SETTLE_S = 2.0
MAX_BOOT_RELEASES = 3
CATCH_INTERVAL_S = 0.05

# Factory/update transport selection (profile.app_load).
APP_LOAD_CHOICES = ("swd", "boot")
TEST_MODE_CHOICES = ("bench", "under_boot")
SLOT_B_AFTER_TEST_CHOICES = ("erase", "leave_superseded")


__all__ = [name for name in globals() if name.isupper()]
