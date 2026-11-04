"""HWLINK framing and synchronous request/response session (Appendix A).

The module is deliberately Qt-free (ARC-005) so that the device simulator,
the command-line interface and tests can use it without a QApplication.
"""

from __future__ import annotations

import contextlib
import queue
import struct
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

PROTOCOL_MAJOR = 1
FLAG_RESPONSE = 1 << 0
FLAG_EVENT = 1 << 1
MAX_FRAME = 2048
DEFAULT_TIMEOUT = 0.5
MAX_RETRIES = 3
HEADER = struct.Struct("<BBH")  # proto, flags, seq
CRC = struct.Struct("<I")


class LinkError(RuntimeError):
    """Base class for link failures."""


class LinkTimeout(LinkError):
    """No response arrived before the deadline."""


class TransportError(LinkError):
    """The transport is closed or failed."""


class ProtocolError(LinkError):
    """A frame or FlatBuffer was malformed."""


class NakError(LinkError):
    """The device rejected a request with a Nak."""

    def __init__(self, code: int, detail: str, expected_offset: int = 0) -> None:
        self.code = code
        self.detail = detail
        self.expected_offset = expected_offset
        super().__init__(f"Nak {code}: {detail}" if detail else f"Nak {code}")


ERROR_NAMES = {
    0: "NONE",
    1: "MALFORMED",
    2: "UNSUPPORTED",
    3: "BAD_ARGUMENT",
    4: "BAD_STATE",
    5: "BUSY",
    6: "NOT_FOUND",
    7: "BAD_HEADER",
    8: "BAD_LAYOUT",
    9: "BAD_VECTORS",
    10: "OUT_OF_ORDER",
    11: "FLASH_ERROR",
    12: "CRC_MISMATCH",
    13: "WRONG_FLASH_MODE",
}


def error_name(code: int) -> str:
    return ERROR_NAMES.get(code, f"ERROR_{code}")


# --------------------------------------------------------------------------
# COBS


def cobs_encode(data: bytes) -> bytes:
    """COBS-encode ``data`` and append the 0x00 frame delimiter."""
    out = bytearray()
    out.append(0)
    code_index = 0
    code = 1
    for byte in data:
        if byte == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_index] = 0xFF
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    out.append(0)
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Decode a COBS block without the trailing delimiter."""
    out = bytearray()
    index = 0
    length = len(data)
    while index < length:
        code = data[index]
        index += 1
        if code == 0:
            raise ProtocolError("unexpected COBS delimiter inside frame")
        block = code - 1
        if index + block > length:
            raise ProtocolError("truncated COBS block")
        out.extend(data[index : index + block])
        index += block
        if code != 0xFF and index < length:
            out.append(0)
    return bytes(out)


def build_raw(payload: bytes, seq: int, flags: int = 0, proto: int = PROTOCOL_MAJOR) -> bytes:
    raw = HEADER.pack(proto, flags, seq & 0xFFFF) + payload
    return raw + CRC.pack(zlib.crc32(raw) & 0xFFFFFFFF)


@dataclass(slots=True)
class RawFrame:
    flags: int
    seq: int
    payload: bytes


def decode_raw(raw: bytes) -> RawFrame:
    if len(raw) < HEADER.size + CRC.size:
        raise ProtocolError("short HWLINK frame")
    if len(raw) > MAX_FRAME:
        raise ProtocolError("HWLINK frame exceeds 2048 bytes")
    proto, flags, seq = HEADER.unpack_from(raw)
    if proto != PROTOCOL_MAJOR:
        raise ProtocolError(f"unsupported protocol major {proto}")
    body_len = len(raw) - CRC.size
    (crc,) = CRC.unpack_from(raw, body_len)
    if zlib.crc32(raw[:body_len]) & 0xFFFFFFFF != crc:
        raise ProtocolError("HWLINK CRC mismatch")
    return RawFrame(flags=flags, seq=seq, payload=raw[HEADER.size : body_len])


class FrameParser:
    """Incremental COBS frame parser with resynchronisation (LNK-004)."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> list[RawFrame]:
        frames: list[RawFrame] = []
        for byte in data:
            if byte == 0:
                if not self._buffer:
                    continue
                encoded = bytes(self._buffer)
                self._buffer.clear()
                try:
                    frames.append(decode_raw(cobs_decode(encoded)))
                except ProtocolError:
                    self.dropped += 1
            else:
                self._buffer.append(byte)
                if len(self._buffer) > MAX_FRAME * 2:
                    # A frame that cannot possibly fit: drop and resync on the
                    # next zero byte.
                    self.dropped += 1
                    self._buffer.clear()
        return frames

    def reset(self) -> None:
        self._buffer.clear()


# --------------------------------------------------------------------------
# Transport protocol


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read_frame(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------
# Session


@dataclass(slots=True)
class SessionConfig:
    timeout: float = DEFAULT_TIMEOUT
    retries: int = MAX_RETRIES
    host_name: str = "hwval-studio"
    host_version: str = "0.1.0"
    protocol_minor: int = 1


@dataclass(slots=True)
class Event:
    body_type: int
    payload: bytes = b""
    table: Any = None
    seq: int = 0


EventHandler = Callable[[Event], None]
LogHandler = Callable[[str, str], None]


class LinkSession:
    """One outstanding request at a time, with retries and event dispatch.

    The session is synchronous and owns no thread; the caller supplies a
    :class:`Transport` whose ``read_frame`` blocks.  Services call it from a
    worker thread so the Qt UI thread never blocks (ARC-002).
    """

    def __init__(
        self,
        transport: Transport,
        *,
        config: SessionConfig | None = None,
        event_handler: EventHandler | None = None,
        log_handler: LogHandler | None = None,
    ) -> None:
        self.transport = transport
        self.config = config or SessionConfig()
        self.event_handler = event_handler
        self.log_handler = log_handler
        self.seq = 0
        self.events: queue.Queue[Event] = queue.Queue()
        self._lock = threading.RLock()

    # -- low level -----------------------------------------------------
    def next_seq(self) -> int:
        self.seq = (self.seq + 1) & 0xFFFF
        if self.seq == 0:
            self.seq = 1
        return self.seq

    def send_raw(self, payload: bytes, seq: int, flags: int = 0) -> None:
        self.transport.write(cobs_encode(build_raw(payload, seq, flags)))

    def read_raw(self, timeout: float) -> RawFrame:
        raw = self.transport.read_frame(timeout)
        if raw is None:
            raise LinkTimeout("no HWLINK frame")
        return decode_raw(raw)

    # -- event dispatch -------------------------------------------------
    def _dispatch(self, frame: RawFrame) -> Event:
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink import Message
        from hostcore.protocol.generated.hwlink.Body import Body

        try:
            message = Message.Message.GetRootAs(frame.payload, 0)
            body_type = message.BodyType()
            if body_type == Body.NONE:
                raise ProtocolError("FlatBuffer has no body")
            table = codec.body_table(message)
        except Exception as exc:  # unknown body type or malformed table
            # LNK-013: a newer minor version may send events this build does
            # not know.  Log and ignore them instead of failing the request.
            if self.log_handler:
                self.log_handler("WARN", f"ignored unknown event: {exc}")
            return Event(body_type=0, payload=frame.payload, seq=frame.seq)
        event = Event(body_type=body_type, payload=frame.payload, table=table, seq=frame.seq)
        if body_type == Body.Log:
            level = codec.log_level(table)
            text = codec.text(table.Text())
            if self.log_handler:
                self.log_handler(level, text)
        if self.event_handler:
            self.event_handler(event)
        else:
            self.events.put(event)
        return event

    def wait_event(self, timeout: float) -> Event:
        """Read the transport until the next event arrives."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.events.get_nowait()
            except queue.Empty:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LinkTimeout("no event")
            try:
                frame = self.read_raw(remaining)
            except LinkTimeout:
                continue
            except ProtocolError as exc:
                if self.log_handler:
                    self.log_handler("WARN", f"dropped malformed frame: {exc}")
                continue
            if frame.flags & FLAG_EVENT:
                self._dispatch(frame)
                try:
                    return self.events.get_nowait()
                except queue.Empty:
                    return Event(body_type=0, payload=frame.payload, seq=frame.seq)
            # A response with no outstanding request (late reply) is dropped.

    # -- request/response ----------------------------------------------
    def request(
        self,
        payload: bytes,
        *,
        timeout: float | None = None,
        retries: int | None = None,
        response_types: set[int] | None = None,
        allow_nak: bool = False,
    ) -> tuple[int, Any]:
        """Send one request and return ``(body_type, table)``.

        Events received while waiting are dispatched and do not consume the
        response.  A ``Nak`` raises :class:`NakError` unless ``allow_nak`` is
        set, in which case it is returned like a normal response.
        """
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink.Body import Body

        timeout = self.config.timeout if timeout is None else timeout
        retries = self.config.retries if retries is None else retries
        with self._lock:
            seq = self.next_seq()
            attempt = 0
            while attempt <= retries:
                self.send_raw(payload, seq)
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        frame = self.read_raw(remaining)
                    except LinkTimeout:
                        break
                    except ProtocolError as exc:
                        if self.log_handler:
                            self.log_handler("WARN", f"dropped malformed frame: {exc}")
                        continue
                    if frame.flags & FLAG_EVENT:
                        self._dispatch(frame)
                        deadline = time.monotonic() + timeout
                        continue
                    if (frame.flags & FLAG_RESPONSE) and frame.seq == seq:
                        body_type = codec.body_type(frame.payload)
                        table = codec.body_table_from_payload(frame.payload)
                        if body_type == Body.Nak and not allow_nak:
                            nak = codec.parse_nak(table)
                            raise NakError(
                                int(nak.get("error", 0)),
                                str(nak.get("detail", "")),
                                int(nak.get("expected_offset", 0)),
                            )
                        if response_types and body_type not in response_types:
                            # A response for another operation may be left over
                            # from a previous timeout; drop it and keep waiting.
                            continue
                        return body_type, table
                    # A response with an unexpected sequence number: drop.
                attempt += 1
            raise LinkTimeout(f"timeout waiting for response to sequence {seq} after {retries + 1} attempts")

    # -- convenience requests ------------------------------------------
    def hello(self) -> dict[str, Any]:
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink.Body import Body

        body = codec.build_hello(self.config.host_name, self.config.host_version, self.config.protocol_minor)
        _body_type, table = self.request(body, response_types={Body.HelloReply})
        return codec.parse_hello_reply(table)

    def ping(self) -> int:
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink.Body import Body

        body = codec.build_ping(int(time.monotonic() * 1000))
        _, table = self.request(body, response_types={Body.Pong})
        return int(table.UptimeMs())

    def set_baud(self, rate: int) -> None:
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink.Body import Body

        body = codec.build_set_baud(rate)
        try:
            self.request(body, response_types={Body.Ack})
        except NakError:
            raise

    def reset(self) -> None:
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink.Body import Body

        body = codec.build_reset()
        with contextlib.suppress(LinkTimeout):
            # A device may reset before the Ack reaches the host.
            self.request(body, response_types={Body.Ack}, timeout=1.0)

    def enter_text_mode(self) -> None:
        from hostcore.protocol import codec
        from hostcore.protocol.generated.hwlink.Body import Body

        body = codec.build_enter_text_mode()
        with contextlib.suppress(LinkTimeout):
            self.request(body, response_types={Body.Ack}, timeout=1.0)

    def request_with_retry(
        self,
        payload_factory: Callable[[], bytes],
        *,
        timeout: float,
        response_types: set[int] | None = None,
        cancel: threading.Event | None = None,
    ) -> tuple[int, Any]:
        """Retry a request up to 3 times with the same sequence number.

        ``payload_factory`` is called once so the payload (and therefore the
        sequence-independent content) is identical across retries.
        """
        from hostcore.protocol.generated.hwlink.Body import Body

        payload = payload_factory()
        timeout = max(timeout, 0.05)
        with self._lock:
            seq = self.next_seq()
            for _attempt in range(MAX_RETRIES + 1):
                if cancel is not None and cancel.is_set():
                    raise LinkTimeout("cancelled")
                self.send_raw(payload, seq)
                deadline = time.monotonic() + timeout
                while True:
                    if cancel is not None and cancel.is_set():
                        raise LinkTimeout("cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        frame = self.read_raw(remaining)
                    except LinkTimeout:
                        break
                    except ProtocolError as exc:
                        if self.log_handler:
                            self.log_handler("WARN", f"dropped malformed frame: {exc}")
                        continue
                    if frame.flags & FLAG_EVENT:
                        self._dispatch(frame)
                        deadline = time.monotonic() + timeout
                        continue
                    if frame.seq == seq and (frame.flags & FLAG_RESPONSE):
                        from hostcore.protocol import codec

                        body_type = codec.body_type(frame.payload)
                        table = codec.body_table_from_payload(frame.payload)
                        if body_type == Body.Nak:
                            nak = codec.parse_nak(table)
                            raise NakError(
                                int(nak.get("error", 0)),
                                str(nak.get("detail", "")),
                                int(nak.get("expected_offset", 0)),
                            )
                        if response_types and body_type not in response_types:
                            continue
                        return body_type, table
                # retry
            raise LinkTimeout(f"timeout waiting for response to sequence {seq}")


@dataclass(slots=True)
class EventStream:
    """Helper used by long-running operations to consume role events."""

    session: LinkSession
    seen: list[Event] = field(default_factory=list)

    def next(self, timeout: float) -> Event:
        event = self.session.wait_event(timeout)
        self.seen.append(event)
        return event
