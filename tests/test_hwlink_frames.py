from __future__ import annotations

import struct
import zlib

import pytest

from hostcore.protocol import codec
from hostcore.protocol.link import (
    FrameParser,
    ProtocolError,
    build_raw,
    cobs_decode,
    cobs_encode,
    decode_raw,
)


def test_cobs_round_trip_with_zeros() -> None:
    raw = bytes([0, 1, 0, 2, 3, 0, 0, 255, 0])
    encoded = cobs_encode(raw)
    assert encoded.endswith(b"\x00")
    assert cobs_decode(encoded[:-1]) == raw


def test_hwlink_frame_header_and_crc() -> None:
    payload = b"HWLK-test"
    frame = build_raw(payload, seq=7, flags=0)
    assert frame[:4] == struct.pack("<BBH", 1, 0, 7)
    assert struct.unpack("<I", frame[-4:])[0] == zlib.crc32(frame[:-4]) & 0xFFFFFFFF
    decoded = decode_raw(frame)
    assert decoded.payload == payload
    assert decoded.seq == 7


def test_frame_parser_resynchronises_after_corruption() -> None:
    good = cobs_encode(build_raw(b"hello", 1, 0))
    corrupt = cobs_encode(build_raw(b"bad", 2, 0))[:-5] + b"\xff\x00"
    parser = FrameParser()
    frames = parser.feed(corrupt + good)
    assert [frame.seq for frame in frames] == [1]


def test_bad_crc_is_rejected() -> None:
    frame = bytearray(build_raw(b"payload", 3, 0))
    frame[-1] ^= 0xFF
    with pytest.raises(ProtocolError):
        decode_raw(bytes(frame))


def test_flatbuffer_hello_round_trip() -> None:
    payload = codec.build_hello("pytest", "0.1", 1)
    assert codec.verify(payload)
    assert codec.body_type(payload) == codec.Body.Hello
    table = codec.body_table_from_payload(payload)
    assert codec.text(table.HostName()) == "pytest"
