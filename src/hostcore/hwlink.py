"""Real HWLINK stack: COBS frames, CRC-32, FlatBuffers messages.

This replaces the old invented ``0xA5 0x5A``/JSON protocol with the wire
format used by the firmware (``validation-firmware/src/hwlink.h`` and
``protocol/hwlink.fbs``).  The framing/codec code is shared with HWVAL Studio.
"""

from __future__ import annotations

import contextlib
import fnmatch
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.Body import Body
from hostcore.protocol.generated.hwlink.Role import Role
from hostcore.protocol.generated.hwlink.TestVerdict import TestVerdict
from hostcore.protocol.link import (
    PROTOCOL_MINOR,
    FrameParser,
    LinkError,
    LinkSession,
    LinkTimeout,
    ProtocolError,
    SessionConfig,
    TransportError,
    build_raw,
    cobs_decode,
)
from hostcore.protocol.link import Transport as RawTransport

HOST_VERSION = "0.1.0"

ROLE_NAMES = {Role.HWVAL: "HWVAL", Role.BOOTLOADER: "BOOTLOADER", Role.DEMO: "DEMO"}
VERDICT_NAMES = {TestVerdict.PASS: "PASS", TestVerdict.FAIL: "FAIL", TestVerdict.SKIP: "SKIP", TestVerdict.NA: "NA"}


class HwLinkError(LinkError):
    """Protocol, transport or device error."""


def _role_value(role: str | int | None) -> int | None:
    if role is None:
        return None
    if isinstance(role, int):
        return role
    key = role.strip().upper()
    if key in ("APP", "APPLICATION"):
        return Role.DEMO
    return {"HWVAL": Role.HWVAL, "BOOT": Role.BOOTLOADER, "BOOTLOADER": Role.BOOTLOADER, "DEMO": Role.DEMO}.get(key)


class SerialTransport:
    """pyserial transport, located by the ST-LINK USB serial (STN-003)."""

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise HwLinkError("pyserial is required for serial HWLINK") from exc
        try:
            self._serial = serial.Serial(port, baudrate, timeout=0.05, write_timeout=3)
        except Exception as exc:
            raise HwLinkError(f"cannot open {port}: {exc}") from exc
        self.port = port
        self._parser = FrameParser()
        self._frames: queue.Queue[bytes] = queue.Queue()
        self._closed = False

    @classmethod
    def find_by_probe_serial(cls, probe_serial: str, baudrate: int = 115200) -> SerialTransport:
        try:
            from serial.tools import list_ports
        except ImportError as exc:  # pragma: no cover
            raise HwLinkError("pyserial is required for serial HWLINK") from exc
        wanted = probe_serial.replace(":", "").lower()
        candidates: list[str] = []
        for info in list_ports.comports():
            haystack = " ".join(
                str(x) for x in (info.serial_number, info.hwid, info.description, info.device)
            ).lower()
            if wanted and wanted in haystack.replace(":", ""):
                candidates.append(info.device)
        if not candidates:
            raise HwLinkError(f"no VCP found for ST-LINK serial {probe_serial}")
        return cls(candidates[0], baudrate=baudrate)

    def write(self, data: bytes) -> None:
        if self._closed:
            raise HwLinkError("transport closed")
        try:
            self._serial.write(data)
            self._serial.flush()
        except Exception as exc:
            raise HwLinkError(f"serial write failed: {exc}") from exc

    def read_frame(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self._frames.get_nowait()
            except queue.Empty:
                pass
            if self._closed:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                waiting = self._serial.in_waiting
                data = self._serial.read(min(max(1, waiting), 4096))
            except Exception as exc:
                raise HwLinkError(f"serial read failed: {exc}") from exc
            for frame in self._parser.feed(data):
                self._frames.put(build_raw(frame.payload, frame.seq, frame.flags))
            if not data:
                time.sleep(0.001)

    def close(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            self._serial.close()


class SimulatedTransport:
    """In-process transport backed by the HWLINK device simulator."""

    def __init__(self, device: Any) -> None:
        self.device = device
        self._queue: list[bytes] = []
        self._lock = threading.Lock()
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise HwLinkError("simulated transport closed")
        for raw in _split_encoded_frames(data):
            for reply in self.device.handle_frame(raw):
                with self._lock:
                    self._queue.append(reply)

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
            if self._closed or time.monotonic() >= deadline:
                return None
            time.sleep(0.001)

    def close(self) -> None:
        self._closed = True


def _split_encoded_frames(data: bytes) -> list[bytes]:
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


class HwLinkSession:
    """Synchronous request/response session with event and prompt handling."""

    def __init__(
        self,
        transport: RawTransport,
        prompt_handler: Callable[[dict[str, Any]], str] | None = None,
        log_handler: Callable[[str], None] | None = None,
        timeout: float = 5.0,
        retries: int = 2,
    ) -> None:
        self.transport = transport
        self.prompt_handler = prompt_handler
        self.log_handler = log_handler
        self.timeout = timeout
        self.identity: dict[str, Any] = {}
        self.role: int = 0
        self.link_log: list[str] = []
        self._closed = False

        def _on_log(level: str, text: str) -> None:
            if log_handler:
                log_handler(f"{level}: {text}" if level else text)

        self._link = LinkSession(
            transport,
            config=SessionConfig(
                timeout=timeout,
                retries=retries,
                host_name="hwval-factory",
                host_version=HOST_VERSION,
                protocol_minor=PROTOCOL_MINOR,
            ),
            log_handler=_on_log,
        )

    def _record(self, text: str) -> None:
        """Keep a link-up trace (P0-10) without flooding the station log."""
        self.link_log.append(text)
        if self.log_handler:
            self.log_handler(text)

    # -- lifecycle -----------------------------------------------------
    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                self.transport.close()

    def __enter__(self) -> HwLinkSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- discovery -----------------------------------------------------
    def hello(self, timeout: float | None = None) -> dict[str, Any]:
        body = codec.build_hello("hwval-factory", HOST_VERSION, PROTOCOL_MINOR)
        _, table = self._link.request(
            body,
            response_types={Body.HelloReply},
            timeout=timeout if timeout is not None else self.timeout,
        )
        reply = codec.parse_hello_reply(table)
        role_id = int(reply.get("role", 0))
        reply["role_id"] = role_id
        reply["role"] = reply.get("role_name", "")
        reply["firmware"] = reply.get("firmware_name", "")
        self.identity = reply
        self.role = role_id
        return reply

    def connect(
        self,
        expected_role: str | int | None = None,
        *,
        timeout: float | None = None,
        link_retry_s: float = 0.5,
    ) -> dict[str, Any]:
        """Enter link mode if needed and return the normalized HelloReply.

        HWVAL boots into its text CLI and may be running its boot test suite,
        during which it ignores HWLINK (LINK-010).  Keep writing ``mode link``
        at a bounded interval until a Hello is answered, the same persistence
        as ``bootctl catch`` (P0-10).  The switch survives one reset, so an
        already-linked device answers immediately.
        """
        deadline = time.monotonic() + (timeout if timeout is not None else max(self.timeout, 10.0))
        last: Exception | None = None
        next_link_at = time.monotonic()
        link_attempts = 0
        while time.monotonic() < deadline:
            try:
                reply = self.hello(timeout=min(2.0, max(0.3, deadline - time.monotonic())))
                break
            except (LinkTimeout, LinkError) as exc:
                last = exc
                now = time.monotonic()
                if now >= next_link_at:
                    link_attempts += 1
                    self._record(f"link-up: mode link ({link_attempts})")
                    with contextlib.suppress(Exception):
                        self.transport.write(b"mode link\r")
                    next_link_at = now + link_retry_s
                time.sleep(0.15)
        else:
            raise HwLinkError(
                f"firmware did not answer HWLINK Hello after {link_attempts} "
                f"mode-link attempt(s): {last}"
            )
        major = int(reply.get("protocol_major", 0))
        if major != 1:
            raise HwLinkError(f"unsupported HWLINK protocol major {major}")
        wanted = _role_value(expected_role)
        if wanted is not None and int(reply.get("role_id", 0)) != wanted:
            raise HwLinkError(
                f"expected {expected_role} role, device reports {reply.get('role_name')}"
            )
        self._record(
            f"link-up: {reply.get('role_name')} {reply.get('firmware_name')} "
            f"{reply.get('version')} (minor {reply.get('protocol_minor')})"
        )
        return reply

    def request(self, payload: bytes, *, response_types: set[int] | None = None, timeout: float | None = None) -> Any:
        try:
            return self._link.request(
                payload,
                response_types=response_types,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except LinkError as exc:
            raise HwLinkError(str(exc)) from exc

    # -- HWVAL ---------------------------------------------------------
    def device_info(self) -> dict[str, Any]:
        _, table = self.request(codec.build_get_device_info(), response_types={Body.DeviceInfo})
        return codec.parse_device_info(table)

    def list_tests(self) -> list[dict[str, Any]]:
        tests: list[dict[str, Any]] = []
        page = 0
        while True:
            _, table = self.request(
                codec.build_list_tests("", page), response_types={Body.TestList}
            )
            data = codec.parse_test_list(table)
            tests.extend(data["tests"])
            page += 1
            if page >= int(data["page_count"]) or page > 256:
                break
        return tests

    def resolve_tests(self, patterns: list[str]) -> list[dict[str, Any]]:
        catalogue = self.list_tests()
        if not catalogue:
            raise HwLinkError("device returned an empty test catalogue")
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        missing: list[str] = []
        for pattern in patterns:
            matches = [
                test
                for test in catalogue
                if test["id"] == pattern
                or fnmatch.fnmatchcase(test["id"], pattern)
                or (not any(ch in pattern for ch in "*?[") and test["group"] == pattern)
            ]
            if not matches:
                missing.append(pattern)
            for test in matches:
                if test["id"] not in seen:
                    seen.add(test["id"])
                    selected.append(test)
        if missing:
            raise HwLinkError(
                "test(s) not in the device catalogue: " + ", ".join(missing)
            )
        return selected

    def run(
        self,
        tests: list[str],
        limits: dict[str, dict[str, Any]] | None = None,
        allow_operator: bool = True,
        timeout: float | None = None,
        abort_event: threading.Event | None = None,
        on_result: Callable[[dict[str, Any]], None] | None = None,
        *,
        allow_destructive: bool = False,
        selection: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selected = selection if selection is not None else self.resolve_tests(tests)
        if not selected:
            raise HwLinkError("empty test selection")
        if not allow_destructive:
            destructive = [t for t in selected if bool(t.get("destructive"))]
            if destructive:
                names = ", ".join(str(t["id"]) for t in destructive[:6])
                raise HwLinkError(f"profile selects destructive test(s) without permission: {names}")
        ids = [str(test["id"]) for test in selected]
        payload = codec.build_run(
            ids=ids,
            groups=[],
            all_tests=False,
            levels="S,L,E",
            allow_operator=allow_operator,
            allow_destructive=allow_destructive,
            repeat=1,
            until_fail=False,
        )
        self.request(payload, response_types={Body.Ack}, timeout=3.0)
        deadline = time.monotonic() + (timeout if timeout is not None else 120.0)
        results: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
        counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "NA": 0}
        while True:
            if abort_event is not None and abort_event.is_set():
                self.abort()
                raise HwLinkError("aborted by operator")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HwLinkError("HWLINK timeout during test plan")
            try:
                event = self._link.wait_event(min(0.25, remaining))
            except LinkTimeout:
                continue
            except LinkError as exc:
                raise HwLinkError(str(exc)) from exc
            if event.body_type == Body.OperatorPrompt:
                prompt = codec.parse_operator_prompt(event.table)
                prompt["answers_list"] = [
                    part.strip() for part in prompt.get("answers", "").split(",") if part.strip()
                ]
                answer = "OK"
                if self.prompt_handler:
                    answer = self.prompt_handler(prompt)
                self.request(
                    codec.build_operator_answer(int(prompt["prompt_id"]), str(answer)),
                    response_types={Body.Ack, Body.OperatorPromptDone},
                    timeout=3.0,
                )
                deadline = time.monotonic() + (timeout if timeout is not None else 120.0)
                continue
            if event.body_type == Body.TestResult:
                row = normalize_result(codec.parse_test_result(event.table))
                results.append(row)
                verdict = str(row["result"])
                counts[verdict if verdict in counts else "NA"] += 1
                if on_result is not None:
                    on_result(row)
                deadline = time.monotonic() + (timeout if timeout is not None else 120.0)
                continue
            if event.body_type == Body.RunFinished:
                finished = codec.parse_run_finished(event.table)
                summary = dict(finished)
                break
            if event.body_type in (Body.Progress, Body.Log, Body.TestStarted, Body.RunStarted, Body.OperatorPromptDone):
                deadline = time.monotonic() + (timeout if timeout is not None else 120.0)
                continue
        failed = next((row for row in results if row["result"] != "PASS"), None)
        overall = "PASS"
        if summary.get("aborted"):
            overall = "ABORTED"
        elif summary.get("fail", 0) or failed is not None:
            overall = "FAIL"
        elif summary.get("skip", 0):
            # SKIP is not a pass for an end-of-line plan.
            overall = "FAIL"
        return {
            "tests": results,
            "result": overall,
            "reason": "" if failed is None else str(failed.get("reason", "test failed")),
            "counts": counts,
            "summary": summary,
            "selection": selected,
        }

    def abort(self) -> None:
        with contextlib.suppress(HwLinkError):
            self.request(codec.build_abort(), response_types={Body.Ack}, timeout=1.0)

    def update_image(
        self,
        image: Any,
        *,
        chunk_size: int = 1024,
        activate: bool = True,
        force: bool = False,
        progress: Callable[[int, int], None] | None = None,
        abort_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Upload a signed IBWH image to BOOT over HWLINK (B-13 / P0-07)."""
        header = image.header_dict()
        body = bytes(image.data)
        self.request(
            codec.build_update_begin(header, force=force),
            response_types={Body.Ack},
            timeout=5.0,
        )
        offset = 0
        while offset < len(body):
            if abort_event is not None and abort_event.is_set():
                with contextlib.suppress(HwLinkError):
                    self.abort_update()
                raise HwLinkError("update cancelled by operator")
            chunk = body[offset : offset + chunk_size]
            _, table = self.request(
                codec.build_update_data(offset, chunk),
                response_types={Body.Ack, Body.Nak},
                timeout=10.0,
            )
            ack = codec.parse_ack(table)
            next_offset = int(ack.get("next_offset", 0))
            if next_offset <= offset:
                raise HwLinkError(f"BOOT did not advance past update offset {offset}")
            offset = next_offset
            if progress is not None:
                progress(offset, len(body))
            self._drain_progress()
        _, table = self.request(
            codec.build_update_end(),
            response_types={Body.UpdateResult},
            timeout=30.0,
        )
        result = codec.parse_update_result(table)
        if not bool(result.get("success")):
            raise HwLinkError(
                f"BOOT update failed: {result.get('error_name')}: {result.get('detail')}"
            )
        if activate:
            self.request(codec.build_activate(), response_types={Body.Ack}, timeout=5.0)
        return result

    def abort_update(self) -> None:
        with contextlib.suppress(HwLinkError):
            self.request(codec.build_update_abort(), response_types={Body.Ack}, timeout=1.0)

    def _drain_progress(self) -> None:
        while True:
            try:
                event = self._link.events.get_nowait()
            except queue.Empty:
                return
            if event.body_type == Body.Progress:
                progress = codec.parse_progress(event.table)
                if self.log_handler:
                    self.log_handler(
                        f"{progress.get('operation')}: {progress.get('done')}/{progress.get('total')}"
                    )

    # -- DEMO / BOOT ---------------------------------------------------
    def status(self) -> dict[str, Any]:
        _, table = self.request(codec.build_get_status(), response_types={Body.Status})
        return codec.parse_status(table)

    def boot_info(self) -> dict[str, Any]:
        _, table = self.request(codec.build_get_boot_info(), response_types={Body.BootInfo})
        return codec.parse_boot_info(table)

    def reset(self) -> None:
        with contextlib.suppress(LinkError):
            self._link.reset()

    def enter_text_mode(self) -> None:
        with contextlib.suppress(LinkError):
            self._link.enter_text_mode()


def normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
    measured = raw.get("measured") or {}
    limits = raw.get("limits") or {}
    valid_measured = bool(measured.get("valid"))
    valid_limits = bool(limits.get("valid"))
    return {
        "test_id": str(raw.get("id", "")),
        "group": str(raw.get("group", "")),
        "result": str(raw.get("result_name", "NA")),
        "duration_ms": int(raw.get("duration_ms", 0)),
        "reason": str(raw.get("reason", "")),
        "measured_value": float(measured.get("value", 0.0)) if valid_measured else None,
        "measured_unit": str(measured.get("unit", "")) if valid_measured else "",
        "measured_text": str(measured.get("text", "")),
        "limit_min": float(limits.get("min", 0.0)) if valid_limits else None,
        "limit_max": float(limits.get("max", 0.0)) if valid_limits else None,
        "limit_text": str(limits.get("text", "")),
        "operator_answer": raw.get("operator_answer"),
        "reqs": str(raw.get("reqs", "")),
    }


class HwLinkClient:
    """Small facade used by higher layers and tools."""

    def __init__(self, session: HwLinkSession) -> None:
        self.session = session

    @classmethod
    def simulated(
        cls,
        device: Any,
        prompt_handler: Callable[[dict[str, Any]], str] | None = None,
        log_handler: Callable[[str], None] | None = None,
    ) -> HwLinkClient:
        return cls(HwLinkSession(SimulatedTransport(device), prompt_handler, log_handler))

    @classmethod
    def serial(
        cls,
        port: str,
        prompt_handler: Callable[[dict[str, Any]], str] | None = None,
        log_handler: Callable[[str], None] | None = None,
    ) -> HwLinkClient:
        return cls(HwLinkSession(SerialTransport(port), prompt_handler, log_handler))

    def close(self) -> None:
        self.session.close()


__all__ = [
    "HwLinkClient",
    "HwLinkError",
    "HwLinkSession",
    "SerialTransport",
    "SimulatedTransport",
    "TransportError",
    "normalize_result",
]
