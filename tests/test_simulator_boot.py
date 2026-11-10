"""Simulated BOOT security checks (P0-03 / section 8.2)."""

from __future__ import annotations

import struct
import zlib

from cryptography.hazmat.primitives.asymmetric import ec

from hostcore import layout
from hostcore.images import build_image
from hostcore.protocol import codec
from hostcore.simulator_device import SimulatedDevice


def _payload() -> bytes:
    payload = bytearray(256)
    struct.pack_into("<I", payload, 0, 0x20020000)
    struct.pack_into("<I", payload, 4, layout.SLOT_A_BASE + 1)
    return bytes(payload)


def _begin(device: SimulatedDevice, blob: bytes) -> tuple[str, dict]:
    header = blob[: layout.IMAGE_HEADER_SIZE]
    fields = {
        "magic": struct.unpack_from("<I", header, 0)[0],
        "header_version": struct.unpack_from("<H", header, 4)[0],
        "header_size": struct.unpack_from("<H", header, 6)[0],
        "image_type": struct.unpack_from("<I", header, 8)[0],
        "version": struct.unpack_from("<I", header, 12)[0],
        "image_size": struct.unpack_from("<I", header, 16)[0],
        "image_crc32": struct.unpack_from("<I", header, 20)[0],
        "load_address": struct.unpack_from("<I", header, 24)[0],
        "entry_address": struct.unpack_from("<I", header, 28)[0],
        "build_timestamp": struct.unpack_from("<I", header, 32)[0],
        "git_commit": header[36:56],
        "name": header[56:88].rstrip(b"\0").decode("ascii", "replace"),
        "header_crc32": struct.unpack_from("<I", header, 252)[0],
        "extension": header[88:252],
    }
    seq = getattr(device, "_test_seq", 0) + 1
    device._test_seq = seq
    replies = device.handle_frame(_frame(codec.build_update_begin(fields), seq))
    body = codec.body_table_from_payload(_frame_payload(replies[0]))
    if isinstance(body, codec.Nak.Nak):
        parsed = codec.parse_nak(body)
        return parsed["error_name"], parsed
    return "ACK", {}


def _frame(payload: bytes, seq: int = 1) -> bytes:
    from hostcore.protocol.link import build_raw

    return build_raw(payload, seq, 0)


def _frame_payload(frame: bytes) -> bytes:
    from hostcore.protocol.link import decode_raw

    return decode_raw(frame).payload


def test_simulator_refuses_unsigned_and_wrong_key() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())
    signed = build_image(_payload(), version=0x10000, name="app", signing_key=key)
    unsigned = build_image(_payload(), version=0x10000, name="app")
    device = SimulatedDevice("boot")
    device.signing_public_key = key.public_key()
    device.signed_images = True
    assert _begin(device, signed)[0] == "ACK"
    device.update.active = False
    assert _begin(device, unsigned)[0] == "AUTH_FAILED"
    device.signing_public_key = other.public_key()
    assert _begin(device, signed)[0] == "AUTH_FAILED"


def test_simulator_refuses_a_counter_below_the_floor() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    device = SimulatedDevice("boot")
    device.signing_public_key = key.public_key()
    device.security_floor = 5
    lower = build_image(
        _payload(), version=0x10000, name="app", signing_key=key, security_counter=4
    )
    name, nak = _begin(device, lower)
    assert name == "ROLLBACK"
    assert "floor 5" in nak["detail"]
    higher = build_image(
        _payload(), version=0x10000, name="app", signing_key=key, security_counter=5
    )
    assert _begin(device, higher)[0] == "ACK"


def test_simulator_rejects_header_crc_damage() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    blob = bytearray(build_image(_payload(), version=0x10000, name="app", signing_key=key))
    struct.pack_into("<I", blob, 252, zlib.crc32(blob[:252]) & 0xFFFFFFFF ^ 0xFFFF)
    device = SimulatedDevice("boot")
    assert _begin(device, bytes(blob))[0] == "BAD_HEADER"
