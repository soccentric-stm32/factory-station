"""Flash BOOT over SWD and the factory firmware through BOOT over UART.

The steps match the bootloader repository's own flash tool: BOOT is written
with OpenOCD after the dual-bank option bytes are set, and applications are
signed with a P-256 key and sent with ``bootctl.py``.  The key never leaves
the PC and the station never generates one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hostcore import images
from hostcore.openocd import OpenOcdError, OpenOcdProcess
from hwval_factory.bench.factorylink import (
    FactoryInfo,
    FactoryLink,
    FactoryLinkError,
    SerialFactoryTransport,
)

Log = Callable[[str], None]

# Dual-bank option bytes written before BOOT: nDBANK=0, nDBOOT=1, BOR level 3,
# no write protection, RDP level 0 (0xAA), BOOT_ADD0/1 left at the ST defaults.
DUAL_BANK_OPTCR = 0x1FFFAAE4
DUAL_BANK_OPTCR1 = 0x00400080

VENDOR_MKIMAGE = Path(images.__file__).resolve().parent / "vendor" / "mkimage.py"


class FlashError(RuntimeError):
    """A flashing step failed; the message says what to do next."""


@dataclass(slots=True)
class KeyInfo:
    path: Path
    key_id: int
    private: bool

    @property
    def key_id_text(self) -> str:
        return f"0x{self.key_id:08X}"


def inspect_key(path: str | Path) -> KeyInfo:
    """Load a P-256 key (private or public PEM) and return its BOOT key id."""
    try:
        key = images.load_key(path)
    except images.ImageError as exc:
        raise FlashError(str(exc)) from exc
    return KeyInfo(
        Path(path),
        images.key_id(images.public_point(key)),
        private=hasattr(key, "private_numbers"),
    )


def bootloader_root() -> Path | None:
    """The bootloader repository: $HWVAL_BOOTLOADER, else a sibling checkout."""
    candidates: list[Path] = []
    env = os.environ.get("HWVAL_BOOTLOADER")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent.parent / "bootloader")
        candidates.append(parent / "bootloader")
    for candidate in candidates:
        if (candidate / "tools" / "bootctl.py").is_file():
            return candidate
    return None


def bootctl_path() -> Path:
    root = bootloader_root()
    if root is None:
        raise FlashError(
            "bootctl.py not found. Check out the bootloader repository next to this one "
            "or set HWVAL_BOOTLOADER to its folder."
        )
    return root / "tools" / "bootctl.py"


def run_command(args: list[str], log: Log, timeout: float = 120.0) -> str:
    log("$ " + " ".join(args))
    try:
        process = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FlashError(
            f"{Path(args[1] if len(args) > 1 else args[0]).name} timed out after {timeout:g} s"
        ) from exc
    except OSError as exc:
        raise FlashError(f"cannot run {args[0]}: {exc}") from exc
    output = (process.stdout or "") + (process.stderr or "")
    for line in output.splitlines():
        log(line)
    if process.returncode != 0:
        tail = (
            output.strip().splitlines()[-1] if output.strip() else f"exit code {process.returncode}"
        )
        raise FlashError(tail)
    return output


# -- BOOT over SWD -----------------------------------------------------


def set_dual_bank_options(backend: OpenOcdProcess) -> None:
    """Write the dual-bank option bytes and read them back."""
    try:
        backend.write_option_bytes(DUAL_BANK_OPTCR, DUAL_BANK_OPTCR1)
        backend.reset(halt=True)
        actual = backend.read_option_bytes()
    except OpenOcdError as exc:
        raise FlashError(f"option bytes could not be set: {exc}") from exc
    if actual.optcr != DUAL_BANK_OPTCR:
        raise FlashError(
            f"option-byte read-back failed: OPTCR {actual.optcr:#010x} != {DUAL_BANK_OPTCR:#010x}"
        )


def flash_boot(
    image: str | Path,
    probe_serial: str,
    log: Log,
    *,
    openocd: str = "openocd",
    set_options: bool = True,
) -> None:
    """Set the dual-bank option bytes, erase the flash and write BOOT over SWD."""
    path = Path(image)
    if not path.is_file():
        raise FlashError(f"BOOT image {path} does not exist")
    log(f"BOOT image: {path.name} ({path.stat().st_size} bytes)")
    backend = OpenOcdProcess(probe_serial, openocd=openocd, log_callback=log)
    try:
        backend.start()
        backend.halt()
        if set_options:
            log("Setting dual-bank option bytes (nDBANK=0, nDBOOT=1)")
            set_dual_bank_options(backend)
        log("Erasing the flash")
        backend.mass_erase()
        log("Writing BOOT")
        backend.program(path, verify=True)
        backend.reset(halt=False)
        log("BOOT written and verified")
    except OpenOcdError as exc:
        raise FlashError(f"SWD flashing failed: {exc}") from exc
    finally:
        backend.stop()


def swd_reset(probe_serial: str, log: Log, *, openocd: str = "openocd") -> None:
    backend = OpenOcdProcess(probe_serial, openocd=openocd, log_callback=log)
    try:
        backend.start()
        backend.reset(halt=False)
    except OpenOcdError as exc:
        raise FlashError(f"reset over SWD failed: {exc}") from exc
    finally:
        backend.stop()


# -- application over UART -----------------------------------------------


def sign_image(
    image: str | Path,
    key: str | Path,
    output: str | Path,
    log: Log,
    *,
    security_counter: int | None = None,
) -> Path:
    """Sign a wrapped ``.img`` with a P-256 key, as the bootloader tool does."""
    source = Path(image)
    if not source.is_file():
        raise FlashError(f"firmware image {source} does not exist")
    if not inspect_key(key).private:
        raise FlashError("signing needs the private key; this file holds only a public key")
    args = [
        sys.executable,
        str(VENDOR_MKIMAGE),
        "--sign",
        str(source),
        "--key",
        str(key),
        "--output",
        str(output),
    ]
    if security_counter is not None:
        args += ["--security-counter", str(security_counter)]
    run_command(args, log, timeout=60.0)
    return Path(output)


def flash_application(
    signed_image: str | Path,
    port: str,
    log: Log,
    *,
    baud: int = 115200,
    wait_timeout: float = 30.0,
) -> None:
    """Send a signed image through BOOT and activate it (slot A on a blank board)."""
    run_command(
        [
            sys.executable,
            str(bootctl_path()),
            "--port",
            port,
            "--baud",
            str(baud),
            "update",
            str(signed_image),
            "--activate",
            "--wait",
            "--wait-timeout",
            str(wait_timeout),
        ],
        log,
        timeout=wait_timeout + 240.0,
    )


def boot_key_id(port: str, log: Log, *, baud: int = 115200) -> int | None:
    """The key id BOOT trusts, from ``bootctl info --json`` (None when absent)."""
    output = run_command(
        [
            sys.executable,
            str(bootctl_path()),
            "--port",
            port,
            "--baud",
            str(baud),
            "info",
            "--json",
        ],
        log,
        timeout=60.0,
    )
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = _find_key(data)
        if found is not None:
            return found
    return None


def _find_key(node: object) -> int | None:
    if isinstance(node, dict):
        for name, value in node.items():
            if name in ("key_id", "boot_key_id", "trusted_key_id") and isinstance(
                value, (str, int)
            ):
                try:
                    return int(value, 0) if isinstance(value, str) else value
                except ValueError:
                    continue
            nested = _find_key(value)
            if nested is not None:
                return nested
    elif isinstance(node, list):
        for item in node:
            nested = _find_key(item)
            if nested is not None:
                return nested
    return None


def wait_for_factory_firmware(
    port: str,
    log: Log,
    *,
    baud: int = 115200,
    timeout: float = 25.0,
    transport_factory: Callable[[str, int], object] = SerialFactoryTransport,
) -> FactoryInfo:
    """Poll INFO until the factory firmware answers (after a reset BOOT listens first)."""
    deadline = time.monotonic() + timeout
    last = "no answer"
    while time.monotonic() < deadline:
        try:
            transport = transport_factory(port, baud)
        except FactoryLinkError as exc:
            last = str(exc)
            time.sleep(0.5)
            continue
        link = FactoryLink(transport, timeout=0.6, retries=1)  # type: ignore[arg-type]
        try:
            info = link.info()
            log(f"{info.name} {info.version}, build {info.build}, chip {info.uid_hex}")
            return info
        except FactoryLinkError as exc:
            last = str(exc)
        finally:
            link.close()
        time.sleep(0.4)
    raise FlashError(f"the factory firmware did not answer on {port} within {timeout:g} s ({last})")
