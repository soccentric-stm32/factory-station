"""Python model of the factory firmware, for the simulator and the tests.

It follows ``factory-firmware/src/factory_proto.c`` step for step; the test
suite checks it against the real C handler when that library is built.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from hostcore.protocol import codec
from hostcore.protocol.generated.hwlink.Body import Body
from hostcore.protocol.generated.hwlink.FactoryKind import FactoryKind
from hostcore.protocol.generated.hwlink.Role import Role
from hostcore.protocol.link import (
    ProtocolError,
    build_raw,
    cobs_decode,
    cobs_encode,
    decode_raw,
)
from hwval_factory.bench import files, spec

OK, BAD_COMMAND, BAD_RANGE, BAD_HEADER, BAD_CRC, BAD_UID, FLASH_ERROR, SEQUENCE = range(8)

# Raw status -> hwlink Error (protocol minor 4).
_STATUS_TO_ERROR = {
    BAD_COMMAND: 2,
    BAD_RANGE: 16,
    BAD_HEADER: 7,
    BAD_CRC: 12,
    BAD_UID: 17,
    FLASH_ERROR: 11,
    SEQUENCE: 4,
}


def _fb_error(status: int) -> int:
    return _STATUS_TO_ERROR.get(status, 2)


@dataclass(slots=True)
class _Session:
    address: int
    total: int
    crc: int
    received: int = 0
    data: bytearray = field(default_factory=lambda: bytearray(b"\xff" * spec.BLOCK_SIZE))


class SimFactoryDevice:
    def __init__(
        self,
        uid: bytes = bytes(range(1, 13)),
        name: str = spec.FACTORY_FIRMWARE_NAME,
        version: str = "0.1.0",
        build: str = "simulated",
    ) -> None:
        self.uid = uid
        self.name = name
        self.version = version
        self.build = build
        self.flash = bytearray(b"\xff" * (spec.WINDOW_END - spec.IDENTITY_BASE))
        self.fail_next_write = False
        self.session: _Session | None = None
        self.resets = 0
        # What the simulated hardware reports to SELFTEST; tests change these to inject faults.
        self.idcode = 0x1000_0000 | spec.DEVICE_ID
        self.flash_kb = spec.FLASH_SIZE_KB
        self.clock_hz = spec.CORE_CLOCK_HZ
        self.optcr = 0x0FFF_AAE4
        self.ram_fault = False

    # -- helpers ------------------------------------------------------
    def _at(self, address: int) -> int:
        return address - spec.IDENTITY_BASE

    @staticmethod
    def _status(command: int, status: int) -> bytes:
        return bytes([command | spec.REPLY_FLAG, status])

    def _write_block(self, address: int, data: bytes) -> bool:
        if self.fail_next_write:
            self.fail_next_write = False
            return False
        start = self._at(address)
        self.flash[start : start + spec.BLOCK_SIZE] = data
        return True

    @staticmethod
    def _block_ok(address: int) -> bool:
        return (
            address >= spec.WINDOW_BASE
            and address + spec.BLOCK_SIZE <= spec.WINDOW_END
            and address % spec.BLOCK_SIZE == 0
        )

    # -- commands -----------------------------------------------------
    def handle(self, request: bytes) -> bytes:
        if not request:
            return b""
        command = request[0]
        handler = {
            spec.CMD_INFO: self._info,
            spec.CMD_READ: self._read,
            spec.CMD_WRITE_BEGIN: self._begin,
            spec.CMD_WRITE_DATA: self._data,
            spec.CMD_WRITE_END: self._end,
            spec.CMD_DIAG_INIT: self._diag_init,
            spec.CMD_SELFTEST: self._selftest,
        }.get(command)
        if handler is None:
            return self._status(command, BAD_COMMAND)
        return handler(request)

    def _info(self, _request: bytes) -> bytes:
        out = bytearray(self._status(spec.CMD_INFO, OK))
        for text in (self.name, self.version, self.build):
            encoded = text.encode("ascii")
            out += bytes([len(encoded)]) + encoded
        return bytes(out + self.uid)

    def _read(self, request: bytes) -> bytes:
        if len(request) < 7:
            return self._status(spec.CMD_READ, BAD_COMMAND)
        address, length = struct.unpack_from("<IH", request, 1)
        if (
            length == 0
            or length > spec.MAX_CHUNK
            or address < spec.IDENTITY_BASE
            or address > spec.WINDOW_END - length
        ):
            return self._status(spec.CMD_READ, BAD_RANGE)
        start = self._at(address)
        return self._status(spec.CMD_READ, OK) + bytes(self.flash[start : start + length])

    def _begin(self, request: bytes) -> bytes:
        if len(request) < 11:
            return self._status(spec.CMD_WRITE_BEGIN, BAD_COMMAND)
        address, total, crc = struct.unpack_from("<IHI", request, 1)
        self.session = None
        if not self._block_ok(address) or not spec.FILE_HEADER_SIZE <= total <= spec.BLOCK_SIZE:
            return self._status(spec.CMD_WRITE_BEGIN, BAD_RANGE)
        self.session = _Session(address, total, crc)
        return self._status(spec.CMD_WRITE_BEGIN, OK)

    def _data(self, request: bytes) -> bytes:
        if len(request) < 4:
            return self._status(spec.CMD_WRITE_DATA, BAD_COMMAND)
        offset = struct.unpack_from("<H", request, 1)[0]
        chunk = request[3:]
        session = self.session
        if session is None or offset > session.received:
            return self._status(spec.CMD_WRITE_DATA, SEQUENCE)
        if len(chunk) > spec.MAX_CHUNK or offset + len(chunk) > session.total:
            return self._status(spec.CMD_WRITE_DATA, BAD_RANGE)
        session.data[offset : offset + len(chunk)] = chunk
        session.received = max(session.received, offset + len(chunk))
        return self._status(spec.CMD_WRITE_DATA, OK)

    def _check(self, data: bytes) -> int:
        magic, fmt, _rev, length, payload_crc, uid, header_crc = spec.FILE_HEADER.unpack_from(data)
        if magic not in spec.MAGIC_KIND:
            return BAD_HEADER
        if fmt != 1 or length != len(data) - spec.FILE_HEADER_SIZE:
            return BAD_HEADER
        if files.crc32(data[:28]) != header_crc:
            return BAD_HEADER
        if files.crc32(data[spec.FILE_HEADER_SIZE :]) != payload_crc:
            return BAD_CRC
        if any(uid) and uid != self.uid:
            return BAD_UID
        return OK

    def _program(self, command: int, address: int, data: bytes, total: int) -> bytes:
        if not self._write_block(address, bytes(data)):
            return self._status(command, FLASH_ERROR)
        start = self._at(address)
        if bytes(self.flash[start : start + spec.BLOCK_SIZE]) != bytes(data):
            return self._status(command, FLASH_ERROR)
        return self._status(command, OK) + struct.pack("<I", files.crc32(bytes(data[:total])))

    def _end(self, _request: bytes) -> bytes:
        session = self.session
        if session is None or session.received != session.total:
            return self._status(spec.CMD_WRITE_END, SEQUENCE)
        self.session = None
        total, data = session.total, bytes(session.data)
        if files.crc32(data[:total]) != session.crc:
            return self._status(spec.CMD_WRITE_END, BAD_CRC)
        status = self._check(data[:total])
        if status != OK:
            return self._status(spec.CMD_WRITE_END, status)
        return self._program(spec.CMD_WRITE_END, session.address, data, total)

    def _diag_init(self, request: bytes) -> bytes:
        if len(request) < 5:
            return self._status(spec.CMD_DIAG_INIT, BAD_COMMAND)
        address = struct.unpack_from("<I", request, 1)[0]
        self.session = None
        if not self._block_ok(address):
            return self._status(spec.CMD_DIAG_INIT, BAD_RANGE)
        payload = struct.pack("<BHHH", spec.DIAG_LOG_CFG, 4, 64, 0)
        clean = files.build_file("diag", payload, revision=0)  # matches factory_proto.c
        block = bytearray(b"\xff" * spec.BLOCK_SIZE)
        block[: len(clean)] = clean
        return self._program(spec.CMD_DIAG_INIT, address, bytes(block), len(clean))

    def _selftest(self, _request: bytes) -> bytes:
        """Mirror ``handle_selftest`` in factory_proto.c."""
        if self.session is not None:
            return self._status(spec.CMD_SELFTEST, SEQUENCE)
        known_crc = zlib.crc32(b"123456789") & 0xFFFFFFFF

        def verdict(ok: bool) -> int:
            return spec.RESULT_PASS if ok else spec.RESULT_FAIL

        checks = (
            (spec.CHECK_DEVICE_ID, verdict(self.idcode & 0xFFF == spec.DEVICE_ID), self.idcode),
            (spec.CHECK_FLASH_SIZE, verdict(self.flash_kb == spec.FLASH_SIZE_KB), self.flash_kb),
            (spec.CHECK_CLOCK, verdict(self.clock_hz == spec.CORE_CLOCK_HZ), self.clock_hz),
            (spec.CHECK_RAM, verdict(not self.ram_fault), spec.BLOCK_SIZE),
            (spec.CHECK_CRC, verdict(known_crc == 0xCBF43926), known_crc),
            (spec.CHECK_FLASH_READ, spec.RESULT_PASS, 0),
            (
                spec.CHECK_OPTION_BYTES,
                verdict(not self.optcr & (1 << 29) and (self.optcr >> 8) & 0xFF != 0xCC),
                self.optcr,
            ),
        )
        out = bytearray(self._status(spec.CMD_SELFTEST, OK))
        out.append(len(checks))
        for check, result, value in checks:
            out += struct.pack("<BBI", check, result, value)
        return bytes(out)

    # -- FlatBuffers path (protocol minor 4) --------------------------
    def handle_fb(self, payload: bytes, seq: int) -> bytes:
        """Answer one FACTORY FlatBuffers request; return a reply payload."""
        if not codec.verify(payload):
            return codec.build_nak(seq, 1, "malformed FlatBuffer")
        body_type = codec.body_type(payload)
        table = codec.body_table_from_payload(payload)
        if body_type == Body.Hello:
            return codec.build_hello_reply(
                role=Role.FACTORY,
                firmware_name=self.name,
                version=self.version,
                commit="sim",
                build_time=self.build,
                uid=int.from_bytes(self.uid[:8], "little"),
                dev_id=spec.DEVICE_ID,
                rev_id=0x1000,
                flash_mode=0,
                protocol_major=1,
                protocol_minor=4,
                max_frame=2048,
                detail="simulated factory firmware",
            )
        if body_type == Body.GetFactoryInfo:
            return codec.build_factory_info(
                firmware_name=self.name,
                version=self.version,
                build=self.build,
                uid=self.uid,
                flash_mode=0,
                window_base=spec.WINDOW_BASE,
                window_end=spec.WINDOW_END,
                max_chunk=spec.MAX_CHUNK,
            )
        if body_type == Body.ReadMemory:
            request = codec.parse_read_memory(table)
            reply = self._read(
                bytes([spec.CMD_READ]) + struct.pack("<IH", request["address"], request["length"])
            )
            if len(reply) < 2 or reply[1] != OK:
                return codec.build_nak(
                    seq, _fb_error(reply[1] if len(reply) >= 2 else 1), "read failed"
                )
            return codec.build_memory_data(request["address"], reply[2:])
        if body_type == Body.FactoryWriteBegin:
            request = codec.parse_factory_write_begin(table)
            if request["kind"] not in (FactoryKind.SYS, FactoryKind.CAL, FactoryKind.DIAG):
                return codec.build_nak(seq, 3, "write begin: kind")
            reply = self._begin(
                bytes([spec.CMD_WRITE_BEGIN])
                + struct.pack("<IHI", request["address"], request["total"], request["crc32"])
            )
            if reply[1] != OK:
                return codec.build_nak(seq, _fb_error(reply[1]), "write begin refused")
            return codec.build_ack(seq)
        if body_type == Body.FactoryWriteData:
            request = codec.parse_factory_write_data(table)
            reply = self._data(
                bytes([spec.CMD_WRITE_DATA]) + struct.pack("<H", request["offset"]) + request["data"]
            )
            if reply[1] != OK:
                return codec.build_nak(seq, _fb_error(reply[1]), "write data refused")
            return codec.build_ack(seq)
        if body_type == Body.FactoryWriteEnd:
            reply = self._end(bytes([spec.CMD_WRITE_END]))
            if reply[1] == OK and len(reply) >= 6:
                return codec.build_factory_write_result(
                    success=True, crc32=struct.unpack_from("<I", reply, 2)[0],
                    detail="programmed and verified",
                )
            return codec.build_factory_write_result(
                success=False, error=_fb_error(reply[1] if len(reply) >= 2 else 1),
                detail="write end failed",
            )
        if body_type == Body.FactoryDiagInit:
            request = codec.parse_factory_diag_init(table)
            reply = self._diag_init(bytes([spec.CMD_DIAG_INIT]) + struct.pack("<I", request["address"]))
            if reply[1] == OK and len(reply) >= 6:
                return codec.build_factory_write_result(
                    success=True, crc32=struct.unpack_from("<I", reply, 2)[0],
                    detail="clean diagnostic map programmed",
                )
            return codec.build_factory_write_result(
                success=False, error=_fb_error(reply[1] if len(reply) >= 2 else 1),
                detail="diag init failed",
            )
        if body_type == Body.FactorySelfTest:
            reply = self._selftest(bytes([spec.CMD_SELFTEST]))
            if reply[1] != OK:
                return codec.build_nak(seq, _fb_error(reply[1]), "self-test refused")
            count = reply[2]
            items = []
            for index in range(count):
                check, result, value = struct.unpack_from("<BBI", reply, 3 + index * 6)
                items.append((check, result, value))
            return codec.build_self_test_result(items)
        return codec.build_nak(seq, 2, f"unsupported body {body_type}")

    def handle_frame(self, raw: bytes) -> list[bytes]:
        try:
            frame = decode_raw(raw)
        except Exception:
            return []
        if frame.flags & 3:
            return []
        if raw[0] == 1:
            reply = self.handle_fb(frame.payload, frame.seq)
            return [build_raw(reply, frame.seq, 1)] if reply else []
        if raw[0] != spec.PROTO_FACTORY:
            return []
        reply = self.handle(frame.payload)
        if not reply:
            return []
        return [build_raw(reply, frame.seq, 1, spec.PROTO_FACTORY)]


class SimFactoryTransport:
    """In-process transport that talks to a SimFactoryDevice."""

    def __init__(self, device: SimFactoryDevice) -> None:
        self.device = device
        self._queue: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise ProtocolError("transport closed")
        for encoded in data.split(b"\x00"):
            if not encoded:
                continue
            self._queue.extend(self.device.handle_frame(cobs_decode(encoded)))

    def read_frame(self, timeout: float) -> bytes | None:
        return self._queue.pop(0) if self._queue else None

    def close(self) -> None:
        self.closed = True


__all__ = ["SimFactoryDevice", "SimFactoryTransport", "cobs_encode"]
