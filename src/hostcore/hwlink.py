"""HWLINK framing, session and simulator.

The on-wire format is a small binary frame carrying UTF-8 JSON payloads.  It is
the subset of HWLINK needed by the EOL station: hello/identity, test-plan run,
streamed test results, operator prompts, status and error messages.

Frame layout (little endian)::

    sync     2 bytes   0xA5 0x5A
    version  1 byte    1
    type     1 byte    message type
    flags    1 byte
    seq      2 bytes
    length   4 bytes   payload length
    payload  N bytes   UTF-8 JSON object
    crc32    4 bytes   CRC-32/ISO-HDLC over header + payload

The format is intentionally easy to implement in C on the firmware side and is
shared with HWVAL Studio through ``hostcore`` (ARCH-001, [LINK]).
"""

from __future__ import annotations

import json
import queue
import struct
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

SYNC = b"\xa5\x5a"
PROTOCOL_VERSION = 1
HEADER = struct.Struct("<2sBBHHI")
CRC = struct.Struct("<I")
MAX_PAYLOAD = 1 << 20


class MessageType:
    HELLO = 1
    HELLO_REPLY = 2
    RUN = 3
    RUN_REPLY = 4
    TEST_RESULT = 5
    PROMPT = 6
    PROMPT_REPLY = 7
    STATUS = 8
    LOG = 9
    ERROR = 10
    PING = 11
    PONG = 12
    RESET = 13


class HwLinkError(RuntimeError):
    """Protocol, transport or device error."""


@dataclass(slots=True)
class HwLinkMessage:
    type: int
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    flags: int = 0
    version: int = PROTOCOL_VERSION

    def encode(self) -> bytes:
        raw = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_PAYLOAD:
            raise HwLinkError("HWLINK payload too large")
        head = HEADER.pack(SYNC, self.version, self.type, self.flags, self.seq, len(raw))
        body = head + raw
        return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def decode(cls, frame: bytes) -> HwLinkMessage:
        if len(frame) < HEADER.size + CRC.size:
            raise HwLinkError("short HWLINK frame")
        sync, version, msg_type, flags, seq, length = HEADER.unpack_from(frame)
        if sync != SYNC:
            raise HwLinkError("bad HWLINK sync")
        if version != PROTOCOL_VERSION:
            raise HwLinkError(f"unsupported HWLINK version {version}")
        if length > MAX_PAYLOAD:
            raise HwLinkError("HWLINK payload too large")
        expected = HEADER.size + length + CRC.size
        if len(frame) != expected:
            raise HwLinkError("HWLINK frame length mismatch")
        (crc,) = CRC.unpack_from(frame, HEADER.size + length)
        if zlib.crc32(frame[: HEADER.size + length]) & 0xFFFFFFFF != crc:
            raise HwLinkError("HWLINK CRC mismatch")
        try:
            payload = json.loads(frame[HEADER.size : HEADER.size + length] or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HwLinkError(f"bad HWLINK payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise HwLinkError("HWLINK payload is not an object")
        return cls(type=msg_type, payload=payload, seq=seq, flags=flags, version=version)


class FrameParser:
    """Incremental frame parser for a byte stream."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[HwLinkMessage]:
        self._buf.extend(data)
        messages: list[HwLinkMessage] = []
        while True:
            start = self._buf.find(SYNC)
            if start < 0:
                # Keep a trailing 0xA5 in case the sync straddles reads.
                if self._buf[-1:] == SYNC[:1]:
                    del self._buf[:-1]
                else:
                    self._buf.clear()
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < HEADER.size:
                break
            _, _, _, _, _, length = HEADER.unpack_from(self._buf)
            if length > MAX_PAYLOAD:
                del self._buf[:2]
                continue
            total = HEADER.size + length + CRC.size
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            try:
                messages.append(HwLinkMessage.decode(frame))
            except HwLinkError:
                # A corrupt frame does not poison the rest of the stream.
                continue
        return messages


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, timeout: float) -> bytes: ...

    def close(self) -> None: ...


class SerialTransport:
    """pyserial transport, located by the ST-LINK USB serial (STN-003)."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - optional at import time
            raise HwLinkError("pyserial is required for serial HWLINK") from exc
        self.port = port
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    @classmethod
    def find_by_probe_serial(cls, probe_serial: str, baudrate: int = 115200) -> SerialTransport:
        """Find the VCP belonging to the same physical ST-LINK probe."""
        try:
            from serial.tools import list_ports
        except ImportError as exc:  # pragma: no cover
            raise HwLinkError("pyserial is required for serial HWLINK") from exc
        wanted = probe_serial.replace(":", "").lower()
        candidates = []
        for info in list_ports.comports():
            haystack = " ".join(
                str(x) for x in (info.serial_number, info.hwid, info.description, info.device)
            ).lower()
            if wanted and (wanted in haystack.replace(":", "")):
                candidates.append(info.device)
        if not candidates:
            raise HwLinkError(f"no VCP found for ST-LINK serial {probe_serial}")
        return cls(candidates[0], baudrate=baudrate)

    def write(self, data: bytes) -> None:
        self._serial.write(data)
        self._serial.flush()

    def read(self, timeout: float) -> bytes:
        self._serial.timeout = max(0.0, timeout)
        return bytes(self._serial.read(max(1, self._serial.in_waiting)))

    def close(self) -> None:
        self._serial.close()


class SimulatedTransport:
    """Transport backed by a :class:`SimulatedDevice` (no hardware)."""

    def __init__(self, device: SimulatedDevice) -> None:
        self.device = device
        self.device.attach(self)
        self._in: queue.Queue[bytes] = queue.Queue()
        self._parser = FrameParser()
        self._pending: queue.Queue[HwLinkMessage] = queue.Queue()
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise HwLinkError("transport closed")
        for message in self._parser.feed(data):
            for reply in self.device.feed(message):
                self._in.put(reply.encode())

    def read(self, timeout: float) -> bytes:
        if self._pending.empty():
            deadline = time.monotonic() + timeout
            try:
                raw = self._in.get(timeout=max(0.0, timeout))
                for msg in FrameParser().feed(raw):
                    self._pending.put(msg)
                while not self._in.empty() and self._pending.qsize() < 64:
                    raw = self._in.get_nowait()
                    for msg in FrameParser().feed(raw):
                        self._pending.put(msg)
            except queue.Empty:
                pass
            if self._pending.empty() and time.monotonic() < deadline:
                time.sleep(0.001)
        if self._pending.empty():
            return b""
        msg = self._pending.get()
        return msg.encode()

    def close(self) -> None:
        self._closed = True


class HwLinkSession:
    """Request/response session over a transport.

    The session is single-threaded and meant to be owned by a fixture worker.
    Incoming prompts are passed to ``prompt_handler``; the answer is sent back
    automatically.  Logs and test results can be consumed through callbacks.
    """

    def __init__(
        self,
        transport: Transport,
        prompt_handler: Callable[[dict[str, Any]], str] | None = None,
        log_handler: Callable[[str], None] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.transport = transport
        self.prompt_handler = prompt_handler
        self.log_handler = log_handler
        self.timeout = timeout
        self._seq = 0
        self._closed = False
        self._parser = FrameParser()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.transport.close()

    def __enter__(self) -> HwLinkSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def send(self, msg_type: int, payload: dict[str, Any] | None = None) -> int:
        seq = self._next_seq()
        msg = HwLinkMessage(type=msg_type, payload=payload or {}, seq=seq)
        self.transport.write(msg.encode())
        return seq

    def _read_message(self, deadline: float) -> HwLinkMessage | None:
        while time.monotonic() < deadline:
            chunk = self.transport.read(min(0.05, max(0.0, deadline - time.monotonic())))
            if not chunk:
                continue
            for msg in self._parser.feed(chunk):
                if msg.type == MessageType.LOG:
                    if self.log_handler:
                        self.log_handler(str(msg.payload.get("text", "")))
                    continue
                if msg.type == MessageType.PROMPT:
                    answer = "OK"
                    if self.prompt_handler:
                        answer = self.prompt_handler(msg.payload)
                    self.send(
                        MessageType.PROMPT_REPLY,
                        {
                            "prompt_id": msg.payload.get("prompt_id", ""),
                            "answer": answer,
                        },
                    )
                    continue
                return msg
        return None

    def request(
        self,
        msg_type: int,
        payload: dict[str, Any] | None = None,
        expect: int | tuple[int, ...] | None = None,
        timeout: float | None = None,
    ) -> HwLinkMessage:
        wait = self.timeout if timeout is None else timeout
        seq = self.send(msg_type, payload)
        deadline = time.monotonic() + wait
        expected = (expect,) if isinstance(expect, int) else expect
        while True:
            msg = self._read_message(deadline)
            if msg is None:
                raise HwLinkError(f"HWLINK timeout waiting for reply to type {msg_type}")
            if msg.seq not in (0, seq):
                continue
            if expected is not None and msg.type not in expected:
                if msg.type == MessageType.ERROR:
                    raise HwLinkError(str(msg.payload.get("message", "device error")))
                continue
            if msg.type == MessageType.ERROR:
                raise HwLinkError(str(msg.payload.get("message", "device error")))
            return msg

    def ping(self) -> bool:
        try:
            reply = self.request(MessageType.PING, expect=MessageType.PONG, timeout=1.0)
        except HwLinkError:
            return False
        return bool(reply.payload.get("ok", True))

    def hello(self, timeout: float = 5.0) -> dict[str, Any]:
        return self.request(
            MessageType.HELLO, expect=MessageType.HELLO_REPLY, timeout=timeout
        ).payload

    def status(self) -> dict[str, Any]:
        return self.request(MessageType.STATUS, expect=MessageType.STATUS).payload

    def reset(self) -> None:
        self.send(MessageType.RESET, {})

    def run(
        self,
        tests: list[str],
        limits: dict[str, dict[str, Any]] | None = None,
        allow_operator: bool = True,
        timeout: float | None = None,
        abort_event: threading.Event | None = None,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run a test plan and stream results until ``RUN_REPLY``.

        Returns ``{"tests": [...], "result": "PASS"|"FAIL"}``.  Every streamed
        result is collected; prompts are routed through ``prompt_handler``.
        """
        limits = limits or {}
        seq = self.send(
            MessageType.RUN,
            {
                "tests": tests,
                "limits": limits,
                "allow_operator": allow_operator,
            },
        )
        deadline = time.monotonic() + (
            timeout if timeout is not None else max(30.0, len(tests) * 2.0)
        )
        results: list[dict[str, Any]] = []
        while True:
            if abort_event is not None and abort_event.is_set():
                raise HwLinkError("aborted by operator")
            msg = self._read_message(deadline)
            if msg is None:
                raise HwLinkError("HWLINK timeout during test plan")
            if msg.seq not in (0, seq):
                continue
            if msg.type == MessageType.TEST_RESULT:
                results.append(msg.payload)
                if on_result is not None:
                    on_result(msg.payload)
                continue
            if msg.type == MessageType.RUN_REPLY:
                return {
                    "tests": results,
                    "result": str(msg.payload.get("result", "FAIL")),
                    "reason": str(msg.payload.get("reason", "")),
                }
            if msg.type == MessageType.ERROR:
                raise HwLinkError(str(msg.payload.get("message", "test plan error")))


class HwLinkClient:
    """Small facade used by higher layers and the CLI."""

    def __init__(self, session: HwLinkSession) -> None:
        self.session = session

    @classmethod
    def simulated(
        cls,
        device: SimulatedDevice,
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


# ---------------------------------------------------------------------------
# Simulated HWLINK device
# ---------------------------------------------------------------------------


class SimulatedDevice:
    """Deterministic HWVAL/product firmware simulator.

    ``identity`` is called for every ``HelloReply`` so the device can reflect
    the contents of the simulated flash (role, firmware, serial, MAC).
    """

    def __init__(
        self,
        role: str = "HWVAL",
        firmware: str = "hwval",
        version: int = 0x00020000,
        image_hash: str = "",
        identity: Callable[[], dict[str, Any]] | None = None,
        fail_tests: set[str] | None = None,
        prompt_tests: set[str] | None = None,
    ) -> None:
        self.role = role
        self.firmware = firmware
        self.version = version
        self.image_hash = image_hash
        self.identity = identity
        self.fail_tests = fail_tests or set()
        self.prompt_tests = prompt_tests or set()
        self._lock = threading.RLock()
        self._transport: SimulatedTransport | None = None
        self._pending: list[HwLinkMessage] = []
        self._run_tests: list[str] = []
        self._run_index = 0
        self._run_results: list[dict[str, Any]] = []
        self._run_seq = 0
        self._run_limits: dict[str, dict[str, Any]] = {}
        self._run_allow_operator = True
        self._awaiting_prompt: dict[str, Any] | None = None
        self._prompt_answer = ""
        self.logs: list[str] = []

    def attach(self, transport: SimulatedTransport) -> None:
        self._transport = transport

    def set_role(self, role: str, firmware: str, version: int, image_hash: str = "") -> None:
        with self._lock:
            self.role = role
            self.firmware = firmware
            self.version = version
            self.image_hash = image_hash

    def _emit(self, msg_type: int, payload: dict[str, Any], seq: int = 0) -> HwLinkMessage:
        return HwLinkMessage(type=msg_type, payload=payload, seq=seq)

    def feed(self, msg: HwLinkMessage) -> list[HwLinkMessage]:
        with self._lock:
            self._pending.clear()
            if msg.type == MessageType.HELLO:
                self._pending.append(self._hello_reply(msg.seq))
            elif msg.type == MessageType.PING:
                self._pending.append(self._emit(MessageType.PONG, {"ok": True}, msg.seq))
            elif msg.type == MessageType.STATUS:
                self._pending.append(
                    self._emit(MessageType.STATUS, self._status_payload(), msg.seq)
                )
            elif msg.type == MessageType.RESET:
                self._pending.append(self._emit(MessageType.STATUS, {"reset": True}, msg.seq))
            elif msg.type == MessageType.RUN:
                self._start_run(msg)
            elif msg.type == MessageType.PROMPT_REPLY:
                self._answer_prompt(msg)
            else:
                self._pending.append(
                    self._emit(
                        MessageType.ERROR,
                        {"message": f"unsupported HWLINK message {msg.type}"},
                        msg.seq,
                    )
                )
            out = list(self._pending)
            self._pending.clear()
            return out

    def _identity(self) -> dict[str, Any]:
        if self.identity is None:
            return {}
        try:
            return self.identity()
        except Exception as exc:  # pragma: no cover - defensive
            return {"identity_error": str(exc)}

    def _hello_reply(self, seq: int) -> HwLinkMessage:
        identity = self._identity()
        return self._emit(
            MessageType.HELLO_REPLY,
            {
                "role": self.role,
                "firmware": self.firmware,
                "version": self.version,
                "image_hash": self.image_hash,
                "slot": identity.get("slot", ""),
                "confirmed": identity.get("confirmed", False),
                "serial": identity.get("serial", ""),
                "product_id": identity.get("product_id", ""),
                "hw_revision": identity.get("hw_revision", ""),
                "mac": identity.get("mac", ""),
                "uid": identity.get("uid", ""),
                "record_revision": identity.get("record_revision", 0),
                "identity": identity,
            },
            seq,
        )

    def _status_payload(self) -> dict[str, Any]:
        identity = self._identity()
        return {
            "role": self.role,
            "firmware": self.firmware,
            "version": self.version,
            "slot": identity.get("slot", ""),
            "serial": identity.get("serial", ""),
            "confirmed": identity.get("confirmed", False),
        }

    def _start_run(self, msg: HwLinkMessage) -> None:
        self._run_tests = [str(t) for t in msg.payload.get("tests", [])]
        limits = msg.payload.get("limits", {})
        self._run_limits = limits if isinstance(limits, dict) else {}
        self._run_allow_operator = bool(msg.payload.get("allow_operator", True))
        self._run_index = 0
        self._run_results = []
        self._run_seq = msg.seq
        self._awaiting_prompt = None
        self._advance_run()

    def _advance_run(self) -> None:
        while self._run_index < len(self._run_tests):
            test_id = self._run_tests[self._run_index]
            if (
                self._run_allow_operator
                and test_id in self.prompt_tests
                and not self._prompt_answer
            ):
                prompt_id = f"prompt-{self._run_seq}-{self._run_index}"
                self._awaiting_prompt = {"test_id": test_id, "prompt_id": prompt_id}
                self._pending.append(
                    self._emit(
                        MessageType.PROMPT,
                        {
                            "prompt_id": prompt_id,
                            "fixture": "",
                            "test_id": test_id,
                            "text": "Press and release the USER button, then confirm.",
                            "choices": ["OK", "SKIP"],
                            "required": True,
                        },
                        self._run_seq,
                    )
                )
                return
            self._run_index += 1
            result = self._make_result(test_id, self._prompt_answer)
            self._prompt_answer = ""
            self._run_results.append(result)
            self._pending.append(self._emit(MessageType.TEST_RESULT, result, self._run_seq))
        overall = "PASS" if all(r["result"] == "PASS" for r in self._run_results) else "FAIL"
        failed = next((r for r in self._run_results if r["result"] != "PASS"), None)
        self._pending.append(
            self._emit(
                MessageType.RUN_REPLY,
                {
                    "result": overall,
                    "reason": "" if failed is None else str(failed.get("reason", "test failed")),
                    "count": len(self._run_results),
                },
                self._run_seq,
            )
        )

    def _answer_prompt(self, msg: HwLinkMessage) -> None:
        if self._awaiting_prompt is None:
            self._pending.append(
                self._emit(MessageType.ERROR, {"message": "no prompt pending"}, msg.seq)
            )
            return
        if msg.payload.get("prompt_id") != self._awaiting_prompt["prompt_id"]:
            self._pending.append(
                self._emit(MessageType.ERROR, {"message": "prompt id mismatch"}, msg.seq)
            )
            return
        self._prompt_answer = str(msg.payload.get("answer", "OK"))
        self._awaiting_prompt = None
        self._advance_run()

    def _make_result(self, test_id: str, answer: str) -> dict[str, Any]:
        limit = self._run_limits.get(test_id, {})
        base: dict[str, Any] = {
            "test_id": test_id,
            "result": "PASS",
            "measured_value": None,
            "measured_unit": "",
            "limit_min": limit.get("min"),
            "limit_max": limit.get("max"),
            "reason": "",
            "operator_answer": answer or None,
        }
        value = 0.0
        unit = ""
        if test_id.startswith("CLK.001"):
            value, unit = 16000.0, "kHz"
        elif test_id.startswith("CLK.002"):
            value, unit = 32768.0, "Hz"
        elif test_id.startswith("ADC.001"):
            value, unit = 1210.0, "mV"
        elif test_id.startswith("ADC"):
            value, unit = 1650.0, "raw"
        elif test_id.startswith("RAM"):
            value, unit = 32.0, "KB"
        elif test_id.startswith("FLS"):
            value, unit = 2048.0, "KB"
        elif test_id.startswith("ETH"):
            value, unit = 100.0, "Mbit/s"
        elif test_id.startswith("TEMP"):
            value, unit = 25.0, "C"
        base["measured_value"] = value
        base["measured_unit"] = unit
        if limit:
            if limit.get("min") is not None and value < float(limit["min"]):
                base["result"] = "FAIL"
                base["reason"] = f"{value} {unit} below {limit['min']}"
            if limit.get("max") is not None and value > float(limit["max"]):
                base["result"] = "FAIL"
                base["reason"] = f"{value} {unit} above {limit['max']}"
        if test_id in self.fail_tests:
            base["result"] = "FAIL"
            base["reason"] = f"simulated failure of {test_id}"
        return base
