"""Constants from factory-firmware/docs/FACTORY_SPEC.md."""

from __future__ import annotations

import struct

# Factory data window (bank 2 sector 13, the identity sector).
IDENTITY_BASE = 0x0810_4000
SYS_ADDRESS = 0x0810_5000
CAL_ADDRESS = 0x0810_6000
DIAG_ADDRESS = 0x0810_7000
WINDOW_BASE = 0x0810_5000
WINDOW_END = 0x0810_8000
BLOCK_SIZE = 0x1000

FILE_HEADER = struct.Struct("<IHHII12sI")
FILE_HEADER_SIZE = FILE_HEADER.size  # 32
MAX_FILE_SIZE = BLOCK_SIZE
FILE_FORMAT = 1

MAGIC_SYS = 0x3153_5953  # "SYS1"
MAGIC_CAL = 0x314C_4143  # "CAL1"
MAGIC_DIAG = 0x4741_4944  # "DIAG"

KIND_MAGIC = {"sys": MAGIC_SYS, "cal": MAGIC_CAL, "diag": MAGIC_DIAG}
MAGIC_KIND = {value: key for key, value in KIND_MAGIC.items()}
KIND_ADDRESS = {"sys": SYS_ADDRESS, "cal": CAL_ADDRESS, "diag": DIAG_ADDRESS}
KIND_SUFFIX = {"sys": ".sys", "cal": ".cal", "diag": ".diag"}

# .sys TLV types
SYS_SERIAL = 0x01
SYS_PRODUCT = 0x02
SYS_HW_VERSION = 0x03
SYS_FW_VERSION = 0x04
SYS_BATCH = 0x05
SYS_MFG_DATE = 0x06
SYS_MAC = 0x07
SYS_NOTES = 0x08
SYS_NAMES = {
    SYS_SERIAL: "serial",
    SYS_PRODUCT: "product",
    SYS_HW_VERSION: "hw_version",
    SYS_FW_VERSION: "fw_version",
    SYS_BATCH: "batch",
    SYS_MFG_DATE: "mfg_date",
    SYS_MAC: "mac",
    SYS_NOTES: "notes",
}

# .cal TLV types
CAL_CHANNEL = 0x01
CAL_TEMP_REF = 0x02
CAL_DATE = 0x03
CAL_OPERATOR = 0x04

# .diag TLV types
DIAG_FAULT_MAP = 0x01
DIAG_COUNTER = 0x02
DIAG_LOG_CFG = 0x03

# Factory protocol
PROTO_FACTORY = 0x46
CMD_INFO = 0x01
CMD_READ = 0x02
CMD_WRITE_BEGIN = 0x03
CMD_WRITE_DATA = 0x04
CMD_WRITE_END = 0x05
CMD_DIAG_INIT = 0x06
CMD_SELFTEST = 0x07
REPLY_FLAG = 0x80
MAX_CHUNK = 512

STATUS_NAMES = {
    0: "OK",
    1: "BAD_COMMAND",
    2: "BAD_RANGE",
    3: "BAD_HEADER",
    4: "BAD_CRC",
    5: "BAD_UID",
    6: "FLASH_ERROR",
    7: "SEQUENCE",
}

# SELFTEST check ids and results (factory-firmware/docs/FACTORY_SPEC.md section 5.1)
CHECK_DEVICE_ID = 1
CHECK_FLASH_SIZE = 2
CHECK_CLOCK = 3
CHECK_RAM = 4
CHECK_CRC = 5
CHECK_FLASH_READ = 6
CHECK_OPTION_BYTES = 7
CHECK_NAMES = {
    CHECK_DEVICE_ID: "Chip ID",
    CHECK_FLASH_SIZE: "Flash size",
    CHECK_CLOCK: "System clock",
    CHECK_RAM: "RAM",
    CHECK_CRC: "CRC unit",
    CHECK_FLASH_READ: "Identity sector read",
    CHECK_OPTION_BYTES: "Option bytes",
}
RESULT_PASS = 0
RESULT_FAIL = 1
RESULT_SKIP = 2

DEVICE_ID = 0x451
FLASH_SIZE_KB = 2048
CORE_CLOCK_HZ = 216_000_000

FACTORY_FIRMWARE_NAME = "soccentric-factory-firmware"

CSV_COLUMNS = (
    "id",
    "serial",
    "product",
    "hw_version",
    "fw_version",
    "batch",
    "mfg_date",
    "mac",
    "notes",
    "status",
)
