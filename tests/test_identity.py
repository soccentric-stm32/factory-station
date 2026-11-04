from __future__ import annotations

import pytest

from hostcore import layout
from hwval_factory.identity import (
    ProvisioningData,
    RecordError,
    build_record,
    now_utc,
    parse_record,
)
from hwval_factory.profile import ProfileError
from hwval_factory.protection import ProtectionSpec


def _data(**overrides):
    base = dict(
        serial_number="SC767-2645-000001-0",
        product_id="sc767",
        hw_revision="B",
        manufacturing_time=now_utc(),
        station_fixture="STN-01/F1",
        profile="sc767@1.0.0",
        firmware_version=0x00010000,
        uid=bytes.fromhex("002A004B3337500D37323336"),
        mac=bytes.fromhex("020000000001"),
        calibration={0x20: (3265.0, "mV")},
        customer={0x40: "SoCcentric"},
        custom={"line": "L1"},
        revision=3,
    )
    base.update(overrides)
    return ProvisioningData(**base)


def test_identity_round_trip_and_uid_binding() -> None:
    blob = build_record(_data())
    parsed = parse_record(blob, expect_uid=bytes.fromhex("002A004B3337500D37323336"))
    assert parsed.serial_number == "SC767-2645-000001-0"
    assert parsed.mac == "02:00:00:00:00:01"
    assert parsed.revision == 3
    assert parsed.calibration_value(0x20) == (3265.0, "mV")
    assert parsed.custom_value("line") == "L1"


def test_identity_rejects_wrong_uid_and_corruption() -> None:
    blob = build_record(_data())
    with pytest.raises(RecordError, match="UID"):
        parse_record(blob, expect_uid=b"\x00" * 12)
    broken = bytearray(blob)
    broken[-1] ^= 0xFF
    with pytest.raises(RecordError, match="CRC"):
        parse_record(bytes(broken))


def test_identity_magic_matches_contract() -> None:
    assert int.from_bytes(b"PRV1", "little") == 0x31565250
    blob = build_record(_data())
    assert blob[:4] == b"PRV1"


def test_identity_sector_constants() -> None:
    assert layout.IDENTITY_BASE == 0x08104000
    assert layout.IDENTITY_BASE_SWAPPED == 0x08004000


def test_option_bytes_product_configuration() -> None:
    spec = ProtectionSpec(
        ndbank=0,
        ndboot=1,
        bor_lev=3,
        wrp_bank1=[0, 1],
        wrp_bank2=[1],
        rdp=1,
        boot_addr0=0x0080,
        boot_addr1=0x0040,
    )
    spec.validate()
    optcr = spec.computed_optcr()
    assert (optcr >> 29) & 1 == 0          # nDBANK = 0
    assert (optcr >> 28) & 1 == 1          # nDBOOT = 1
    assert (optcr >> 8) & 0xFF == 0x00     # RDP 1
    assert spec.computed_optcr1() == 0x00400080
    with pytest.raises(ProfileError, match="RDP level 2"):
        ProtectionSpec(rdp=2).validate()
