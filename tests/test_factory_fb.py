"""Cross-language tests for the FACTORY FlatBuffers messages.

Python builds every request with the generated accessors; the real firmware
handler (factory_fb.c + factory_proto.c) decodes it against the simulated
flash and builds the reply.  Python decodes that reply.  A bad vtable or an
off-by-one in either encoder fails here before hardware is involved.
"""

from __future__ import annotations

import ctypes
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.Body import Body
from hwval_factory.bench import files, spec

FIRMWARE = Path(__file__).resolve().parents[2] / "factory-firmware"


@pytest.fixture(scope="module")
def c_factory(tmp_path_factory: pytest.TempPathFactory):
    if shutil.which("cc") is None or not (FIRMWARE / "src/protocol/factory_fb.c").is_file():
        pytest.skip("factory-firmware checkout or C compiler not available")
    work = tmp_path_factory.mktemp("cfactory")
    library = work / "libfactory_fb.so"
    result = subprocess.run(
        [
            "cc",
            "-O0",
            "-shared",
            "-fPIC",
            "-I",
            str(FIRMWARE / "src/protocol"),
            "-I",
            str(FIRMWARE / "src"),
            "-o",
            str(library),
            str(FIRMWARE / "tests/host/factory_sim.c"),
            str(FIRMWARE / "src/protocol/factory_proto.c"),
            str(FIRMWARE / "src/protocol/factory_fb.c"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot build the C factory handler: {result.stderr[:300]}")
    lib = ctypes.CDLL(str(library))
    lib.sim_fb_handle.restype = ctypes.c_size_t
    lib.sim_fb_handle.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_uint16,
    ]
    lib.sim_reset()

    def call(request: bytes, seq: int = 1) -> tuple[int, object]:
        out = ctypes.create_string_buffer(4096)
        length = lib.sim_fb_handle(request, len(request), out, len(out), seq)
        assert length > 0, "the C handler returned an empty reply"
        payload = out.raw[:length]
        assert codec.verify(payload), "the C reply is not a valid HWLK FlatBuffer"
        return codec.body_type(payload), codec.body_table_from_payload(payload)

    return call, lib


def test_hello_reports_the_factory_role(c_factory) -> None:
    call, _lib = c_factory
    body_type, table = call(codec.build_hello("pytest", "0.1.0"), seq=7)
    assert body_type == Body.HelloReply
    reply = codec.parse_hello_reply(table)
    assert reply["role_name"] == "FACTORY"
    assert reply["firmware_name"] == "soccentric-factory-firmware"
    assert reply["protocol_minor"] == 4
    # HelloReply carries the first 64 UID bits little-endian; the simulator's
    # UID is 01..0C, so the value is 0x0807060504030201.
    assert reply["uid"] == 0x0807060504030201


def test_factory_info_round_trip(c_factory) -> None:
    call, _lib = c_factory
    body_type, table = call(codec.build_get_factory_info())
    assert body_type == Body.FactoryInfo
    info = codec.parse_factory_info(table)
    assert info["firmware_name"] == "soccentric-factory-firmware"
    assert len(info["uid"]) == 12
    assert info["window_base"] == spec.WINDOW_BASE
    assert info["window_end"] == spec.WINDOW_END
    assert info["max_chunk"] == spec.MAX_CHUNK


def test_write_and_read_back_through_c(c_factory) -> None:
    call, lib = c_factory
    lib.sim_reset()
    blob = files.build_sys(
        {
            "serial": "SC767-2611-00001",
            "product": "SC767",
            "hw_version": "B",
            "mac": "02:00:00:00:00:01",
        }
    )

    body_type, table = call(
        codec.build_factory_write_begin(1, spec.SYS_ADDRESS, len(blob), files.crc32(blob))
    )
    assert body_type == Body.Ack
    for offset in range(0, len(blob), spec.MAX_CHUNK):
        body_type, table = call(codec.build_factory_write_data(offset, blob[offset : offset + spec.MAX_CHUNK]))
        assert body_type == Body.Ack
    body_type, table = call(codec.build_factory_write_end())
    assert body_type == Body.FactoryWriteResult
    result = codec.parse_factory_write_result(table)
    assert result["success"], result

    body_type, table = call(codec.build_read_memory(spec.SYS_ADDRESS, len(blob)))
    assert body_type == Body.MemoryData
    data = codec.parse_memory_data(table)
    assert data["address"] == spec.SYS_ADDRESS
    assert data["data"] == blob


def test_c_rejects_out_of_order_and_bad_uid(c_factory) -> None:
    call, lib = c_factory
    lib.sim_reset()
    blob = files.build_sys({"serial": "SC767-2611-00002", "product": "SC767", "hw_version": "B"})
    body_type, _table = call(
        codec.build_factory_write_data(0, blob[: spec.MAX_CHUNK])
    )
    assert body_type == Body.Nak  # no WriteBegin first

    bound = files.build_sys(
        {"serial": "SC767-2611-00003", "product": "SC767", "hw_version": "B"},
        uid=b"\xAA" * 12,
    )
    body_type, _table = call(
        codec.build_factory_write_begin(1, spec.SYS_ADDRESS, len(bound), files.crc32(bound))
    )
    assert body_type == Body.Ack
    for offset in range(0, len(bound), spec.MAX_CHUNK):
        call(codec.build_factory_write_data(offset, bound[offset : offset + spec.MAX_CHUNK]))
    body_type, table = call(codec.build_factory_write_end())
    assert body_type == Body.FactoryWriteResult
    result = codec.parse_factory_write_result(table)
    assert not result["success"]
    assert result["error"] == 17  # BAD_UID


def test_self_test_reply_from_c(c_factory) -> None:
    call, lib = c_factory
    lib.sim_reset()
    body_type, table = call(codec.build_factory_self_test())
    assert body_type == Body.SelfTestResult
    result = codec.parse_self_test_result(table)
    assert len(result["items"]) == 7
    assert all(item["check"] for item in result["items"])
