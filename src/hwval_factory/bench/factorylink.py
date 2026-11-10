"""Client for the factory firmware's UART commands (spec section 5)."""

from __future__ import annotations

import contextlib
import struct
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from hostcore.protocol.link import (
    CRC,
    HEADER,
    ProtocolError,
    build_raw,
    cobs_decode,
    cobs_encode,
)
from hwval_factory.bench import files, spec

Progress = Callable[[int, int], None]


class FactoryLinkError(RuntimeError):
    """A factory command failed or the device did not answer."""


class FactoryStatusError(FactoryLinkError):
    def __init__(self, command: int, status: int) -> None:
        self.command = command
        self.status = status
        super().__init__(f"device refused the command: {spec.STATUS_NAMES.get(status, status)}")


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read_frame(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


class SerialFactoryTransport:
    """pyserial transport that keeps the protocol byte of every frame."""

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise FactoryLinkError("pyserial is required to talk to the board") from exc
        try:
            self._serial = serial.Serial(port, baudrate, timeout=0.05, write_timeout=3)
        except Exception as exc:
            raise FactoryLinkError(f"cannot open {port}: {exc}") from exc
        self.port = port
        self._buffer = bytearray()

    def write(self, data: bytes) -> None:
        try:
            self._serial.write(data)
            self._serial.flush()
        except Exception as exc:
            raise FactoryLinkError(f"serial write failed: {exc}") from exc

    def read_frame(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                chunk = self._serial.read(max(1, self._serial.in_waiting))
            except Exception as exc:
                raise FactoryLinkError(f"serial read failed: {exc}") from exc
            for byte in chunk:
                if byte == 0:
                    if self._buffer:
                        encoded = bytes(self._buffer)
                        self._buffer.clear()
                        try:
                            return cobs_decode(encoded)
                        except ProtocolError:
                            continue
                else:
                    self._buffer.append(byte)
                    if len(self._buffer) > 5000:
                        self._buffer.clear()
            if time.monotonic() >= deadline:
                return None

    def flush_input(self) -> None:
        self._buffer.clear()
        self._serial.reset_input_buffer()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._serial.close()


@dataclass(slots=True)
class FactoryInfo:
    name: str
    version: str
    build: str
    uid: bytes

    @property
    def uid_hex(self) -> str:
        return self.uid.hex().upper()

    @property
    def is_factory_firmware(self) -> bool:
        return self.name == spec.FACTORY_FIRMWARE_NAME


@dataclass(slots=True)
class SelfTestItem:
    """One SELFTEST check: id, result (see ``spec.RESULT_*``) and the measured value."""

    check: int
    result: int
    value: int


def split_strings(blob: bytes, count: int) -> tuple[list[str], bytes]:
    out: list[str] = []
    offset = 0
    for _ in range(count):
        if offset >= len(blob):
            raise FactoryLinkError("INFO reply is truncated")
        length = blob[offset]
        offset += 1
        if offset + length > len(blob):
            raise FactoryLinkError("INFO reply is truncated")
        out.append(blob[offset : offset + length].decode("ascii", "replace"))
        offset += length
    return out, blob[offset:]


class FactoryLink:
    def __init__(self, transport: Transport, timeout: float = 1.0, retries: int = 3) -> None:
        self.transport = transport
        self.timeout = timeout
        self.retries = retries
        self._seq = 0

    def close(self) -> None:
        self.transport.close()

    def _next_seq(self) -> int:
        self._seq = (self._seq % 0xFFFF) + 1
        return self._seq

    def _exchange(self, payload: bytes, timeout: float, retries: int) -> bytes:
        seq = self._next_seq()
        frame = cobs_encode(build_raw(payload, seq, 0, spec.PROTO_FACTORY))
        for _attempt in range(max(1, retries)):
            self.transport.write(frame)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw = self.transport.read_frame(remaining)
                if raw is None:
                    break
                reply = self._decode(raw, seq)
                if reply is not None:
                    return reply
        raise FactoryLinkError(
            "the board did not answer; check the UART port and that the factory firmware is running"
        )

    @staticmethod
    def _decode(raw: bytes, seq: int) -> bytes | None:
        if len(raw) < HEADER.size + CRC.size:
            return None
        proto, _flags, frame_seq = HEADER.unpack_from(raw)
        body = len(raw) - CRC.size
        if zlib.crc32(raw[:body]) & 0xFFFFFFFF != CRC.unpack_from(raw, body)[0]:
            return None
        if proto != spec.PROTO_FACTORY or frame_seq != seq:
            return None
        return raw[HEADER.size : body]

    def request(
        self,
        command: int,
        args: bytes = b"",
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> bytes:
        reply = self._exchange(
            bytes([command]) + args,
            self.timeout if timeout is None else timeout,
            self.retries if retries is None else retries,
        )
        if len(reply) < 2 or reply[0] != (command | spec.REPLY_FLAG):
            raise FactoryLinkError("unexpected reply from the board")
        if reply[1] != 0:
            raise FactoryStatusError(command, reply[1])
        return reply[2:]

    # -- commands -----------------------------------------------------
    def info(self) -> FactoryInfo:
        body = self.request(spec.CMD_INFO)
        (name, version, build), rest = split_strings(body, 3)
        if len(rest) < 12:
            raise FactoryLinkError("INFO reply has no chip UID")
        return FactoryInfo(name, version, build, rest[:12])

    def read(self, address: int, length: int, progress: Progress | None = None) -> bytes:
        out = bytearray()
        while len(out) < length:
            count = min(spec.MAX_CHUNK, length - len(out))
            out += self.request(spec.CMD_READ, struct.pack("<IH", address + len(out), count))
            if progress:
                progress(len(out), length)
        return bytes(out)

    def write_file(self, address: int, data: bytes, progress: Progress | None = None) -> None:
        """Send one validated file to ``address`` and confirm it by reading it back."""
        if len(data) > spec.MAX_FILE_SIZE:
            raise FactoryLinkError(f"file is {len(data)} bytes; the limit is {spec.MAX_FILE_SIZE}")
        crc = files.crc32(data)
        self.request(spec.CMD_WRITE_BEGIN, struct.pack("<IHI", address, len(data), crc))
        sent = 0
        while sent < len(data):
            chunk = data[sent : sent + spec.MAX_CHUNK]
            self.request(spec.CMD_WRITE_DATA, struct.pack("<H", sent) + chunk)
            sent += len(chunk)
            if progress:
                progress(sent, len(data) * 2)
        # Never retry WRITE_END: the device closes the session when it succeeds.
        try:
            reply = self.request(spec.CMD_WRITE_END, timeout=8.0, retries=1)
            reported = struct.unpack("<I", reply[:4])[0] if len(reply) >= 4 else None
        except FactoryLinkError as exc:
            if isinstance(exc, FactoryStatusError):
                raise
            reported = None  # no answer: the read-back below decides
        self.verify(address, data)
        if reported is not None and reported != crc:
            raise FactoryLinkError("the board reported a different CRC than the file")
        if progress:
            progress(len(data) * 2, len(data) * 2)

    def verify(self, address: int, data: bytes) -> None:
        back = self.read(address, len(data))
        if back != data:
            first = next(
                (i for i, (a, b) in enumerate(zip(back, data, strict=False)) if a != b), len(data)
            )
            raise FactoryLinkError(f"read-back differs from the file at byte {first}")

    def diag_init(self, address: int = spec.DIAG_ADDRESS) -> bytes:
        """Write a clean fault and diagnostic map and return what the board stored."""
        reply = self.request(spec.CMD_DIAG_INIT, struct.pack("<I", address), timeout=8.0, retries=1)
        header = self.read(address, spec.FILE_HEADER_SIZE)
        magic, _fmt, _rev, length, _crc, _uid, _hcrc = spec.FILE_HEADER.unpack(header)
        if magic != spec.MAGIC_DIAG:
            raise FactoryLinkError("the board did not store a diagnostic map")
        stored = self.read(address, spec.FILE_HEADER_SIZE + length)
        if len(reply) >= 4 and struct.unpack("<I", reply[:4])[0] != files.crc32(stored):
            raise FactoryLinkError("diagnostic map CRC differs from what the board reported")
        return stored

    def selftest(self) -> list[SelfTestItem]:
        """Run the firmware's peripheral checks; nothing is written to flash."""
        body = self.request(spec.CMD_SELFTEST, timeout=5.0, retries=1)
        if not body:
            raise FactoryLinkError("SELFTEST reply is empty")
        count = body[0]
        if len(body) < 1 + count * 6:
            raise FactoryLinkError("SELFTEST reply is truncated")
        items = []
        for index in range(count):
            check, result, value = struct.unpack_from("<BBI", body, 1 + index * 6)
            items.append(SelfTestItem(check, result, value))
        return items

    def read_file(self, address: int) -> files.FactoryFile | None:
        """Read back the file stored at ``address``; None when the block is blank."""
        header = self.read(address, spec.FILE_HEADER_SIZE)
        if header == b"\xff" * spec.FILE_HEADER_SIZE:
            return None
        length = struct.unpack_from("<I", header, 8)[0]
        if length > spec.MAX_FILE_SIZE - spec.FILE_HEADER_SIZE:
            raise FactoryLinkError("the stored file header is corrupt")
        return files.parse_file(self.read(address, spec.FILE_HEADER_SIZE + length))
