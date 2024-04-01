"""Golden wire vectors for the FACTORY messages (plan step 1.6).

The request bytes are what the station sends; the replies are what the
simulated firmware sends.  They are kept as hex so a schema or builder change
shows up as a byte diff, and the C host test uses the same request bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.Body import Body
from hwval_factory.bench import files, spec

VECTORS = json.loads((Path(__file__).parent / "golden/factory_vectors.json").read_text())


def _reply(name: str) -> tuple[int, object]:
    payload = bytes.fromhex(VECTORS[name])
    assert codec.verify(payload)
    return codec.body_type(payload), codec.body_table_from_payload(payload)


def test_request_vectors_are_stable() -> None:
    sys_blob = files.build_sys(
        {"serial": "SC767-2611-00001", "product": "SC767", "hw_version": "B"}
    )
    assert codec.build_get_factory_info().hex() == VECTORS["get_factory_info_request"]
    assert codec.build_read_memory(spec.SYS_ADDRESS, 16).hex() == VECTORS["read_memory_request"]
    assert (
        codec.build_factory_write_begin(
            1, spec.SYS_ADDRESS, len(sys_blob), files.crc32(sys_blob)
        ).hex()
        == VECTORS["write_begin_request"]
    )
    assert codec.build_factory_write_data(0, sys_blob[:64]).hex() == VECTORS["write_data_request"]
    assert codec.build_factory_write_end().hex() == VECTORS["write_end_request"]
    assert codec.build_factory_self_test().hex() == VECTORS["self_test_request"]


def test_reply_vectors_decode() -> None:
    body_type, table = _reply("factory_info_reply")
    assert body_type == Body.FactoryInfo
    info = codec.parse_factory_info(table)
    assert info["firmware_name"] == "soccentric-factory-firmware"
    assert info["window_base"] == spec.WINDOW_BASE

    body_type, table = _reply("memory_data_reply")
    assert body_type == Body.MemoryData
    assert codec.parse_memory_data(table)["address"] == spec.SYS_ADDRESS

    body_type, table = _reply("write_result_reply")
    assert body_type == Body.FactoryWriteResult
    assert codec.parse_factory_write_result(table)["success"] is False

    body_type, table = _reply("self_test_result_reply")
    assert body_type == Body.SelfTestResult
    assert len(codec.parse_self_test_result(table)["items"]) == 7
