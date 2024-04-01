"""The generated C API, compiled for the host and run over real files.

Author: Sandesh Ghimire <sandesh@soccentric.com>

The Python generators write the three blocks, the C program loads them
through the generated API, edits them in RAM, commits, and the files it wrote
are parsed again by the Python reader: the two implementations of the format
check each other.
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hwval_factory.core import capi, spec
from hwval_factory.core.fileformat import (
    build_cal,
    build_diag,
    build_sys,
    describe,
    parse_file,
)

HERE = Path(__file__).resolve().parent
pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")

UID = bytes.fromhex("0102030405060708090A0B0C")


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    folder = tmp_path_factory.mktemp("capi")
    capi.write(folder)
    exe = folder / "fd_host_test"
    subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", f"-I{folder}",
         str(folder / capi.SOURCE_NAME), str(HERE / "c" / "fd_host_test.c"), "-o", str(exe)],
        check=True, capture_output=True, text=True)
    return exe


def image(sys_uid: bytes | None = None) -> bytes:
    sys_file = build_sys(
        {"serial": "SC0001", "product": "Widget", "hw_version": "B", "mac": "02:00:00:00:00:01"},
        uid=sys_uid)
    cal_file = build_cal(
        [(1, 1.01, 0.5), (2, 0.99, -0.25)], temp_ref=25.0, date="2026-01-02", operator="alice")
    diag_file = build_diag(faults=[(0x100, 2, 1, "Over temp")], counters=[(1, 0)], log_depth=64)
    blocks = []
    for data in (sys_file, cal_file, diag_file):
        blocks.append(data + b"\xff" * (spec.BLOCK_SIZE - len(data)))
    return b"".join(blocks)


def run(binary, tmp_path, data: bytes, *extra: str):
    src, dst = tmp_path / "in.bin", tmp_path / "out.bin"
    src.write_bytes(data)
    done = subprocess.run([str(binary), str(src), str(dst), *extra],
                          capture_output=True, text=True, check=True)
    values = dict(line.split("=", 1) for line in done.stdout.splitlines())
    return values, dst


def block(out: Path, index: int) -> bytes:
    raw = out.read_bytes()[index * spec.BLOCK_SIZE : (index + 1) * spec.BLOCK_SIZE]
    length = struct.unpack_from("<I", raw, 8)[0]
    return raw[: spec.FILE_HEADER_SIZE + length]


def test_c_reads_what_python_wrote(binary, tmp_path):
    v, _ = run(binary, tmp_path, image())
    assert (v["load_sys"], v["load_cal"], v["load_diag"]) == ("0", "0", "0")
    assert v["serial"] == "SC0001" and v["product"] == "Widget"
    assert v["mac"] == "020000000001"
    assert v["notes_absent"] == "1" and v["serial_small_buffer"] == "1"
    assert v["channel2"] == "0.9900,-0.2500" and v["channels"] == "2"
    assert v["temp_ref"] == "25.00" and v["operator"] == "alice"
    assert v["fault"] == "2,1,Over temp" and v["counter1"] == "0"
    assert v["log"] == "64,0"


def test_bad_use_is_refused_and_discard_restores(binary, tmp_path):
    v, _ = run(binary, tmp_path, image())
    assert v["empty_serial_refused"] == v["missing_channel"] == v["missing_remove"] == "1"
    assert v["modified_after_set"] == "1" and v["modified_after_discard"] == "0"
    assert v["serial_after_discard"] == "SC0001"


def test_edits_commit_to_files_python_accepts(binary, tmp_path):
    v, out = run(binary, tmp_path, image())
    assert all(v[k] == "0" for k in v if k.startswith("commit_"))
    assert v["modified_after_commit"] == "0" and v["serial_reloaded"] == "SC9999"

    sys_file = parse_file(block(out, 0), "sys")           # CRCs and header checked
    assert sys_file.revision == 7
    fields = dict(describe(sys_file))
    assert fields["serial"] == "SC9999" and fields["notes"] == "edited on target"
    assert fields["mac"] == "02:11:22:33:44:55" and fields["product"] == "Widget"

    cal = dict(describe(parse_file(block(out, 1), "cal")))
    assert "channel 1" not in cal                          # removed
    assert cal["channel 2"] == "gain 1.25, offset 0.75"    # replaced
    assert cal["channel 3"] == "gain 2, offset -2"         # added
    assert cal["date"] == "2026-11-12" and "operator" not in cal
    assert cal["temperature reference"] == "25 °C"

    diag = dict(describe(parse_file(block(out, 2), "diag")))
    assert diag["fault 0x0100"] == "severity 1, flags 0x01, Over temperature"
    assert diag["fault 0x0200"] == "severity 3, flags 0x00, New fault"
    assert diag["counter 1"] == "initial 77" and diag["counter 2"] == "initial 5"
    assert diag["log"] == "depth 128, flags 0x0001"
    assert v["faults"] == "2" and v["fault_at1"] == "512,New fault"


def test_chip_binding(binary, tmp_path):
    # a file bound to this chip loads, and stays bound after the commit
    v, out = run(binary, tmp_path, image(UID), UID.hex())
    assert v["load_sys"] == "0" and v["commit_sys"] == "0"
    assert parse_file(block(out, 0)).uid == UID
    # a file bound to another chip is refused (FD_ERR_UID = 5)
    other = bytes(range(12, 0, -1)).hex()
    v, _ = run(binary, tmp_path, image(UID), other)
    assert v["load_sys"] == "5"
    # without a chip UID hook the binding cannot be checked, the file loads
    v, _ = run(binary, tmp_path, image(UID))
    assert v["load_sys"] == "0"


def test_corrupt_block_is_refused(binary, tmp_path):
    data = bytearray(image())
    data[spec.FILE_HEADER_SIZE + 5] ^= 0xFF               # a payload byte of .sys
    v, _ = run(binary, tmp_path, bytes(data))
    assert v["load_sys"] == "4"                           # FD_ERR_FORMAT
    assert v["load_cal"] == "0"


def test_blank_flash_is_not_a_valid_block(binary, tmp_path):
    v, _ = run(binary, tmp_path, b"\xff" * (3 * spec.BLOCK_SIZE))
    assert v["load_sys"] == v["load_cal"] == v["load_diag"] == "4"


def test_generated_constants_come_from_the_spec():
    header = capi.header()
    for value in (spec.SYS_ADDRESS, spec.CAL_ADDRESS, spec.DIAG_ADDRESS):
        assert f"0x{value:08X}u" in header
    assert not re.search(r"@[A-Z_]+@", capi.source())     # no placeholder left
    assert "Sandesh Ghimire" in header and "Claude" not in header
