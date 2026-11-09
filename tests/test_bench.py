"""Tests for the bench workflow: file formats, CSV database, UART client, simulator."""

from __future__ import annotations

import ctypes
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from hwval_factory.bench import csvdb, detect, files, spec
from hwval_factory.bench.factorylink import FactoryLink, FactoryLinkError, FactoryStatusError
from hwval_factory.bench.sim import SimFactoryDevice, SimFactoryTransport

ROW = {
    "id": "SC0001",
    "serial": "SC767-2611-00001",
    "product": "SC767",
    "hw_version": "B",
    "fw_version": "0.1.0",
    "batch": "2611A",
    "mfg_date": "2026-11-09",
    "mac": "02:00:00:00:00:01",
    "notes": "",
    "status": "new",
}


def make_link(device: SimFactoryDevice | None = None) -> tuple[FactoryLink, SimFactoryDevice]:
    device = device or SimFactoryDevice()
    return FactoryLink(SimFactoryTransport(device), timeout=0.2, retries=1), device


# -- file formats ---------------------------------------------------------


def test_sys_round_trip() -> None:
    blob = files.build_sys(ROW)
    parsed = files.parse_file(blob, "sys")
    described = files.describe(parsed)
    assert described["serial"] == "SC767-2611-00001"
    assert described["mac"] == "02:00:00:00:00:01"
    assert parsed.kind == "sys" and not parsed.bound


def test_sys_requires_serial() -> None:
    with pytest.raises(files.FileFormatError):
        files.build_sys({**ROW, "serial": ""})


def test_sys_rejects_bad_mac() -> None:
    with pytest.raises(files.FileFormatError):
        files.build_sys({**ROW, "mac": "02:00:00"})


def test_cal_and_diag_round_trip() -> None:
    cal = files.parse_file(
        files.build_cal([(0, 1.0, 0.5), (1, 2.0, -1.0)], temp_ref=25.0, date="2026-11-09"), "cal"
    )
    assert files.describe(cal)["channel 1"] == "gain 2, offset -1"
    diag = files.parse_file(files.build_diag([(0x10, 2, 1, "overtemp")], [(1, 0)]), "diag")
    assert "fault 0x0010" in files.describe(diag)


@pytest.mark.parametrize("damage", ["magic", "header", "payload", "length", "short"])
def test_parse_rejects_damaged_files(damage: str) -> None:
    blob = bytearray(files.build_sys(ROW))
    if damage == "magic":
        blob[0] ^= 0xFF
    elif damage == "header":
        blob[6] ^= 0x01
    elif damage == "payload":
        blob[-1] ^= 0x01
    elif damage == "length":
        blob += b"\x00"
    else:
        blob = blob[:10]
    with pytest.raises(files.FileFormatError):
        files.parse_file(bytes(blob))


def test_parse_checks_expected_kind() -> None:
    with pytest.raises(files.FileFormatError, match=r"expected a \.cal"):
        files.parse_file(files.build_sys(ROW), "cal")


def test_file_size_limit() -> None:
    with pytest.raises(files.FileFormatError):
        files.build_file("sys", bytes(spec.MAX_FILE_SIZE))


# -- CSV database -----------------------------------------------------------


def test_csv_search_and_sys(tmp_path: Path) -> None:
    path = csvdb.write_sample(tmp_path / "factory.csv", 5)
    db = csvdb.FactoryCsv(path)
    assert db.get("sc0003")["serial"].endswith("00003")
    assert [row["id"] for row in db.search("SC000")] == [
        "SC0001",
        "SC0002",
        "SC0003",
        "SC0004",
        "SC0005",
    ]
    assert [row["id"] for row in db.search("00004")] == ["SC0004"]
    parsed = files.parse_file(db.sys_file("SC0002"), "sys")
    assert files.describe(parsed)["serial"] == "SC767-2611-00002"


def test_csv_mark_programmed_persists(tmp_path: Path) -> None:
    path = csvdb.write_sample(tmp_path / "factory.csv", 2)
    db = csvdb.FactoryCsv(path)
    db.mark_programmed("SC0001")
    again = csvdb.FactoryCsv(path)
    assert again.get("SC0001")["status"] == "programmed"
    assert again.get("SC0002")["status"] == "new"


def test_csv_rejects_duplicates_and_missing_columns(tmp_path: Path) -> None:
    bad = tmp_path / "dup.csv"
    bad.write_text("id,serial\nA,1\nA,2\n")
    with pytest.raises(csvdb.FactoryDbError, match="duplicate"):
        csvdb.FactoryCsv(bad)
    bad.write_text("name\nx\n")
    with pytest.raises(csvdb.FactoryDbError, match="missing columns"):
        csvdb.FactoryCsv(bad)
    with pytest.raises(csvdb.FactoryDbError):
        csvdb.FactoryCsv(tmp_path / "missing.csv")


def test_csv_unknown_id(tmp_path: Path) -> None:
    db = csvdb.FactoryCsv(csvdb.write_sample(tmp_path / "factory.csv", 1))
    with pytest.raises(csvdb.FactoryDbError):
        db.sys_file("NOPE")


# -- UART client against the simulator -------------------------------------


def test_info() -> None:
    link, device = make_link()
    info = link.info()
    assert info.is_factory_firmware and info.version == "0.1.0" and info.uid == device.uid


def test_write_and_read_each_file() -> None:
    link, _ = make_link()
    for kind, blob in (
        ("sys", files.build_sys(ROW)),
        ("cal", files.build_cal([(0, 1.0, 0.0)], date="2026-11-09")),
        ("diag", files.build_diag(counters=[(1, 0)])),
    ):
        link.write_file(spec.KIND_ADDRESS[kind], blob)
        stored = link.read_file(spec.KIND_ADDRESS[kind])
        assert stored is not None and stored.kind == kind
    assert link.read_file(spec.CAL_ADDRESS).items[0][0] == spec.CAL_CHANNEL


def test_blocks_are_independent() -> None:
    link, _ = make_link()
    link.write_file(spec.SYS_ADDRESS, files.build_sys(ROW))
    link.write_file(spec.CAL_ADDRESS, files.build_cal([(0, 1.0, 0.0)]))
    assert files.describe(link.read_file(spec.SYS_ADDRESS))["serial"] == ROW["serial"]


def test_rewrite_with_shorter_file_clears_the_tail() -> None:
    link, device = make_link()
    link.write_file(spec.SYS_ADDRESS, files.build_sys({**ROW, "notes": "x" * 500}))
    short = files.build_sys(ROW)
    link.write_file(spec.SYS_ADDRESS, short)
    start = spec.SYS_ADDRESS - spec.IDENTITY_BASE
    tail = bytes(device.flash[start + len(short) : start + spec.BLOCK_SIZE])
    assert tail == b"\xff" * len(tail)


def test_identity_record_is_never_written() -> None:
    link, device = make_link()
    device.flash[:16] = b"PRV1-IDENTITY..."
    with pytest.raises(FactoryStatusError) as exc:
        link.write_file(spec.IDENTITY_BASE, files.build_sys(ROW))
    assert exc.value.status == 2
    assert bytes(device.flash[:16]) == b"PRV1-IDENTITY..."
    assert link.read(spec.IDENTITY_BASE, 16) == b"PRV1-IDENTITY..."


def test_device_refuses_a_file_bound_to_another_chip() -> None:
    link, _ = make_link()
    other = files.build_sys(ROW, uid=bytes(12) + b"")
    bound = files.build_sys(ROW, uid=bytes([9]) * 12)
    link.write_file(spec.SYS_ADDRESS, other)
    with pytest.raises(FactoryStatusError) as exc:
        link.write_file(spec.SYS_ADDRESS, bound)
    assert exc.value.status == 5


def test_device_accepts_a_file_bound_to_this_chip() -> None:
    link, device = make_link()
    link.write_file(spec.SYS_ADDRESS, files.build_sys(ROW, uid=device.uid))


def test_flash_error_is_reported_and_retry_works() -> None:
    link, device = make_link()
    device.fail_next_write = True
    with pytest.raises(FactoryStatusError) as exc:
        link.write_file(spec.SYS_ADDRESS, files.build_sys(ROW))
    assert exc.value.status == 6
    link.write_file(spec.SYS_ADDRESS, files.build_sys(ROW))


def test_corrupt_file_is_refused_by_the_device() -> None:
    link, _ = make_link()
    blob = bytearray(files.build_sys(ROW))
    blob[-1] ^= 0xFF
    with pytest.raises(FactoryStatusError) as exc:
        link.write_file(spec.SYS_ADDRESS, bytes(blob))
    assert exc.value.status == 4


def test_diag_init_stores_a_clean_map() -> None:
    link, _ = make_link()
    stored = link.diag_init()
    parsed = files.parse_file(stored, "diag")
    assert files.describe(parsed) == {"log": "depth 64, flags 0x0000"}
    with pytest.raises(FactoryStatusError):
        link.diag_init(spec.IDENTITY_BASE)


def test_no_answer_is_a_clear_error() -> None:
    class Silent(SimFactoryTransport):
        def read_frame(self, timeout: float) -> bytes | None:
            return None

    link = FactoryLink(Silent(SimFactoryDevice()), timeout=0.05, retries=2)
    with pytest.raises(FactoryLinkError, match="did not answer"):
        link.info()


def test_blank_block_reads_as_none() -> None:
    link, _ = make_link()
    assert link.read_file(spec.CAL_ADDRESS) is None


# -- detection ---------------------------------------------------------------


def test_detect_never_raises_without_hardware() -> None:
    result = detect.detect()
    assert isinstance(result.probes, list) and isinstance(result.ports, list)


def test_sysfs_probe_scan(tmp_path: Path) -> None:
    usb = tmp_path / "1-1"
    usb.mkdir()
    (usb / "idVendor").write_text("0483\n")
    (usb / "idProduct").write_text("374b\n")
    (usb / "serial").write_text("066DFF123\n")
    (usb / "product").write_text("STM32 STLink\n")
    other = tmp_path / "1-2"
    other.mkdir()
    (other / "idVendor").write_text("1d6b\n")
    (other / "idProduct").write_text("0002\n")
    probes = detect._sysfs_probes(str(tmp_path))
    assert [(p.serial, p.product_id) for p in probes] == [("066DFF123", 0x374B)]


# -- the simulator matches the real firmware handler ------------------------

FIRMWARE = Path(__file__).resolve().parents[2] / "factory-firmware"


@pytest.fixture(scope="module")
def c_handler(tmp_path_factory: pytest.TempPathFactory):
    if shutil.which("cc") is None or not (FIRMWARE / "tests" / "host" / "Makefile").is_file():
        pytest.skip("factory-firmware checkout or C compiler not available")
    work = tmp_path_factory.mktemp("cfirmware")
    library = work / "libfactory_sim.so"
    result = subprocess.run(
        [
            "cc",
            "-O1",
            "-shared",
            "-fPIC",
            "-o",
            str(library),
            str(FIRMWARE / "tests/host/factory_sim.c"),
            str(FIRMWARE / "src/factory_proto.c"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot build the C handler: {result.stderr[:200]}")
    lib = ctypes.CDLL(str(library))
    lib.sim_handle.restype = ctypes.c_size_t
    lib.sim_handle.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
    lib.sim_reset()

    def call(request: bytes) -> bytes:
        out = ctypes.create_string_buffer(1024)
        length = lib.sim_handle(request, len(request), out, len(out))
        return out.raw[:length]

    return call, lib


def test_python_model_matches_the_c_handler(c_handler) -> None:
    call, lib = c_handler
    lib.sim_reset()
    device = SimFactoryDevice(name="soccentric-factory-firmware", version="0.1.0", build="host-sim")
    sys_blob = files.build_sys(ROW)
    cal_blob = files.build_cal([(0, 1.0, 0.0)])
    bad_payload = bytearray(sys_blob)
    bad_payload[-1] ^= 1

    def begin(address: int, blob: bytes, crc: int | None = None) -> bytes:
        return bytes([spec.CMD_WRITE_BEGIN]) + struct.pack(
            "<IHI", address, len(blob), files.crc32(blob) if crc is None else crc
        )

    def data(offset: int, chunk: bytes) -> bytes:
        return bytes([spec.CMD_WRITE_DATA]) + struct.pack("<H", offset) + chunk

    script = [
        bytes([spec.CMD_INFO]),
        begin(spec.SYS_ADDRESS, sys_blob),
        data(0, sys_blob),
        bytes([spec.CMD_WRITE_END]),
        bytes([spec.CMD_READ]) + struct.pack("<IH", spec.SYS_ADDRESS, len(sys_blob)),
        begin(spec.CAL_ADDRESS, cal_blob),
        data(5, cal_blob[5:]),  # sequence error
        data(0, cal_blob),
        bytes([spec.CMD_WRITE_END]),
        begin(spec.IDENTITY_BASE, sys_blob),  # out of window
        begin(spec.SYS_ADDRESS + 0x100, sys_blob),  # unaligned
        begin(spec.DIAG_ADDRESS, bytes(bad_payload)),
        data(0, bytes(bad_payload)),
        bytes([spec.CMD_WRITE_END]),  # payload CRC
        begin(spec.DIAG_ADDRESS, sys_blob, crc=1234),
        data(0, sys_blob),
        bytes([spec.CMD_WRITE_END]),  # declared CRC
        bytes([spec.CMD_DIAG_INIT]) + struct.pack("<I", spec.DIAG_ADDRESS),
        bytes([spec.CMD_READ]) + struct.pack("<IH", spec.DIAG_ADDRESS, 48),
        bytes([spec.CMD_DIAG_INIT]) + struct.pack("<I", spec.DIAG_ADDRESS + 1),
        bytes([spec.CMD_READ]) + struct.pack("<IH", 0, 4),
        bytes([0x7F]),
    ]
    for request in script:
        assert device.handle(request) == call(request), request[:8].hex()
