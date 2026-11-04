"""OpenOCD process control, one process per fixture.

Factory uses the OpenOCD telnet command port (FCON-003).  A process is started
for a specific ST-LINK serial (``adapter serial``) and target config, then
commands are issued over a local socket.  ``SimulatedOpenOcd`` in the Factory
package implements the same small interface for the fixture simulator.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

FLASH_OPTCR = 0x40023C14
FLASH_OPTCR1 = 0x40023C18
FLASH_KEYR = 0x40023C04
FLASH_OPTKEYR = 0x40023C08
FLASH_SR = 0x40023C0C
FLASH_CR = 0x40023C10


class OpenOcdError(RuntimeError):
    """OpenOCD failed to start, crashed or returned an error."""


@dataclass(slots=True)
class OptionBytes:
    optcr: int = 0
    optcr1: int = 0
    raw: dict[str, int] = field(default_factory=dict)

    @property
    def rdp(self) -> int:
        """Logical RDP level: 0xAA -> 0, 0x00 -> 1, 0xCC -> 2."""
        raw = (self.optcr >> 8) & 0xFF
        if raw == 0xCC:
            return 2
        if raw == 0xAA:
            return 0
        return 1

    @property
    def bor_level(self) -> int:
        """Logical BOR level; the register field is 00=off, 01=3, 10=2, 11=1."""
        return {0: 0, 1: 3, 2: 2, 3: 1}[(self.optcr >> 2) & 0x3]

    @property
    def ndbank(self) -> int:
        return (self.optcr >> 29) & 0x1

    @property
    def ndboot(self) -> int:
        return (self.optcr >> 28) & 0x1

    def bank1_wrp(self) -> int:
        return (self.optcr >> 16) & 0xFFF

    def bank2_wrp(self) -> int:
        # STM32F7 has a single nWRP[11:0] field shared by both flash banks.
        return (self.optcr >> 16) & 0xFFF


class OpenOcdBackend(Protocol):
    log: list[str]

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def command(self, command: str, timeout: float = 20.0) -> str: ...
    def halt(self) -> None: ...
    def reset(self, halt: bool = False) -> None: ...
    def mass_erase(self) -> None: ...
    def program(
        self, path: str | Path, address: int | None = None, verify: bool = True
    ) -> None: ...
    def verify_image(self, path: str | Path, address: int | None = None) -> None: ...
    def read_u32(self, address: int) -> int: ...
    def read_u16(self, address: int) -> int: ...
    def read_memory(self, address: int, length: int) -> bytes: ...
    def write_memory(self, address: int, data: bytes) -> None: ...
    def erase_address(self, address: int, length: int) -> None: ...
    def read_option_bytes(self) -> OptionBytes: ...
    def write_option_bytes(self, optcr: int, optcr1: int) -> None: ...


class OpenOcdProcess:
    """Long-running OpenOCD instance controlled over telnet."""

    def __init__(
        self,
        probe_serial: str,
        *,
        openocd: str = "openocd",
        interface_cfg: str = "interface/stlink.cfg",
        target_cfg: str = "target/stm32f7x.cfg",
        telnet_port: int = 4444,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.probe_serial = probe_serial
        self.openocd = openocd
        self.interface_cfg = interface_cfg
        self.target_cfg = target_cfg
        self.telnet_port = telnet_port
        self.log_callback = log_callback
        self.log: list[str] = []
        self._process: subprocess.Popen[str] | None = None
        self._socket: socket.socket | None = None
        self._buffer = ""

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._process is not None:
            return
        cmd = [
            self.openocd,
            "-f",
            self.interface_cfg,
            "-c",
            f"adapter serial {self.probe_serial}",
            "-f",
            self.target_cfg,
            "-c",
            f"telnet_port {self.telnet_port}",
            "-c",
            "gdb_port disabled",
            "-c",
            "tcl_port disabled",
            "-c",
            "init",
        ]
        self._log("$ " + " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise OpenOcdError(f"cannot start OpenOCD: {exc}") from exc
        self._connect_socket()
        self._drain_startup()

    def _connect_socket(self) -> None:
        deadline = time.monotonic() + 10.0
        last: OSError | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                output = self._read_process_output()
                raise OpenOcdError(f"OpenOCD exited during start-up: {output}")
            try:
                sock = socket.create_connection(("127.0.0.1", self.telnet_port), timeout=1.0)
                sock.settimeout(10.0)
                self._socket = sock
                return
            except OSError as exc:
                last = exc
                time.sleep(0.1)
        raise OpenOcdError(f"OpenOCD telnet port {self.telnet_port} did not open: {last}")

    def _read_process_output(self) -> str:
        if self._process is None or self._process.stdout is None:
            return ""
        try:
            return self._process.stdout.read()
        except Exception:
            return ""

    def _drain_startup(self) -> None:
        with contextlib.suppress(OpenOcdError):
            self._read_until_prompt(2.0)

    def stop(self) -> None:
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.sendall(b"shutdown\n")
            self._socket.close()
            self._socket = None
        if self._process is not None:
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None

    close = stop

    def __enter__(self) -> OpenOcdProcess:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- command channel ----------------------------------------------
    def _log(self, text: str) -> None:
        text = text.rstrip()
        if not text:
            return
        self.log.append(text)
        if len(self.log) > 2000:
            del self.log[:1000]
        if self.log_callback:
            self.log_callback(text)

    def _read_until_prompt(self, timeout: float) -> str:
        if self._socket is None:
            raise OpenOcdError("OpenOCD is not started")
        deadline = time.monotonic() + timeout
        data = self._buffer
        while "> " not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._buffer = data
                raise OpenOcdError(f"OpenOCD timeout; output: {data[-500:]}")
            self._socket.settimeout(remaining)
            try:
                chunk = self._socket.recv(65536)
            except TimeoutError as exc:
                raise OpenOcdError("OpenOCD command timeout") from exc
            if not chunk:
                raise OpenOcdError("OpenOCD closed the telnet connection")
            decoded = chunk.decode("utf-8", "replace")
            data += decoded
        output, _, rest = data.partition("> ")
        self._buffer = rest
        clean = "\n".join(line for line in output.splitlines() if not line.startswith(">"))
        self._log(clean)
        return clean

    def command(self, command: str, timeout: float = 20.0) -> str:
        if self._process is not None and self._process.poll() is not None:
            raise OpenOcdError("OpenOCD process has exited")
        if self._socket is None:
            raise OpenOcdError("OpenOCD is not started")
        self._log("> " + command)
        try:
            self._socket.sendall((command + "\n").encode("utf-8"))
        except OSError as exc:
            raise OpenOcdError(f"OpenOCD write failed: {exc}") from exc
        output = self._read_until_prompt(timeout)
        lowered = output.lower()
        if "error:" in lowered or "failed" in lowered:
            raise OpenOcdError(output.strip())
        return output

    # -- high-level operations ---------------------------------------
    def halt(self) -> None:
        self.command("halt")

    def reset(self, halt: bool = False) -> None:
        self.command("reset halt" if halt else "reset run")

    def mass_erase(self) -> None:
        self.halt()
        # F767ZI has 2 MB of flash; erasing by address covers both banks and
        # works whether nDBANK is 0 or 1.  Fall back to per-bank sector erase
        # on OpenOCD versions that do not accept erase_address.
        try:
            self.command("flash erase_address 0x08000000 0x00200000", timeout=120.0)
        except OpenOcdError:
            self.command("flash erase_sector 0 0 last", timeout=60.0)
            with contextlib.suppress(OpenOcdError):
                self.command("flash erase_sector 1 0 last", timeout=60.0)

    def program(
        self,
        path: str | Path,
        address: int | None = None,
        verify: bool = True,
    ) -> None:
        args = f'"{path}"'
        if address is not None:
            args += f" 0x{address:08X}"
        if verify:
            args += " verify"
        args += " reset"
        self.command(f"program {args}", timeout=120.0)

    def verify_image(self, path: str | Path, address: int | None = None) -> None:
        args = f'verify_image "{path}"'
        if address is not None:
            args += f" 0x{address:08X}"
        self.command(args, timeout=120.0)

    def read_u32(self, address: int) -> int:
        output = self.command(f"mdw 0x{address:08X} 1")
        match = re.search(r"0x[0-9a-fA-F]+:\s*(0x[0-9a-fA-F]+)", output)
        if not match:
            raise OpenOcdError(f"cannot parse mdw output: {output}")
        return int(match.group(1), 16)

    def read_u16(self, address: int) -> int:
        # The flash-size register is 16-bit and halfword aligned; mdw at
        # 0x1FF0F442 is not word aligned.
        output = self.command(f"mdh 0x{address:08X} 1")
        match = re.search(r"0x[0-9a-fA-F]+:\s*(0x[0-9a-fA-F]+)", output)
        if not match:
            raise OpenOcdError(f"cannot parse mdh output: {output}")
        return int(match.group(1), 16)

    def read_memory(self, address: int, length: int) -> bytes:
        if length <= 0:
            return b""
        # Prefer dump_image to a temporary file: it preserves arbitrary bytes
        # and works for flash, OTP and SRAM regions alike.
        return self._dump_image(address, length)

    def erase_address(self, address: int, length: int) -> None:
        """Erase every flash sector that overlaps ``[address, address+length)``."""
        if length <= 0:
            return
        self.command(f"flash erase_address 0x{address:08X} 0x{length:08X}", timeout=120.0)

    def _dump_image(self, address: int, length: int) -> bytes:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
            name = handle.name
        try:
            self.command(
                f'dump_image "{name}" 0x{address:08X} 0x{length:08X}',
                timeout=60.0,
            )
            return Path(name).read_bytes()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(name)

    def write_memory(self, address: int, data: bytes) -> None:
        """Program bytes with the OpenOCD flash driver, then verify them.

        ``load_image`` writes target memory (SRAM/peripheral space), not flash,
        so it must never be used for metadata or the identity record.  The
        caller is responsible for erasing the containing flash sector first;
        this keeps append-only metadata writes from erasing earlier records.
        """
        import tempfile

        if not data:
            return
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
            handle.write(data)
            name = handle.name
        try:
            if 0x0800_0000 <= address < 0x0820_0000:
                self.command(
                    f'flash write_image "{name}" 0x{address:08X} bin',
                    timeout=120.0,
                )
                self.verify_image(name, address)
            else:
                # OTP / backup registers / SRAM: not reachable by the flash
                # driver.  load_image is correct for this address space.
                self.command(f'load_image "{name}" 0x{address:08X} bin', timeout=120.0)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(name)

    def read_option_bytes(self) -> OptionBytes:
        optcr = self.read_u32(FLASH_OPTCR)
        optcr1 = self.read_u32(FLASH_OPTCR1)
        return OptionBytes(
            optcr=optcr,
            optcr1=optcr1,
            raw={"OPTCR": optcr, "OPTCR1": optcr1},
        )

    def write_option_bytes(self, optcr: int, optcr1: int) -> None:
        """Program option bytes with OpenOCD's real F7 argument list.

        ``stm32f2x options_write <bank> <user_options> <boot_addr0>
        <boot_addr1>`` is the documented form (provision.sh, OPEN-B05).  The
        raw OPTCR value carries nDBANK/nDBOOT/WRP/RDP/BOR; the two boot
        addresses live in OPTCR1.  RDP level 2 is rejected by the caller and
        again here.
        """
        if ((optcr >> 8) & 0xFF) == 0xCC:
            raise OpenOcdError("RDP level 2 is permanent and is never permitted")
        boot0 = optcr1 & 0xFFFF
        boot1 = (optcr1 >> 16) & 0xFFFF
        try:
            self.command(
                f"stm32f2x options_write 0 0x{optcr:08X} 0x{boot0:04X} 0x{boot1:04X}",
                timeout=30.0,
            )
        except OpenOcdError:
            self._write_option_bytes_by_register(optcr, optcr1)
        self.reset(halt=True)

    def _write_option_bytes_by_register(self, optcr: int, optcr1: int) -> None:
        """Register fallback for OpenOCD builds without options_write."""
        self.halt()
        self.command(f"mww 0x{FLASH_OPTKEYR:08X} 0x08192A3B")
        self.command(f"mww 0x{FLASH_OPTKEYR:08X} 0x4C5D6E7F")
        self.command(f"mww 0x{FLASH_OPTCR1:08X} 0x{optcr1:08X}")
        self.command(f"mww 0x{FLASH_OPTCR:08X} 0x{optcr & ~0x1:08X}")
        # OPTSTRT is bit 1 in OPTCR; bit 0 is OPTLOCK.
        self.command(f"mww 0x{FLASH_OPTCR:08X} 0x{(optcr & ~0x1) | 0x2:08X}")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not (self.read_u32(FLASH_SR) & 0x10000):
                break
            time.sleep(0.05)
        self.command(f"mww 0x{FLASH_OPTCR:08X} 0x{optcr | 0x1:08X}")


def option_bytes_to_dict(option_bytes: OptionBytes) -> dict[str, int]:
    return {
        "OPTCR": option_bytes.optcr,
        "OPTCR1": option_bytes.optcr1,
        "RDP": option_bytes.rdp,
        "BOR_LEV": option_bytes.bor_level,
        "nDBANK": option_bytes.ndbank,
        "nDBOOT": option_bytes.ndboot,
        "WRP_BANK1": option_bytes.bank1_wrp(),
        "WRP_BANK2": option_bytes.bank2_wrp(),
    }


def format_command_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)
