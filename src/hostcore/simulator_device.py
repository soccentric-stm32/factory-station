"""HWLINK device simulator (ARC-006).

Implements the HWVAL, BOOT and DEMO roles without hardware.  The simulator is
used by the desktop GUI, the headless CLI and the optional TCP server.
"""

from __future__ import annotations

import contextlib
import hashlib
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

from hostcore.images import verify_header_signature
from hostcore.layout import (
    IMAGE_EXTENSION_OFFSET,
    IMAGE_EXTENSION_SIZE,
    IMAGE_HEADER_SIZE,
    IMAGE_HEADER_VERSION,
    IMAGE_HEADER_VERSION_UNSIGNED,
)
from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.BootOutcome import BootOutcome
from hostcore.protocol.generated.hwlink.FlashMode import FlashMode
from hostcore.protocol.generated.hwlink.HealthState import HealthState
from hostcore.protocol.generated.hwlink.LedPattern import LedPattern
from hostcore.protocol.generated.hwlink.PromptKind import PromptKind
from hostcore.protocol.generated.hwlink.Role import Role
from hostcore.protocol.generated.hwlink.Slot import Slot
from hostcore.protocol.generated.hwlink.TestVerdict import TestVerdict
from hostcore.protocol.generated.hwlink.TrialState import TrialState
from hostcore.protocol.link import (
    FLAG_EVENT,
    FLAG_RESPONSE,
    ProtocolError,
    build_raw,
    cobs_decode,
    decode_raw,
)
from hostcore.test_catalogue import GROUPS, TESTS

UID_HEX = "002A004B3337500D37323336"
UID_VALUE = int(UID_HEX[:16], 16)
DEV_ID = 0x451
REV_ID = 0x1003

CATEGORY_MAP = {
    "DEV": "CPU & System",
    "CORE": "CPU & System",
    "FRT": "CPU & System",
    "CACHE": "CPU & System",
    "MPU": "CPU & System",
    "INT": "CPU & System",
    "DMA": "CPU & System",
    "DBG": "CPU & System",
    "RAM": "Memory",
    "FLS": "Memory",
    "OB": "Memory",
    "OTP": "Memory",
    "QSPI": "Memory",
    "FMC": "Memory",
    "SD": "Memory",
    "CLK": "Clocks & Power",
    "PWR": "Clocks & Power",
    "RST": "Clocks & Power",
    "WDG": "Clocks & Power",
    "RTC": "Clocks & Power",
    "GPIO": "GPIO & Board I/O",
    "LED": "GPIO & Board I/O",
    "BTN": "GPIO & Board I/O",
    "ADC": "Analog",
    "DAC": "Analog",
    "TIM": "Timers",
    "LPTIM": "Timers",
    "UART": "Communication",
    "SPI": "Communication",
    "I2S": "Communication",
    "I2C": "Communication",
    "CAN": "Communication",
    "SAI": "Communication",
    "SPDIF": "Communication",
    "ETH": "Connectivity",
    "USB": "Connectivity",
    "USBHS": "Connectivity",
    "LTDC": "Graphics & Media",
    "DMA2D": "Graphics & Media",
    "JPEG": "Graphics & Media",
    "DCMI": "Graphics & Media",
    "CRC": "Other Peripherals",
    "RNG": "Other Peripherals",
    "CEC": "Other Peripherals",
    "DFSDM": "Other Peripherals",
    "MDIOS": "Other Peripherals",
    "STLK": "Board",
    "BRD": "Board",
}

GROUP_NAMES = {
    "DEV": "Device Identity",
    "CORE": "Cortex-M7 Core",
    "FRT": "FreeRTOS",
    "CACHE": "Caches & TCM",
    "MPU": "MPU",
    "INT": "Interrupts & EXTI",
    "DMA": "DMA",
    "DBG": "Debug",
    "RAM": "RAM",
    "FLS": "Flash",
    "OB": "Option Bytes",
    "OTP": "OTP",
    "QSPI": "Quad-SPI",
    "FMC": "FMC",
    "SD": "SDMMC",
    "CLK": "Clocks",
    "PWR": "Power",
    "RST": "Reset",
    "WDG": "Watchdogs",
    "RTC": "RTC",
    "GPIO": "GPIO",
    "LED": "LEDs",
    "BTN": "Buttons",
    "ADC": "ADC",
    "DAC": "DAC",
    "TIM": "Timers",
    "LPTIM": "Low-Power Timer",
    "UART": "UART",
    "SPI": "SPI",
    "I2S": "I2S",
    "I2C": "I2C",
    "CAN": "CAN",
    "SAI": "SAI",
    "SPDIF": "SPDIF",
    "ETH": "Ethernet",
    "USB": "USB OTG FS",
    "USBHS": "USB OTG HS",
    "LTDC": "LTDC",
    "DMA2D": "DMA2D",
    "JPEG": "JPEG",
    "DCMI": "DCMI",
    "CRC": "CRC",
    "RNG": "RNG",
    "CEC": "CEC",
    "DFSDM": "DFSDM",
    "MDIOS": "MDIOS",
    "STLK": "ST-LINK",
    "BRD": "Board",
}

HARNESS_WIRES = [
    {"name": "W1 VREF 3.3V", "pin_a": "CN8-6", "pin_b": "CN10-7", "used_by": "ADC.003, DAC.001", "fitted": True, "verified": True},
    {"name": "W2 DAC1-ADC1", "pin_a": "CN7-10", "pin_b": "CN7-12", "used_by": "ADC.004", "fitted": True, "verified": True},
    {"name": "W3 CAN loop", "pin_a": "CN10-3", "pin_b": "CN10-5", "used_by": "CAN.001", "fitted": False, "verified": False},
    {"name": "W4 UART loop", "pin_a": "CN9-1", "pin_b": "CN9-2", "used_by": "UART.002", "fitted": True, "verified": True},
    {"name": "W5 ETH loop", "pin_a": "CN4-1", "pin_b": "CN4-3", "used_by": "ETH.001", "fitted": False, "verified": False},
    {"name": "W6 SPDIF loop", "pin_a": "CN10-29", "pin_b": "CN10-31", "used_by": "SPDIF.001", "fitted": True, "verified": False},
]


def _group_catalogue() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for test in TESTS:
        counts[str(test["group"])] = counts.get(str(test["group"]), 0) + 1
    result = []
    for group in GROUPS:
        code = str(group["code"])
        result.append(
            {
                "code": code,
                "name": GROUP_NAMES.get(code, str(group["name"])),
                "category": CATEGORY_MAP.get(code, "Other Peripherals"),
                "test_count": counts.get(code, int(group["test_count"])),
            }
        )
    return result


def _sample_result(test: dict[str, Any], iteration: int = 1) -> dict[str, Any]:
    test_id = str(test["id"])
    group = str(test["group"])
    passed = test_id not in {"ADC.004", "DAC.002", "SPDIF.001"}
    skipped = test_id in {"ADC.007", "ETH.008", "USBHS.001"} or bool(test.get("destructive"))
    duration = max(3, int(test.get("timeout_ms", 100)) // 20 + (hash(test_id) % 17))
    measured: dict[str, Any] = {}
    limits: dict[str, Any] = {}
    reason = ""
    if group in {"ADC", "DAC", "PWR", "CLK", "RTC"}:
        nominal = {
            "ADC": 1.212,
            "DAC": 1.650,
            "PWR": 3.300,
            "CLK": 216.0,
            "RTC": 32.768,
        }.get(group, 1.0)
        unit = {"CLK": "MHz", "RTC": "kHz"}.get(group, "V")
        low = nominal * 0.97
        high = nominal * 1.03
        if not passed:
            nominal = high * 1.01
        measured = {"value": round(nominal, 4), "unit": unit, "text": f"{nominal:.3f} {unit}", "valid": True}
        limits = {"min": round(low, 4), "max": round(high, 4), "unit": unit, "text": f"{low:.3f}-{high:.3f} {unit}", "valid": True}
    if skipped and test_id not in {"ETH.008", "USBHS.001"}:
        reason = "NO_OPERATOR" if test.get("operator") else "HARNESS_MISSING"
    elif not passed:
        reason = f"measured {measured.get('text', '')} outside {limits.get('text', '')}"
    verdict = TestVerdict.SKIP if skipped else (TestVerdict.PASS if passed else TestVerdict.FAIL)
    return {
        "id": test_id,
        "group": group,
        "result": verdict,
        "duration_ms": duration,
        "reason": reason,
        "measured": measured,
        "limits": limits,
        "reqs": str(test.get("reqs", "")),
        "iteration": iteration,
    }


@dataclass
class RunState:
    tests: list[dict[str, Any]] = field(default_factory=list)
    index: int = 0
    repeat: int = 1
    iteration: int = 1
    until_fail: bool = False
    allow_operator: bool = False
    allow_destructive: bool = False
    aborted: bool = False
    finished: bool = False
    waiting_prompt: dict[str, Any] | None = None
    prompt_deadline: float = 0.0
    next_at: float = 0.0
    counts: dict[str, int] = field(default_factory=lambda: {"PASS": 0, "FAIL": 0, "SKIP": 0, "N/A": 0})


@dataclass
class UpdateState:
    active: bool = False
    header: dict[str, Any] = field(default_factory=dict)
    data: bytearray = field(default_factory=bytearray)
    offset: int = 0


def _header_bytes(header: dict[str, Any]) -> bytes:
    """Rebuild the 256 header bytes so the CRC and signature can be checked."""
    raw = bytearray(IMAGE_HEADER_SIZE)
    struct.pack_into(
        "<IHHIIIIIII",
        raw,
        0,
        int(header.get("magic", 0)),
        int(header.get("header_version", 1)),
        int(header.get("header_size", IMAGE_HEADER_SIZE)),
        int(header.get("image_type", 1)),
        int(header.get("version", 0)),
        int(header.get("image_size", 0)),
        int(header.get("image_crc32", 0)),
        int(header.get("load_address", 0)),
        int(header.get("entry_address", 0)),
        int(header.get("build_timestamp", 0)),
    )
    raw[36:56] = bytes(header.get("git_commit") or b"")[:20].ljust(20, b"\0")
    raw[56:88] = str(header.get("name", "")).encode("ascii", "replace")[:32].ljust(32, b"\0")
    extension = bytes(header.get("extension") or b"")
    if extension:
        raw[IMAGE_EXTENSION_OFFSET : IMAGE_EXTENSION_OFFSET + IMAGE_EXTENSION_SIZE] = (
            extension[:IMAGE_EXTENSION_SIZE].ljust(IMAGE_EXTENSION_SIZE, b"\0")
        )
    struct.pack_into("<I", raw, 252, zlib.crc32(raw[:252]) & 0xFFFFFFFF)
    return bytes(raw)


def _extension_fields(header: dict[str, Any]) -> dict[str, Any]:
    extension = bytes(header.get("extension") or b"").ljust(IMAGE_EXTENSION_SIZE, b"\0")
    return {
        "security_counter": int.from_bytes(extension[0:4], "little"),
        "key_id": int.from_bytes(extension[4:8], "little"),
        "image_sha256": extension[8:40],
        "signature": extension[40:104],
    }


class SimulatedDevice:
    """Stateful device implementing all three HWLINK roles."""

    def __init__(self, role: str = "hwval", *, auto_answer: bool = True, tick: float = 0.01) -> None:
        self.role_name = role.lower()
        self.role = {
            "hwval": Role.HWVAL,
            "boot": Role.BOOTLOADER,
            "bootloader": Role.BOOTLOADER,
            "demo": Role.DEMO,
        }.get(self.role_name, Role.HWVAL)
        self.auto_answer = auto_answer
        self.tick = tick
        self.started = time.monotonic()
        self.connected = True
        self.last_seq: int = 0
        self.last_reply: list[bytes] = []
        self.groups = _group_catalogue()
        self.tests = [dict(test) for test in TESTS]
        for test in self.tests:
            test["category"] = CATEGORY_MAP.get(str(test["group"]), "Other Peripherals")
            test["needs"] = test.get("needs", "")
        self.results: list[dict[str, Any]] = []
        self.run_state: RunState | None = None
        self._scheduled: list[tuple[float, bytes]] = []
        self._run_events: list[bytes] = []
        self.firmware = {
            "name": "HWVAL",
            "version": "1.0.0",
            "commit": "b401296",
            "build": "2026-11-03T10:42:00Z",
        }
        self.boot = {
            "active_slot": Slot.A,
            "last_outcome": BootOutcome.BOOTED,
            "rejected_slot": Slot.UNKNOWN,
            "flash_mode": FlashMode.DUAL_BANK,
            "active_valid": True,
            "target_slot": Slot.B,
            "boot_version": "0.1.0",
            "boot_build": "simulated",
            "signed_images": False,
            "key_id": 0,
            "security_floor": 1,
            "slots": [
                {
                    "slot": Slot.A,
                    "present": True,
                    "valid": True,
                    "pending": False,
                    "confirmed": True,
                    "rejected": False,
                    "sequence": 7,
                    "trial_count": 1,
                    "version": 0x00010000,
                    "build_timestamp": 1793702520,
                    "git_commit": "b401296",
                    "name": "stm32-application",
                    "image_size": 0x1A400,
                    "detail": "confirmed",
                    "security_counter": 1,
                },
                {
                    "slot": Slot.B,
                    "present": False,
                    "valid": False,
                    "pending": False,
                    "confirmed": False,
                    "rejected": False,
                    "sequence": 0,
                    "trial_count": 0,
                    "version": 0,
                    "build_timestamp": 0,
                    "git_commit": "",
                    "name": "",
                    "image_size": 0,
                    "detail": "empty",
                    "security_counter": 0,
                },
            ],
        }
        self.update = UpdateState()
        # BOOT security model (P0-03 / section 8.2).
        self.signed_images = False
        self.signing_public_key: Any = None
        self.security_floor = 0
        self.boot_key_id = 0
        self.demo = {
            "leds": [
                {"led": 1, "pattern": LedPattern.HEARTBEAT, "period_ms": 1000},
                {"led": 2, "pattern": LedPattern.OFF, "period_ms": 500},
                {"led": 3, "pattern": LedPattern.OFF, "period_ms": 500},
            ],
            "fault": {
                "present": False,
                "reason": "",
                "cfsr": 0,
                "hfsr": 0,
                "mmfar": 0,
                "bfar": 0,
                "pc": 0,
                "lr": 0,
                "task": "",
                "file": "",
                "line": 0,
            },
            "trial": TrialState.CONFIRMED,
            "trial_count": 1,
            "health": HealthState.PASSED,
            "reset_cause": 0x04000000,
            "reset_cause_text": "power-on reset",
        }
        self.settings = {
            "led.pattern": str(LedPattern.HEARTBEAT),
            "led.period_ms": "1000",
        }
        self._lock = threading.RLock()

    # -- wire handling -------------------------------------------------
    def handle_frame(self, raw: bytes) -> list[bytes]:
        with self._lock:
            try:
                frame = decode_raw(raw)
            except ProtocolError:
                return []
            if frame.seq != 0 and frame.seq == self.last_seq:
                return list(self.last_reply)
            replies = self.handle_request(frame.payload, frame.seq)
            if frame.seq != 0:
                self.last_seq = frame.seq
                # Only the first reply is a response; events are not replayed.
                self.last_reply = [replies[0]] if replies else []
            return replies

    def handle_request(self, payload: bytes, seq: int) -> list[bytes]:
        if not codec.verify(payload):
            return [self._frame(codec.build_nak(seq, 1, "malformed FlatBuffer"), seq, FLAG_RESPONSE)]
        body_type = codec.body_type(payload)
        table = codec.body_table_from_payload(payload)
        handler = getattr(self, f"_on_{body_type}", None)
        if handler is None:
            return [self._frame(codec.build_nak(seq, 2, f"unsupported body {body_type}"), seq, FLAG_RESPONSE)]
        try:
            return handler(table, seq)
        except Exception as exc:
            return [self._frame(codec.build_nak(seq, 3, str(exc)), seq, FLAG_RESPONSE)]

    def _frame(self, payload: bytes, seq: int, flags: int) -> bytes:
        return build_raw(payload, seq, flags)

    def _reply(self, payload: bytes, seq: int) -> list[bytes]:
        return [self._frame(payload, seq, FLAG_RESPONSE)]

    def _event(self, payload: bytes) -> bytes:
        return self._frame(payload, 0, FLAG_EVENT)

    def _sample_result(self, test: dict[str, Any], iteration: int = 1) -> dict[str, Any]:
        """Return the simulated result row; subclasses may override it."""
        return _sample_result(test, iteration)

    # -- polling -------------------------------------------------------
    def poll(self) -> list[bytes]:
        """Return due unsolicited events; called by the simulated transport."""
        now = time.monotonic()
        out: list[bytes] = []
        with self._lock:
            while self._scheduled and self._scheduled[0][0] <= now:
                _, frame = self._scheduled.pop(0)
                out.append(frame)
            if self.run_state is not None:
                out.extend(self._advance_run(now))
        return out

    # -- common --------------------------------------------------------
    def _on_1(self, table: Any, seq: int) -> list[bytes]:  # Hello
        return self._reply(self._hello_reply(), seq)

    def _hello_reply(self) -> bytes:
        if self.role == Role.BOOTLOADER:
            fw_name, version, commit, build = "BOOT", "0.1.0", "b401296", "2026-11-03T10:30:00Z"
            flash_mode = self.boot["flash_mode"]
            uid = UID_VALUE
        elif self.role == Role.DEMO:
            fw_name, version, commit, build = "DEMO", "0.1.0", "b401296", "2026-11-03T10:35:00Z"
            flash_mode = FlashMode.DUAL_BANK
            uid = UID_VALUE
        else:
            fw_name, version, commit, build = "HWVAL", "1.0.0", "b401296", "2026-11-03T10:42:00Z"
            flash_mode = FlashMode.SINGLE_BANK
            uid = UID_VALUE
        return codec.build_hello_reply(
            role=self.role,
            firmware_name=fw_name,
            version=version,
            commit=commit,
            build_time=build,
            uid=uid,
            dev_id=DEV_ID,
            rev_id=REV_ID,
            flash_mode=flash_mode,
            detail="simulated device",
        )

    def _on_3(self, table: Any, seq: int) -> list[bytes]:  # Ping
        return self._reply(codec.build_pong(self.uptime_ms()), seq)

    def _on_5(self, table: Any, seq: int) -> list[bytes]:  # SetBaud
        return self._reply(codec.build_ack(seq), seq)

    def _on_10(self, table: Any, seq: int) -> list[bytes]:  # Reset
        self._reset_state()
        return self._reply(codec.build_ack(seq), seq)

    def _reset_state(self) -> None:
        self.started = time.monotonic()
        self.run_state = None
        self._scheduled.clear()

    def uptime_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    # -- HWVAL ---------------------------------------------------------
    def _on_26(self, table: Any, seq: int) -> list[bytes]:  # GetDeviceInfo
        info = {
            "firmware_name": "HWVAL",
            "version": "1.0.0",
            "commit": "b401296",
            "build_time": "2026-11-03T10:42:00Z",
            "srs_id": "SRS-HWVAL-F767",
            "dev_id": DEV_ID,
            "rev_id": REV_ID,
            "cpuid": 0x411FC271,
            "flash_size_kb": 2048,
            "package": "LQFP144",
            "uid": UID_HEX,
            "optcr": 0x0FFFAAED,
            "optcr1": 0x0000FFFF,
            "sysclk_hz": 216_000_000,
            "hclk_hz": 216_000_000,
            "pclk1_hz": 54_000_000,
            "pclk2_hz": 108_000_000,
            "apb1_tim_hz": 108_000_000,
            "apb2_tim_hz": 216_000_000,
            "pllq_hz": 48_000_000,
            "reset_cause": 0x04000000,
            "reset_cause_text": "power-on reset",
            "uptime_ms": self.uptime_ms(),
        }
        return self._reply(codec.build_device_info(info), seq)

    def _on_28(self, table: Any, seq: int) -> list[bytes]:  # ListGroups
        page = int(table.Page())
        per_page = 16
        start = page * per_page
        chunk = self.groups[start : start + per_page]
        count = (len(self.groups) + per_page - 1) // per_page
        return self._reply(codec.build_group_list(page, max(1, count), len(self.groups), chunk), seq)

    def _on_31(self, table: Any, seq: int) -> list[bytes]:  # ListTests
        group = codec.text(table.Group())
        page = int(table.Page())
        selected = [t for t in self.tests if not group or t["group"] == group]
        per_page = 4
        start = page * per_page
        chunk = selected[start : start + per_page]
        count = (len(selected) + per_page - 1) // per_page
        return self._reply(codec.build_test_list(page, max(1, count), len(selected), chunk), seq)

    def _on_34(self, table: Any, seq: int) -> list[bytes]:  # GetHarness
        page = int(table.Page())
        wires = [dict(wire) for wire in HARNESS_WIRES]
        mask = 0
        for index, wire in enumerate(wires):
            if wire["fitted"]:
                mask |= 1 << index
        return self._reply(codec.build_harness_info(mask, len(wires), wires, page, 1), seq)

    def _select_tests(self, table: Any) -> list[dict[str, Any]]:
        ids = [codec.text(table.Ids(i)) for i in range(int(table.IdsLength()))]
        groups = [codec.text(table.Groups(i)) for i in range(int(table.GroupsLength()))]
        all_tests = bool(table.All())
        levels = codec.text(table.Levels()) or "S,L,E"
        allowed_levels = {name.strip().upper() for name in levels.split(",") if name.strip()}
        selected: list[dict[str, Any]] = []
        for test in self.tests:
            if (ids or groups) and test["id"] not in ids and test["group"] not in groups:
                continue
            if not all_tests and not ids and not groups:
                continue
            if test.get("level_name", "S") not in allowed_levels and not ids:
                continue
            if test.get("operator") and not table.AllowOperator():
                selected.append({**test, "_skip_reason": "OPERATOR_DISABLED"})
                continue
            if test.get("destructive") and not table.AllowDestructive():
                selected.append({**test, "_skip_reason": "DESTRUCTIVE_DISABLED"})
                continue
            selected.append(dict(test))
        return selected

    def _on_37(self, table: Any, seq: int) -> list[bytes]:  # Run
        if self.role != Role.HWVAL:
            return self._reply(codec.build_nak(seq, 2, "Run requires HWVAL"), seq)
        if self.run_state is not None and not self.run_state.finished:
            return self._reply(codec.build_nak(seq, 4, "run already active"), seq)
        selected = self._select_tests(table)
        state = RunState(
            tests=selected,
            repeat=max(1, int(table.Repeat())),
            until_fail=bool(table.UntilFail()),
            allow_operator=bool(table.AllowOperator()),
            allow_destructive=bool(table.AllowDestructive()),
        )
        self.run_state = state
        replies = self._reply(codec.build_ack(seq), seq)
        replies.append(self._event(codec.build_run_started(len(selected), False)))
        state.next_at = time.monotonic() + self.tick
        return replies

    def _advance_run(self, now: float) -> list[bytes]:
        state = self.run_state
        if state is None or state.finished:
            return []
        out: list[bytes] = []
        if state.aborted and not state.finished:
            self.results = [r for r in self.results if r.get("_complete")]
            state.finished = True
            out.append(
                self._event(
                    codec.build_run_finished(
                        state.counts["PASS"] + state.counts["FAIL"] + state.counts["SKIP"] + state.counts["N/A"],
                        state.counts["PASS"],
                        state.counts["FAIL"],
                        state.counts["SKIP"],
                        state.counts["N/A"],
                        True,
                        False,
                    )
                )
            )
            return out
        if state.waiting_prompt is not None:
            prompt = state.waiting_prompt
            if now >= state.prompt_deadline:
                state.waiting_prompt = None
                result = self._result_for_prompt(prompt, None)
                self._emit_result(state, result, out)
            elif self.auto_answer:
                state.waiting_prompt = None
                result = self._result_for_prompt(prompt, "yes")
                self._emit_result(state, result, out)
            else:
                return out
        if state.index >= len(state.tests):
            if state.iteration < state.repeat:
                state.iteration += 1
                state.index = 0
                if state.until_fail and state.counts["FAIL"]:
                    state.repeat = state.iteration
            if state.index >= len(state.tests) or state.finished:
                state.finished = True
                out.append(
                    self._event(
                        codec.build_run_finished(
                            state.counts["PASS"] + state.counts["FAIL"] + state.counts["SKIP"] + state.counts["N/A"],
                            state.counts["PASS"],
                            state.counts["FAIL"],
                            state.counts["SKIP"],
                            state.counts["N/A"],
                            state.aborted,
                            False,
                        )
                    )
                )
                return out
        if now < state.next_at:
            return out
        test = state.tests[state.index]
        if test.get("_skip_reason"):
            result = self._sample_result(test, state.iteration)
            result["result"] = TestVerdict.SKIP
            result["reason"] = str(test["_skip_reason"])
            result["duration_ms"] = 1
            self._emit_result(state, result, out)
            return out
        index = state.index
        out.append(self._event(codec.build_test_started(str(test["id"]), index, len(state.tests))))
        if test.get("operator") and state.allow_operator:
            prompt_id = int(time.monotonic() * 1000) & 0x7FFFFFFF
            kind = PromptKind.BUTTON if "BTN" in str(test["group"]) else PromptKind.YES_NO
            text = (
                "Press and release B1 (blue user button) when the LED pattern changes."
                if kind == PromptKind.BUTTON
                else f"Prepare the harness for {test['id']}: {test['title']}, then confirm."
            )
            state.waiting_prompt = {"id": prompt_id, "test": test}
            state.prompt_deadline = now + 20.0
            out.append(
                self._event(
                    codec.build_operator_prompt(
                        prompt_id,
                        str(test["id"]),
                        kind,
                        text,
                        "yes,no,skip",
                        20,
                    )
                )
            )
            state.next_at = now + self.tick
            return out
        result = self._sample_result(test, state.iteration)
        self._emit_result(state, result, out)
        return out

    def _result_for_prompt(self, prompt: dict[str, Any], answer: str | None) -> dict[str, Any]:
        test = prompt["test"]
        result = self._sample_result(test, 1)
        if answer is None:
            result["result"] = TestVerdict.SKIP
            result["reason"] = "NO_OPERATOR"
            result["duration_ms"] = 20000
        elif answer.lower() in {"no", "skip", "n"}:
            result["result"] = TestVerdict.SKIP
            result["reason"] = "OPERATOR_SKIPPED"
        else:
            result["result"] = TestVerdict.PASS
            result["reason"] = ""
        return result

    def _emit_result(self, state: RunState, result: dict[str, Any], out: list[bytes]) -> None:
        state.index += 1
        verdict = int(result.get("result", 0))
        key = {TestVerdict.PASS: "PASS", TestVerdict.FAIL: "FAIL", TestVerdict.SKIP: "SKIP", TestVerdict.NA: "N/A"}.get(verdict, "N/A")
        state.counts[key] += 1
        result["_complete"] = True
        result["iteration"] = state.iteration
        self.results.append(result)
        out.append(self._event(codec.build_test_result(result)))
        if state.until_fail and verdict == TestVerdict.FAIL:
            state.repeat = state.iteration
        state.next_at = time.monotonic() + self.tick

    def _on_38(self, table: Any, seq: int) -> list[bytes]:  # OperatorAnswer
        state = self.run_state
        if state is None or state.waiting_prompt is None:
            return self._reply(codec.build_nak(seq, 4, "no prompt pending"), seq)
        prompt = state.waiting_prompt
        if int(table.PromptId()) != int(prompt["id"]):
            return self._reply(codec.build_nak(seq, 6, "unknown prompt"), seq)
        answer = codec.text(table.Answer())
        state.waiting_prompt = None
        result = self._result_for_prompt(prompt, answer)
        out = self._reply(codec.build_ack(seq), seq)
        self._emit_result(state, result, out)
        return out

    def _on_39(self, table: Any, seq: int) -> list[bytes]:  # Abort
        if self.run_state is not None:
            self.run_state.aborted = True
        return self._reply(codec.build_ack(seq), seq)

    def _on_43(self, table: Any, seq: int) -> list[bytes]:  # GetResults
        page = int(table.Page())
        per_page = 4
        start = page * per_page
        chunk = self.results[start : start + per_page]
        count = (len(self.results) + per_page - 1) // per_page
        return self._reply(codec.build_result_list(page, max(1, count), len(self.results), chunk), seq)

    def _on_45(self, table: Any, seq: int) -> list[bytes]:  # ClearResults
        self.results.clear()
        return self._reply(codec.build_ack(seq), seq)

    def _on_46(self, table: Any, seq: int) -> list[bytes]:  # GetReport
        counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "N/A": 0}
        for result in self.results:
            key = codec.VERDICT_NAMES.get(int(result.get("result", 3)), "N/A")
            counts[key] = counts.get(key, 0) + 1
        lines = [
            "HWVAL test report",
            "firmware: HWVAL 1.0.0 (git b401296)",
            f"uid: {UID_HEX}",
            f"total: {len(self.results)}",
            "",
        ]
        for result in self.results:
            lines.append(
                f"{codec.VERDICT_NAMES.get(int(result['result']), 'N/A'):4s} {result['id']:10s} "
                f"{result.get('duration_ms', 0):6d} ms  {result.get('reason', '')}"
            )
        return self._reply(
            codec.build_report(
                "\n".join(lines),
                counts["PASS"],
                counts["FAIL"],
                counts["SKIP"],
                counts["N/A"],
                len(self.results),
                counts["FAIL"] == 0,
            ),
            seq,
        )

    def _on_48(self, table: Any, seq: int) -> list[bytes]:  # GetSetting
        key = codec.text(table.Key())
        return self._reply(codec.build_setting(key, self.settings.get(key, ""), key in self.settings), seq)

    def _on_49(self, table: Any, seq: int) -> list[bytes]:  # SetSetting
        key = codec.text(table.Key())
        value = codec.text(table.Value())
        if key not in {"led.pattern", "led.period_ms", "test.repeat", "test.levels"}:
            return self._reply(codec.build_nak(seq, 6, f"unknown setting {key}"), seq)
        self.settings[key] = value
        return self._reply(codec.build_ack(seq), seq)

    def _on_51(self, table: Any, seq: int) -> list[bytes]:  # EnterTextMode
        return self._reply(codec.build_ack(seq), seq)

    # -- BOOT ----------------------------------------------------------
    def _on_11(self, table: Any, seq: int) -> list[bytes]:  # GetBootInfo
        return self._reply(codec.build_boot_info(self.boot), seq)

    def _on_13(self, table: Any, seq: int) -> list[bytes]:  # UpdateBegin
        header_table = table.Header()
        if header_table is None:
            return self._reply(codec.build_nak(seq, 7, "missing image header"), seq)
        header = {
            "magic": int(header_table.Magic()),
            "header_version": int(header_table.HeaderVersion()),
            "header_size": int(header_table.HeaderSize()),
            "image_type": int(header_table.ImageType()),
            "version": int(header_table.Version()),
            "image_size": int(header_table.ImageSize()),
            "image_crc32": int(header_table.ImageCrc32()),
            "load_address": int(header_table.LoadAddress()),
            "entry_address": int(header_table.EntryAddress()),
            "build_timestamp": int(header_table.BuildTimestamp()),
            "git_commit": bytes(
                header_table.GitCommit(i) for i in range(int(header_table.GitCommitLength()))
            ),
            "name": codec.text(header_table.Name()),
            "header_crc32": int(header_table.HeaderCrc32()),
            "extension": bytes(
                header_table.Extension(i) for i in range(int(header_table.ExtensionLength()))
            ),
        }
        if header["magic"] != 0x48574249 or header["header_size"] != 256:
            return self._reply(codec.build_nak(seq, 7, "bad image header"), seq)
        version = int(header["header_version"])
        if version not in (IMAGE_HEADER_VERSION_UNSIGNED, IMAGE_HEADER_VERSION):
            return self._reply(codec.build_nak(seq, 7, "bad image header version"), seq)
        if header["load_address"] != 0x0800C000 or header["image_size"] > 0x000F4000:
            return self._reply(codec.build_nak(seq, 8, "bad image layout"), seq)
        raw_header = _header_bytes(header)
        if struct.unpack_from("<I", raw_header, 252)[0] != int(header["header_crc32"]):
            return self._reply(codec.build_nak(seq, 7, "header CRC32 mismatch"), seq)
        if version == IMAGE_HEADER_VERSION:
            fields = _extension_fields(header)
            header.update(fields)
            if not any(fields["signature"]):
                return self._reply(codec.build_nak(seq, 14, "image has no signature"), seq)
            if self.signing_public_key is not None and not verify_header_signature(
                raw_header, self.signing_public_key
            ):
                return self._reply(codec.build_nak(seq, 14, "bad signature"), seq)
            if int(fields["security_counter"]) < int(self.security_floor):
                return self._reply(
                    codec.build_nak(
                        seq,
                        15,
                        f"security counter {fields['security_counter']} is below the "
                        f"floor {self.security_floor}",
                    ),
                    seq,
                )
        elif self.signed_images:
            return self._reply(codec.build_nak(seq, 14, "image is not signed"), seq)
        self.update = UpdateState(active=True, header=header)
        return self._reply(codec.build_ack(seq), seq)

    def _on_14(self, table: Any, seq: int) -> list[bytes]:  # UpdateData
        if not self.update.active:
            return self._reply(codec.build_nak(seq, 4, "no update in progress"), seq)
        offset = int(table.Offset())
        data = bytes(table.Data(i) for i in range(int(table.DataLength())))
        if offset != self.update.offset:
            return self._reply(
                codec.build_nak(seq, 10, "out of order", self.update.offset), seq
            )
        self.update.data.extend(data)
        self.update.offset += len(data)
        total = int(self.update.header.get("image_size", 0)) or self.update.offset
        if self.update.offset % max(1, total // 10 or 1) < len(data) or self.update.offset >= total:
            progress = codec.build_progress("update", self.update.offset, total)
            return [self._frame(codec.build_ack(seq, self.update.offset), seq, FLAG_RESPONSE), self._event(progress)]
        return self._reply(codec.build_ack(seq, self.update.offset), seq)

    def _on_15(self, table: Any, seq: int) -> list[bytes]:  # UpdateEnd
        if not self.update.active:
            return self._reply(codec.build_nak(seq, 4, "no update in progress"), seq)
        import zlib

        header = self.update.header
        ok = len(self.update.data) == int(header.get("image_size", 0))
        ok = ok and (zlib.crc32(self.update.data) & 0xFFFFFFFF) == int(header.get("image_crc32", 0))
        if not ok:
            self.update.active = False
            return self._reply(
                codec.build_update_result(False, int(header.get("image_crc32", 0)), 12, "CRC mismatch"),
                seq,
            )
        if int(header.get("header_version", 1)) == IMAGE_HEADER_VERSION:
            fields = _extension_fields(header)
            if hashlib.sha256(self.update.data).digest() != fields["image_sha256"]:
                self.update.active = False
                return self._reply(
                    codec.build_update_result(
                        False, int(header.get("image_crc32", 0)), 14, "image SHA-256 mismatch"
                    ),
                    seq,
                )
        inactive = Slot.B if self.boot["active_slot"] == Slot.A else Slot.A
        slot = next((s for s in self.boot["slots"] if s["slot"] == inactive), None)
        if slot is not None:
            slot.update(
                {
                    "present": True,
                    "valid": True,
                    "pending": True,
                    "confirmed": False,
                    "rejected": False,
                    "sequence": int(slot.get("sequence", 0)) + 1,
                    "trial_count": 0,
                    "version": int(header.get("version", 0)),
                    "build_timestamp": int(header.get("build_timestamp", 0)),
                    "git_commit": bytes(header.get("git_commit", b"")).hex(),
                    "name": str(header.get("name", "")),
                    "image_size": int(header.get("image_size", 0)),
                    "detail": "verified",
                    "security_counter": int(
                        _extension_fields(header)["security_counter"]
                        if int(header.get("header_version", 1)) == IMAGE_HEADER_VERSION
                        else 0
                    ),
                }
            )
        self.update.active = False
        return self._reply(
            codec.build_update_result(True, int(header.get("image_crc32", 0))), seq
        )

    def _on_17(self, table: Any, seq: int) -> list[bytes]:  # Activate
        pending = next((s for s in self.boot["slots"] if s.get("pending")), None)
        if pending is None:
            return self._reply(codec.build_nak(seq, 4, "no pending image"), seq)
        pending["pending"] = False
        pending["confirmed"] = True
        self.boot["active_slot"] = pending["slot"]
        self.boot["last_outcome"] = BootOutcome.BOOTED
        self.role = Role.DEMO
        return self._reply(codec.build_ack(seq), seq)

    def _on_18(self, table: Any, seq: int) -> list[bytes]:  # UpdateAbort
        self.update = UpdateState()
        return self._reply(codec.build_ack(seq), seq)

    # -- DEMO ----------------------------------------------------------
    def _on_23(self, table: Any, seq: int) -> list[bytes]:  # GetStatus
        status = {
            "firmware_name": "DEMO",
            "version": "0.1.0",
            "commit": "b401296",
            "build_time": "2026-11-03T10:35:00Z",
            "uid": UID_VALUE,
            "uptime_ms": self.uptime_ms(),
            "slot": self.boot["active_slot"],
            "trial": self.demo["trial"],
            "trial_count": self.demo["trial_count"],
            "health": self.demo["health"],
            "reset_cause": self.demo["reset_cause"],
            "reset_cause_text": self.demo["reset_cause_text"],
            "fault": self.demo["fault"],
            "leds": self.demo["leds"],
            "tasks": [
                {"name": "Health", "priority": 4, "state": 2, "stack_high_water": 344},
                {"name": "Comms", "priority": 3, "state": 2, "stack_high_water": 688},
                {"name": "Button", "priority": 2, "state": 2, "stack_high_water": 228},
                {"name": "LED", "priority": 1, "state": 2, "stack_high_water": 196},
            ],
            "heap_free": 0x1A40,
            "heap_min_free": 0x18C0,
            "protocol_major": 1,
            "protocol_minor": 1,
        }
        return self._reply(codec.build_status(status), seq)

    def _on_19(self, table: Any, seq: int) -> list[bytes]:  # SetLed
        led = int(table.Led())
        pattern = int(table.Pattern())
        period = int(table.PeriodMs())
        for entry in self.demo["leds"]:
            if entry["led"] == led:
                entry["pattern"] = pattern
                entry["period_ms"] = period
        return self._reply(codec.build_ack(seq), seq)

    def _on_20(self, table: Any, seq: int) -> list[bytes]:  # SetLedAll
        pattern = int(table.Pattern())
        period = int(table.PeriodMs())
        for entry in self.demo["leds"]:
            entry["pattern"] = pattern
            entry["period_ms"] = period
        return self._reply(codec.build_ack(seq), seq)

    def _on_21(self, table: Any, seq: int) -> list[bytes]:  # ClearFault
        self.demo["fault"] = {
            "present": False,
            "reason": "",
            "cfsr": 0,
            "hfsr": 0,
            "mmfar": 0,
            "bfar": 0,
            "pc": 0,
            "lr": 0,
            "task": "",
            "file": "",
            "line": 0,
        }
        for entry in self.demo["leds"]:
            if entry["led"] == 3:
                entry["pattern"] = LedPattern.OFF
        return self._reply(codec.build_ack(seq), seq)

    def _on_22(self, table: Any, seq: int) -> list[bytes]:  # EnterBootloader
        self.role = Role.BOOTLOADER
        self.boot["last_outcome"] = BootOutcome.UPDATE_MODE
        return self._reply(codec.build_ack(seq), seq)

    def _on_52(self, table: Any, seq: int) -> list[bytes]:  # RunStarted (host should not send)
        return self._reply(codec.build_nak(seq, 2, "event message"), seq)


class SimulatedTransport:
    """In-process transport with the same API as the serial worker."""

    def __init__(self, device: SimulatedDevice) -> None:
        self.device = device
        self._queue: list[bytes] = []
        self._lock = threading.Lock()
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise OSError("simulated transport closed")
        for frame in self._split_frames(data):
            replies = self.device.handle_frame(frame)
            with self._lock:
                self._queue.extend(replies)

    def _split_frames(self, data: bytes) -> list[bytes]:
        frames: list[bytes] = []
        buffer = bytearray()
        for byte in data:
            if byte == 0:
                if buffer:
                    with contextlib.suppress(ProtocolError):
                        frames.append(cobs_decode(bytes(buffer)))
                    buffer.clear()
            else:
                buffer.append(byte)
        return frames

    def read_frame(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._queue:
                    return self._queue.pop(0)
            for event in self.device.poll():
                with self._lock:
                    self._queue.append(event)
            with self._lock:
                if self._queue:
                    return self._queue.pop(0)
            if self._closed:
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.001)

    def close(self) -> None:
        self._closed = True
