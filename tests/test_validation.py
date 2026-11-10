"""Validation checks against the simulated board and the real C self-test."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from hwval_factory.bench import files, spec, validation
from hwval_factory.bench.factorylink import FactoryLink
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


def make_link(device: SimFactoryDevice) -> FactoryLink:
    return FactoryLink(SimFactoryTransport(device), timeout=0.2, retries=1)


def provisioned() -> tuple[FactoryLink, SimFactoryDevice]:
    device = SimFactoryDevice()
    link = make_link(device)
    link.write_file(spec.SYS_ADDRESS, files.build_sys(ROW))
    link.write_file(spec.CAL_ADDRESS, files.build_cal([(0, 1.0, 0.0)]))
    link.diag_init()
    return link, device


def status_of(report: validation.ValidationReport, name: str) -> str:
    return next(check.status for check in report.checks if check.name == name)


def test_a_provisioned_board_passes_everything() -> None:
    link, _device = provisioned()
    report = validation.run_validation(link)
    assert report.verdict == validation.PASS, report.text()
    assert report.count(validation.FAIL) == 0
    peripherals = [c for c in report.checks if c.group == validation.PERIPHERALS]
    assert len(peripherals) == len(spec.CHECK_NAMES)
    assert all(check.status == validation.PASS for check in peripherals)


def test_blank_blocks_are_incomplete_not_failed() -> None:
    report = validation.run_validation(make_link(SimFactoryDevice()))
    assert report.verdict == validation.WARN
    assert status_of(report, "Factory data (.sys)") == validation.WARN
    assert report.count(validation.FAIL) == 0


def test_a_wrong_chip_in_a_bound_file_fails() -> None:
    device = SimFactoryDevice()
    link = make_link(device)
    link.write_file(spec.SYS_ADDRESS, files.build_sys(ROW, uid=device.uid))
    device.uid = bytes(reversed(device.uid))  # the same flash now sits behind another chip
    report = validation.run_validation(make_link(device))
    assert status_of(report, "Factory data (.sys)") == validation.FAIL
    assert report.verdict == validation.FAIL


def test_a_damaged_block_fails_with_the_reason() -> None:
    link, device = provisioned()
    device.flash[spec.CAL_ADDRESS - spec.IDENTITY_BASE + 40] ^= 0xFF  # payload byte
    report = validation.run_validation(link)
    check = next(c for c in report.checks if c.name == "Factory data (.cal)")
    assert check.status == validation.FAIL
    assert "CRC" in check.detail


def test_sys_without_a_serial_fails() -> None:
    device = SimFactoryDevice()
    link = make_link(device)
    blob = files.build_file("sys", files.tlv(spec.SYS_NOTES, b"hello"))
    link.write_file(spec.SYS_ADDRESS, blob)
    report = validation.run_validation(link)
    check = next(c for c in report.checks if c.name == "Factory data (.sys)")
    assert check.status == validation.FAIL
    assert "serial" in check.detail


@pytest.mark.parametrize(
    ("fault", "check"),
    [
        ({"idcode": 0x1000_0450}, "Chip ID"),
        ({"flash_kb": 1024}, "Flash size"),
        ({"clock_hz": 168_000_000}, "System clock"),
        ({"ram_fault": True}, "RAM"),
        ({"optcr": 0x2FFF_AAE4}, "Option bytes"),
        ({"optcr": 0x0FFF_CCE4}, "Option bytes"),
    ],
)
def test_each_peripheral_fault_is_reported(fault: dict[str, object], check: str) -> None:
    link, device = provisioned()
    for name, value in fault.items():
        setattr(device, name, value)
    report = validation.run_validation(link)
    assert status_of(report, check) == validation.FAIL
    assert report.verdict == validation.FAIL
    others = [c for c in report.checks if c.group == validation.PERIPHERALS and c.name != check]
    assert all(c.status == validation.PASS for c in others)


def test_firmware_without_selftest_is_reported() -> None:
    link, device = provisioned()
    original = device.handle

    def old_firmware(request: bytes) -> bytes:
        if request and request[0] == spec.CMD_SELFTEST:
            return bytes([request[0] | spec.REPLY_FLAG, 1])  # BAD_COMMAND
        return original(request)

    device.handle = old_firmware  # type: ignore[method-assign]
    report = validation.run_validation(link)
    check = next(c for c in report.checks if c.name == "Self-test")
    assert check.status == validation.FAIL
    assert "newer" in check.detail


def test_other_firmware_stops_early_and_skips_the_rest() -> None:
    device = SimFactoryDevice(name="some-other-app")
    report = validation.run_validation(make_link(device))
    assert status_of(report, "Factory firmware") == validation.FAIL
    assert report.count(validation.SKIP) == 2


def test_report_text_lists_every_check() -> None:
    link, _device = provisioned()
    report = validation.run_validation(link)
    text = report.text("Board UID 0102")
    assert "Verdict: PASS" in text
    assert text.count("[PASS]") == len(report.checks)
    assert "Peripherals" in text and "Contents" in text


def test_validation_never_writes_to_the_board() -> None:
    link, device = provisioned()
    before = bytes(device.flash)
    validation.run_validation(link)
    assert bytes(device.flash) == before


# -- the Python model matches the real firmware self-test -------------------

FIRMWARE = Path(__file__).resolve().parents[2] / "factory-firmware"


@pytest.fixture(scope="module")
def c_library(tmp_path_factory: pytest.TempPathFactory):
    if shutil.which("cc") is None or not (FIRMWARE / "tests" / "host" / "factory_sim.c").is_file():
        pytest.skip("factory-firmware checkout or C compiler not available")
    library = tmp_path_factory.mktemp("cselftest") / "libfactory_sim.so"
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
    if not hasattr(lib, "sim_set_hardware"):
        pytest.skip("the firmware checkout has no SELFTEST yet")
    lib.sim_set_hardware.argtypes = [ctypes.c_uint32] * 4
    return lib


def c_reply(lib: ctypes.CDLL, request: bytes) -> bytes:
    out = ctypes.create_string_buffer(1024)
    length = lib.sim_handle(request, len(request), out, len(out))
    return out.raw[:length]


@pytest.mark.parametrize(
    "hardware",
    [
        (0x1000_6451, 2048, 216_000_000, 0x0FFF_AAE4),
        (0x1000_6450, 2048, 216_000_000, 0x0FFF_AAE4),
        (0x1000_6451, 1024, 216_000_000, 0x0FFF_AAE4),
        (0x1000_6451, 2048, 168_000_000, 0x0FFF_AAE4),
        (0x1000_6451, 2048, 216_000_000, 0x2FFF_AAE4),
        (0x1000_6451, 2048, 216_000_000, 0x0FFF_CCE4),
    ],
)
def test_python_selftest_matches_the_c_firmware(c_library, hardware: tuple[int, ...]) -> None:
    c_library.sim_reset()
    c_library.sim_set_hardware(*hardware)
    device = SimFactoryDevice()
    device.idcode, device.flash_kb, device.clock_hz, device.optcr = hardware
    request = bytes([spec.CMD_SELFTEST])
    assert device.handle(request) == c_reply(c_library, request)
