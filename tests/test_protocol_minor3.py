"""Protocol minor 3 round-trips (P0-03)."""

from __future__ import annotations

from hostcore import layout
from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.Body import Body
from hostcore.protocol.generated.hwlink.UpdateBegin import UpdateBegin
from hostcore.protocol.link import PROTOCOL_MINOR, error_name
from hostcore.protocol import codec as codec_module


def _header(**overrides):
    header = {
        "magic": layout.IMAGE_MAGIC,
        "header_version": 2,
        "header_size": 256,
        "image_type": layout.IMAGE_TYPE_APPLICATION,
        "version": 0x01020003,
        "image_size": 4096,
        "image_crc32": 0x1234,
        "load_address": layout.SLOT_A_BASE,
        "entry_address": layout.SLOT_A_BASE + 0x1D5,
        "build_timestamp": 1793830667,
        "git_commit": b"ab" * 20,
        "name": "app",
        "header_crc32": 0xDEADBEEF,
        "extension": bytes(range(164)),
    }
    header.update(overrides)
    return header


def test_image_header_extension_and_force_round_trip() -> None:
    header = _header()
    payload = codec.build_update_begin(header, force=True)
    assert codec.body_type(payload) == Body.UpdateBegin
    table = codec.body_table_from_payload(payload)
    assert isinstance(table, UpdateBegin)
    assert table.Force()
    decoded = codec.parse_image_header(table.Header())
    assert decoded["header_version"] == 2
    assert decoded["header_crc32"] == 0xDEADBEEF
    assert decoded["extension"] == header["extension"]
    assert decoded["git_commit"] == b"ab" * 20


def test_update_begin_defaults_force_to_false() -> None:
    table = codec.body_table_from_payload(codec.build_update_begin(_header()))
    assert not table.Force()


def test_boot_info_minor_3_fields_round_trip() -> None:
    payload = codec.build_boot_info(
        {
            "active_slot": 1,
            "last_outcome": 1,
            "rejected_slot": 0,
            "flash_mode": 1,
            "active_valid": True,
            "target_slot": 2,
            "boot_version": "0.1.0",
            "boot_build": "debug-uart-on",
            "signed_images": True,
            "key_id": layout.DEVELOPMENT_KEY_ID,
            "security_floor": 4,
            "slots": [
                {
                    "slot": 1,
                    "present": True,
                    "valid": True,
                    "confirmed": True,
                    "sequence": 2,
                    "security_counter": 4,
                    "detail": "confirmed",
                }
            ],
        }
    )
    parsed = codec.parse_boot_info(codec.body_table_from_payload(payload))
    assert parsed["active_valid"] is True
    assert parsed["target_slot"] == 2
    assert parsed["boot_version"] == "0.1.0"
    assert parsed["boot_build"] == "debug-uart-on"
    assert parsed["signed_images"] is True
    assert parsed["key_id"] == layout.DEVELOPMENT_KEY_ID
    assert parsed["security_floor"] == 4
    assert parsed["slots"][0]["security_counter"] == 4


def test_error_names_cover_minor_3() -> None:
    assert error_name(14) == "AUTH_FAILED"
    assert error_name(15) == "ROLLBACK"
    assert PROTOCOL_MINOR == 3


def test_hello_announces_minor_3() -> None:
    payload = codec_module.build_hello("host", "0.1.0")
    hello = codec.body_table_from_payload(payload)
    assert int(hello.ProtocolMinor()) == 3
