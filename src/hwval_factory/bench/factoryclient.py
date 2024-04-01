"""FlatBuffers client for the factory firmware (protocol minor 4).

The station talks to the board through the shared :class:`HwLinkSession`:
COBS + CRC-32 framing and HWLINK FlatBuffers.  The legacy ``proto=0x46`` raw
command client is kept only for boards flashed before the rewrite; the UI and
the validation code use this client.

Method names and return types match the old ``FactoryLink`` so the pages,
validation and action layer did not have to change shape.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from hostcore.hwlink import HwLinkError, HwLinkSession, SerialTransport
from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.Body import Body
from hostcore.protocol.generated.hwlink.FactoryKind import FactoryKind
from hostcore.protocol.link import LinkError, NakError, error_name
from hwval_factory.bench import files, spec

Progress = Callable[[int, int], None]


class FactoryLinkError(RuntimeError):
    """A factory command failed or the device did not answer."""


class FactoryStatusError(FactoryLinkError):
    def __init__(self, command: int, status: int, detail: str = "") -> None:
        self.command = command
        self.status = status
        message = error_name(status) if detail == "" else f"{error_name(status)}: {detail}"
        super().__init__(f"device refused the command: {message}")


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
    check: int
    result: int
    value: int


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read_frame(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


class FactoryClient:
    """Synchronous factory protocol client over one HWLINK session."""

    def __init__(
        self,
        transport: Transport | HwLinkSession,
        timeout: float = 1.0,
        retries: int = 3,
        prompt_handler: Callable[[dict[str, object]], str] | None = None,
        log_handler: Callable[[str], None] | None = None,
    ) -> None:
        if isinstance(transport, HwLinkSession):
            self.session = transport
            self._owns_session = False
        else:
            self.session = HwLinkSession(
                transport,
                prompt_handler=prompt_handler,
                log_handler=log_handler,
                timeout=timeout,
                retries=retries,
            )
            self._owns_session = True
        self.timeout = timeout
        self.retries = retries

    # -- lifecycle -----------------------------------------------------
    def connect(self, *, timeout: float | None = None) -> dict[str, object]:
        try:
            return self.session.connect("FACTORY", timeout=timeout)
        except (HwLinkError, LinkError) as exc:
            raise FactoryLinkError(str(exc)) from exc

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> FactoryClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- request helpers ----------------------------------------------
    def _request(
        self,
        payload: bytes,
        response_types: set[int],
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> tuple[int, object]:
        try:
            return self.session.request(
                payload,
                response_types=response_types,
                timeout=self.timeout if timeout is None else timeout,
                retries=self.retries if retries is None else retries,
            )
        except NakError as exc:
            raise FactoryStatusError(0, int(exc.code), str(exc.detail)) from exc
        except LinkError as exc:
            raise FactoryLinkError(str(exc)) from exc

    def _ack(self, payload: bytes, *, timeout: float | None = None) -> None:
        try:
            self.session.request(
                payload,
                response_types={Body.Ack},
                timeout=self.timeout if timeout is None else timeout,
            )
        except NakError as exc:
            raise FactoryStatusError(0, int(exc.code), str(exc.detail)) from exc
        except LinkError as exc:
            raise FactoryLinkError(str(exc)) from exc

    # -- commands ------------------------------------------------------
    def info(self) -> FactoryInfo:
        _, table = self._request(codec.build_get_factory_info(), {Body.FactoryInfo})
        data = codec.parse_factory_info(table)
        uid = bytes(data["uid"])
        if len(uid) != 12:
            raise FactoryLinkError(f"INFO returned {len(uid)} UID bytes, expected 12")
        return FactoryInfo(data["firmware_name"], data["version"], data["build"], uid)

    def read(self, address: int, length: int, progress: Progress | None = None) -> bytes:
        out = bytearray()
        while len(out) < length:
            count = min(spec.MAX_CHUNK, length - len(out))
            _, table = self._request(
                codec.build_read_memory(address + len(out), count), {Body.MemoryData}
            )
            data = codec.parse_memory_data(table)
            if int(data["address"]) != address + len(out):
                raise FactoryLinkError("the board answered for another address")
            chunk = bytes(data["data"])
            if len(chunk) != count:
                raise FactoryLinkError(f"the board returned {len(chunk)} bytes, expected {count}")
            out += chunk
            if progress:
                progress(len(out), length)
        return bytes(out)

    @staticmethod
    def _kind_for(data: bytes) -> int:
        if len(data) < 4:
            raise FactoryLinkError("file is shorter than its magic")
        magic = struct.unpack_from("<I", data)[0]
        for kind, value in (
            (FactoryKind.SYS, spec.MAGIC_SYS),
            (FactoryKind.CAL, spec.MAGIC_CAL),
            (FactoryKind.DIAG, spec.MAGIC_DIAG),
        ):
            if magic == value:
                return int(kind)
        raise FactoryLinkError(f"unknown file magic {magic:#010x}")

    def write_file(self, address: int, data: bytes, progress: Progress | None = None) -> None:
        """Send one validated file to ``address`` and confirm it by reading it back."""
        if len(data) > spec.MAX_FILE_SIZE:
            raise FactoryLinkError(f"file is {len(data)} bytes; the limit is {spec.MAX_FILE_SIZE}")
        crc = files.crc32(data)
        kind = self._kind_for(data)
        self._ack(
            codec.build_factory_write_begin(kind, address, len(data), crc),
            timeout=max(self.timeout, 3.0),
        )
        sent = 0
        while sent < len(data):
            chunk = data[sent : sent + spec.MAX_CHUNK]
            self._ack(codec.build_factory_write_data(sent, chunk), timeout=max(self.timeout, 10.0))
            sent += len(chunk)
            if progress:
                progress(sent, len(data) * 2)

        # Never retry WRITE_END: the device closes the session when it succeeds.
        reported: int | None = None
        try:
            _, table = self._request(
                codec.build_factory_write_end(),
                {Body.FactoryWriteResult},
                timeout=8.0,
                retries=0,
            )
            result = codec.parse_factory_write_result(table)
            if not result["success"]:
                raise FactoryStatusError(0, int(result["error"]), str(result["detail"]))
            reported = int(result["crc32"])
        except FactoryStatusError:
            raise
        except FactoryLinkError:
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
        _, table = self._request(
            codec.build_factory_diag_init(address),
            {Body.FactoryWriteResult},
            timeout=8.0,
            retries=1,
        )
        result = codec.parse_factory_write_result(table)
        if not result["success"]:
            raise FactoryStatusError(0, int(result["error"]), str(result["detail"]))
        header = self.read(address, spec.FILE_HEADER_SIZE)
        magic, _fmt, _rev, length, _crc, _uid, _hcrc = spec.FILE_HEADER.unpack(header)
        if magic != spec.MAGIC_DIAG:
            raise FactoryLinkError("the board did not store a diagnostic map")
        stored = self.read(address, spec.FILE_HEADER_SIZE + length)
        if int(result["crc32"]) != files.crc32(stored):
            raise FactoryLinkError("diagnostic map CRC differs from what the board reported")
        return stored

    def selftest(self) -> list[SelfTestItem]:
        """Run the firmware's peripheral checks; nothing is written to flash."""
        _, table = self._request(
            codec.build_factory_self_test(), {Body.SelfTestResult}, timeout=5.0, retries=1
        )
        data = codec.parse_self_test_result(table)
        return [
            SelfTestItem(int(item["check"]), int(item["result"]), int(item["value"]))
            for item in data["items"]
        ]

    def read_file(self, address: int) -> files.FactoryFile | None:
        """Read back the file stored at ``address``; None when the block is blank."""
        header = self.read(address, spec.FILE_HEADER_SIZE)
        if header == b"\xff" * spec.FILE_HEADER_SIZE:
            return None
        length = struct.unpack_from("<I", header, 8)[0]
        if length > spec.MAX_FILE_SIZE - spec.FILE_HEADER_SIZE:
            raise FactoryLinkError("the stored file header is corrupt")
        return files.parse_file(self.read(address, spec.FILE_HEADER_SIZE + length))


__all__ = [
    "FactoryClient",
    "FactoryInfo",
    "FactoryLinkError",
    "FactoryStatusError",
    "SelfTestItem",
    "SerialFactoryTransport",
]

# Old names used by the flashing and validation modules.
SerialFactoryTransport = SerialTransport
FactoryLink = FactoryClient
