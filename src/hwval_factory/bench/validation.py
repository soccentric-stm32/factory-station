"""Basic board validation: the factory data blocks and the firmware's peripheral self-test.

Everything here only reads from the board.  The contents checks parse what the
identity sector holds; the peripheral checks ask the factory firmware to run
its ``SELFTEST`` command.  A check that cannot run is reported, never skipped
silently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from hwval_factory.bench import files, spec
from hwval_factory.bench.factorylink import (
    FactoryInfo,
    FactoryLink,
    FactoryLinkError,
    FactoryStatusError,
    SelfTestItem,
)

Log = Callable[[str], None]

PASS = "pass"
FAIL = "fail"
WARN = "warn"  # not a fault, but the unit is not finished (for example a block is blank)
SKIP = "skip"

CONTENTS = "Contents"
PERIPHERALS = "Peripherals"


@dataclass(slots=True)
class Check:
    group: str
    name: str
    status: str
    detail: str


@dataclass(slots=True)
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, group: str, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(group, name, status, detail))

    def count(self, status: str) -> int:
        return sum(1 for check in self.checks if check.status == status)

    @property
    def verdict(self) -> str:
        """``fail`` if any check failed, ``warn`` if the unit is incomplete, else ``pass``."""
        if self.count(FAIL):
            return FAIL
        if self.count(WARN):
            return WARN
        return PASS

    def summary(self) -> str:
        parts = [f"{self.count(PASS)} passed"]
        for status, label in ((FAIL, "failed"), (WARN, "incomplete"), (SKIP, "skipped")):
            if self.count(status):
                parts.append(f"{self.count(status)} {label}")
        return ", ".join(parts)

    def text(self, board: str = "") -> str:
        lines = ["HWVAL Factory validation report"]
        if board:
            lines.append(board)
        lines.append(f"Verdict: {self.verdict.upper()} ({self.summary()})")
        group = ""
        for check in self.checks:
            if check.group != group:
                group = check.group
                lines += ["", group]
            lines.append(f"  [{check.status.upper():4}] {check.name}: {check.detail}")
        return "\n".join(lines) + "\n"


def _describe_block(kind: str, parsed: files.FactoryFile) -> tuple[str, str]:
    """Return (status, detail) for the fields a block of ``kind`` must carry."""
    if kind == "sys":
        missing = [
            name
            for type_id, name in (
                (spec.SYS_SERIAL, "serial"),
                (spec.SYS_PRODUCT, "product"),
                (spec.SYS_HW_VERSION, "hw_version"),
            )
            if not (parsed.get(type_id) or b"").strip(b"\x00 ")
        ]
        if missing:
            return FAIL, "missing " + ", ".join(missing)
        serial = (parsed.get(spec.SYS_SERIAL) or b"").decode("ascii", "replace")
        mac = parsed.get(spec.SYS_MAC)
        if mac is not None and len(mac) != 6:
            return FAIL, f"MAC is {len(mac)} bytes, expected 6"
        return PASS, f"serial {serial}"
    if kind == "cal":
        channels = [value for type_id, value in parsed.items if type_id == spec.CAL_CHANNEL]
        if not channels:
            return FAIL, "no calibration channels"
        return PASS, f"{len(channels)} channel(s)"
    known = {spec.DIAG_FAULT_MAP, spec.DIAG_COUNTER, spec.DIAG_LOG_CFG}
    if not any(type_id in known for type_id, _value in parsed.items):
        return FAIL, "no fault map, counters or log configuration"
    return PASS, f"{len(parsed.items)} item(s)"


def _check_block(report: ValidationReport, link: FactoryLink, kind: str, uid: bytes) -> None:
    name = f"Factory data (.{kind})"
    try:
        parsed = link.read_file(spec.KIND_ADDRESS[kind])
    except (files.FileFormatError, FactoryLinkError) as exc:
        report.add(CONTENTS, name, FAIL, str(exc))
        return
    if parsed is None:
        report.add(CONTENTS, name, WARN, "not written yet")
        return
    status, detail = _describe_block(kind, parsed)
    if status == PASS and parsed.bound and parsed.uid != uid:
        status, detail = FAIL, "bound to a different chip"
    elif status == PASS and parsed.bound:
        detail += ", bound to this chip"
    report.add(CONTENTS, name, status, detail)


def _describe_item(item: SelfTestItem) -> str:
    value = item.value
    if item.check == spec.CHECK_DEVICE_ID:
        return f"DEV_ID {value & 0xFFF:#05x}, IDCODE {value:#010x}"
    if item.check == spec.CHECK_FLASH_SIZE:
        return f"{value} KB"
    if item.check == spec.CHECK_CLOCK:
        return f"{value / 1_000_000:g} MHz"
    if item.check == spec.CHECK_RAM:
        return f"{value} bytes tested"
    if item.check == spec.CHECK_CRC:
        return f"CRC-32 {value:#010x}"
    if item.check == spec.CHECK_FLASH_READ:
        return "reads back"
    if item.check == spec.CHECK_OPTION_BYTES:
        mode = "single-bank" if value & (1 << 29) else "dual-bank"
        rdp = (value >> 8) & 0xFF
        level = {0xAA: "RDP 0", 0xCC: "RDP 2"}.get(rdp, "RDP 1")
        return f"OPTCR {value:#010x}, {mode}, {level}"
    return f"{value:#x}"


def _expected(item: SelfTestItem) -> str:
    return {
        spec.CHECK_DEVICE_ID: f"expected DEV_ID {spec.DEVICE_ID:#05x}",
        spec.CHECK_FLASH_SIZE: f"expected {spec.FLASH_SIZE_KB} KB",
        spec.CHECK_CLOCK: f"expected {spec.CORE_CLOCK_HZ / 1_000_000:g} MHz",
        spec.CHECK_OPTION_BYTES: "expected dual-bank and not RDP 2",
        spec.CHECK_RAM: "the RAM pattern test failed",
        spec.CHECK_CRC: "the CRC known-answer test failed",
        spec.CHECK_FLASH_READ: "the identity sector did not read back",
    }.get(item.check, "")


def _check_peripherals(report: ValidationReport, link: FactoryLink) -> None:
    try:
        items = link.selftest()
    except FactoryStatusError as exc:
        reason = (
            "this factory firmware has no self-test; flash a newer one"
            if exc.status == 1
            else str(exc)
        )
        report.add(PERIPHERALS, "Self-test", FAIL, reason)
        return
    except FactoryLinkError as exc:
        report.add(PERIPHERALS, "Self-test", FAIL, str(exc))
        return
    seen = {item.check for item in items}
    for item in items:
        name = spec.CHECK_NAMES.get(item.check, f"Check {item.check}")
        if item.result == spec.RESULT_PASS:
            report.add(PERIPHERALS, name, PASS, _describe_item(item))
        elif item.result == spec.RESULT_SKIP:
            report.add(PERIPHERALS, name, SKIP, "the firmware does not provide this reading")
        else:
            detail = _describe_item(item)
            expected = _expected(item)
            report.add(PERIPHERALS, name, FAIL, f"{detail} ({expected})" if expected else detail)
    for check, name in spec.CHECK_NAMES.items():
        if check not in seen:
            report.add(PERIPHERALS, name, FAIL, "the board did not report this check")


def run_validation(link: FactoryLink, log: Log | None = None) -> ValidationReport:
    """Read the board and return one report; the board is never written."""
    say = log or (lambda _text: None)
    report = ValidationReport()

    say("Reading the firmware identity")
    info: FactoryInfo = link.info()
    if info.is_factory_firmware:
        report.add(CONTENTS, "Factory firmware", PASS, f"{info.name} {info.version}")
    else:
        report.add(
            CONTENTS, "Factory firmware", FAIL, f"the board runs {info.name!r}, not the factory firmware"
        )
        report.add(CONTENTS, "Factory data", SKIP, "needs the factory firmware")
        report.add(PERIPHERALS, "Self-test", SKIP, "needs the factory firmware")
        return report
    blank_uid = info.uid in (bytes(12), b"\xff" * 12)
    report.add(
        CONTENTS,
        "Chip UID",
        FAIL if blank_uid else PASS,
        "the UID reads as blank" if blank_uid else info.uid_hex,
    )

    for kind in ("sys", "cal", "diag"):
        say(f"Checking the .{kind} block")
        _check_block(report, link, kind, info.uid)

    say("Running the peripheral self-test")
    _check_peripherals(report, link)
    say(f"Validation {report.verdict.upper()}: {report.summary()}")
    return report
