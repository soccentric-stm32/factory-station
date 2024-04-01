"""The eight station actions, with no Qt widgets.

Every action has the same shape::

    run(state, progress, cancel) -> ActionResult

``state`` is :class:`hwval_factory.ui.bench.state.BenchState` (or any object
with the same fields), ``progress(percent, text)`` is called from the worker
thread and ``cancel`` is a ``threading.Event``.  The Home page wraps these in
a Qt worker; the tests call them directly against the simulated board.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hwval_factory.bench import detect as detect_mod
from hwval_factory.bench import flashing, validation
from hwval_factory.bench.factoryclient import FactoryClient, FactoryInfo, FactoryLinkError
from hwval_factory.core import generators, service, spec
from hwval_factory.core.rules import Rules
from hwval_factory.core.units import Registry, RegistryError

Progress = Callable[[int, str], None]
Log = Callable[[str], None]


@dataclass
class ActionResult:
    ok: bool
    summary: str
    details: str = ""
    artifacts: list[Path] = field(default_factory=list)
    status: str = "ok"  # ok, failed, blocked, stale

    def text(self) -> str:
        return self.summary + (f"\n\n{self.details}" if self.details else "")


class ActionError(RuntimeError):
    """An action cannot run or failed; the message is shown to the operator."""


def _say(progress: Progress | None, percent: int, text: str) -> None:
    if progress:
        progress(max(0, min(100, percent)), text)


def _join(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def _client(state: Any, log: Log | None = None) -> FactoryClient:
    link = state.open_link()
    if isinstance(link, FactoryClient):
        if log is not None:
            link.session.log_handler = log
        return link
    raise ActionError("the bench state returned an unsupported link")  # pragma: no cover


def _connect(client: FactoryClient, state: Any, progress: Progress | None) -> FactoryInfo:
    _say(progress, 5, "Connecting to the board")
    client.connect(timeout=8.0 if not state.simulate else 0.5)
    info = client.info()
    state.set_info(info)
    return info


# --------------------------------------------------------------------------
# 1 Detect


def detect(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    _say(progress, 10, "Listing ST-LINK probes and serial ports")
    result: detect_mod.DetectResult = state.refresh_detection()
    probes = ", ".join(probe.label for probe in result.probes) or "none"
    ports = ", ".join(port.label for port in result.ports) or "none"
    if not result.probes:
        return ActionResult(False, "No ST-LINK probe found", f"Serial ports: {ports}", status="failed")
    if not result.ports:
        return ActionResult(False, "No UART port found", f"Probes: {probes}", status="failed")
    if not state.probe_serial or not state.port:
        return ActionResult(
            False,
            "Choose one probe and one UART port",
            f"Probes: {probes}\nPorts: {ports}",
            status="blocked",
        )
    _say(progress, 100, "Detect complete")
    return ActionResult(
        True,
        f"ST-LINK {state.probe_serial} on {state.port}",
        f"Probes: {probes}\nPorts: {ports}",
    )


# --------------------------------------------------------------------------
# 2 Load bootloader / 3 factory firmware


def load_bootloader(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    if not state.probe_serial:
        return ActionResult(False, "Detect the ST-LINK first", status="blocked")
    image = Path(state.boot_image)
    if not state.simulate and not image.is_file():
        return ActionResult(False, "BOOT image is missing", str(image), status="blocked")
    if state.simulate:
        state.sim_boot_flashed = True
        _say(progress, 100, "Simulated BOOT flashed")
        return ActionResult(True, "BOOT programmed (simulated)", "Slot A prepared")

    log: list[str] = []

    def say(text: str) -> None:
        log.append(text)
        _say(progress, 50, text)

    _say(progress, 5, f"Programming {image.name} over SWD")
    try:
        flashing.flash_boot(image, state.probe_serial, say)
    except flashing.FlashError as exc:
        return ActionResult(False, "BOOT flashing failed", _join(str(exc), *log), status="failed")
    state.sim_boot_flashed = True
    _say(progress, 100, "BOOT programmed and verified")
    return ActionResult(True, "BOOT programmed and verified", _join(*log))


def load_factory_firmware(
    state: Any, progress: Progress | None = None, cancel: Any = None
) -> ActionResult:
    if not state.sim_boot_flashed:
        return ActionResult(False, "Load the bootloader first", status="blocked")
    if not state.port:
        return ActionResult(False, "Choose a UART port first", status="blocked")
    if state.simulate:
        _say(progress, 100, "Simulated factory firmware in slot A")
        return ActionResult(True, "Factory firmware in slot A (simulated)", status="ok")

    image = Path(state.factory_image)
    key = Path(state.key_path)
    if not image.is_file():
        return ActionResult(False, "Factory image is missing", str(image), status="blocked")
    if not key.is_file():
        return ActionResult(False, "Signing key is missing", str(key), status="blocked")

    log: list[str] = []

    def say(text: str) -> None:
        log.append(text)
        _say(progress, 35, text)

    signed = Path(state.data_dir) / "factory-signed.img"
    try:
        _say(progress, 10, "Signing the factory image")
        flashing.sign_image(image, key, signed, say, security_counter=state.security_counter)
        _say(progress, 45, "Sending the image through BOOT")
        flashing.flash_application(signed, state.port, say)
        _say(progress, 80, "Waiting for the factory firmware")
        info = flashing.wait_for_factory_firmware(state.port, say)
    except flashing.FlashError as exc:
        return ActionResult(False, "Factory firmware upload failed", _join(str(exc), *log), status="failed")
    state.set_info(info)
    _say(progress, 100, "Factory firmware is running")
    return ActionResult(
        True,
        f"{info.name} {info.version} in slot A",
        _join(*log),
        artifacts=[signed],
    )


# --------------------------------------------------------------------------
# 4 Device info / 5-7 data blocks


def device_info(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    client = _client(state)
    try:
        info = _connect(client, state, progress)
        blocks = []
        for kind in ("sys", "cal", "diag"):
            _say(progress, 30 + {"sys": 0, "cal": 20, "diag": 40}[kind], f"Reading .{kind}")
            try:
                parsed = client.read_file(spec.KIND_ADDRESS[kind])
            except (FactoryLinkError, ValueError) as exc:
                blocks.append(f".{kind}: unreadable ({exc})")
                continue
            if parsed is None:
                blocks.append(f".{kind}: blank")
            else:
                bound = "bound to this chip" if parsed.bound and parsed.uid == info.uid else (
                    "bound to another chip" if parsed.bound else "unbound"
                )
                blocks.append(f".{kind}: revision {parsed.revision}, {len(parsed.items)} item(s), {bound}")
        details = _join(
            f"Firmware: {info.name} {info.version} ({info.build})",
            f"Chip UID: {info.uid_hex}",
            *blocks,
        )
        _say(progress, 100, "Device info read")
        return ActionResult(True, f"{info.name} on {info.uid_hex}", details)
    finally:
        client.close()


def _csv_path(state: Any, kind: str) -> Path:
    candidates = [state.workspace.csv_path_path(kind)]
    if kind == "sys":
        candidates += [Path(state.csv_path), Path(state.data_dir) / "factory.csv"]
    for path in candidates:
        if Path(path).is_file():
            return Path(path)
    raise ActionError(f"no {kind}.csv in the artifacts repository or the bench folder")


def _rules(state: Any) -> Rules | None:
    path = state.workspace.rules_path
    try:
        return Rules.load(path) if path.is_file() else None
    except Exception:
        return None


def _registry(state: Any) -> Registry:
    return Registry.load(state.workspace.units_path)


def _build_block(state: Any, kind: str, uid: bytes | None, progress: Progress | None) -> tuple[bytes, Path]:
    gen = generators.GENERATORS[kind]
    data = gen.load(_csv_path(state, kind))
    _say(progress, 15, f"Checking {gen.suffix} records")
    issues = service.check(gen, data, uid=uid, rules=_rules(state), registry=_registry(state))
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        text = _join(*(f"line {issue.line}: {issue.message}" for issue in errors[:8]))
        raise ActionError(text)
    unit = state.unit_id
    if unit not in data.groups:
        raise ActionError(f"{unit!r} is not in {Path(data.path).name}")
    try:
        blob = gen.build(data, unit, uid=uid)
    except generators.CsvError as exc:
        raise ActionError(str(exc)) from exc
    out_dir = state.workspace.out_dir_path
    name = gen.filename(unit)
    _say(progress, 30, f"Writing {name}")
    _written, problems = service.write(out_dir, {name: blob}, kind)
    if problems:
        raise ActionError(_join(*problems))
    return blob, out_dir / name


def write_block(
    kind: str, state: Any, progress: Progress | None = None, cancel: Any = None
) -> ActionResult:
    if not state.unit_id:
        return ActionResult(False, "Select a unit first", status="blocked")
    client = _client(state)
    try:
        info = _connect(client, state, progress)
        try:
            blob, path = _build_block(state, kind, info.uid, progress)
        except (ActionError, generators.CsvError) as exc:
            return ActionResult(False, f".{kind} generation failed", str(exc), status="failed")
        _say(progress, 40, f"Writing {path.name} to 0x{spec.KIND_ADDRESS[kind]:08X}")
        try:
            client.write_file(spec.KIND_ADDRESS[kind], blob, lambda done, total: _say(
                progress, 40 + (40 * done // max(1, total)), f"Writing .{kind} {done}/{total}"
            ))
        except FactoryLinkError as exc:
            return ActionResult(False, f".{kind} write failed", str(exc), [path], "failed")
        if kind == "sys":
            try:
                registry = _registry(state)
                sys_data = generators.GENERATORS["sys"].load(_csv_path(state, "sys"))
                service.register(
                    registry,
                    sys_data,
                    {state.unit_id: blob},
                    product=state.workspace.product,
                    batch=state.workspace.batch,
                )
                record = sys_data.groups.get(state.unit_id, [{}])[0]
                serial = record.get("serial", "")
                if serial and serial in registry.units:
                    registry.set_status(serial, "programmed", "written by the factory station")
                registry.save()
            except (RegistryError, generators.CsvError):
                pass
        _say(progress, 100, f".{kind} written and verified")
        return ActionResult(
            True,
            f".{kind} written and verified for {state.unit_id}",
            f"Address: 0x{spec.KIND_ADDRESS[kind]:08X}\nFile: {path}",
            [path],
        )
    finally:
        client.close()


def system_info(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    return write_block("sys", state, progress, cancel)


def calibration(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    return write_block("cal", state, progress, cancel)


def diagnostics(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    return write_block("diag", state, progress, cancel)


# --------------------------------------------------------------------------
# 8 Validate


def validate(state: Any, progress: Progress | None = None, cancel: Any = None) -> ActionResult:
    client = _client(state)
    try:
        info = _connect(client, state, progress)
        _say(progress, 25, "Reading the three data blocks and running the self-test")
        try:
            report = validation.run_validation(
                client, lambda text: _say(progress, 60, text)
            )
        except (FactoryLinkError, ValueError) as exc:
            return ActionResult(False, "Validation failed", str(exc), status="failed")
        state.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = state.report_dir / f"{state.unit_id or info.uid_hex}.txt"
        report_path.write_text(report.text(f"{info.name} {info.version} · {info.uid_hex}"), encoding="utf-8")
        if report.verdict == validation.PASS:
            status = "verified"
            try:
                registry = _registry(state)
                sys_data = generators.GENERATORS["sys"].load(_csv_path(state, "sys"))
                record = sys_data.groups.get(state.unit_id, [{}])[0]
                serial = record.get("serial", "")
                if serial:
                    registry.set_status(serial, status, "validation passed")
                    registry.save()
            except (RegistryError, generators.CsvError):
                pass
            result = ActionResult(
                True,
                f"Validation PASS ({report.summary()})",
                report.text(),
                [report_path],
            )
        elif report.verdict == validation.WARN:
            result = ActionResult(
                False,
                f"Unit incomplete ({report.summary()})",
                report.text(),
                [report_path],
                status="failed",
            )
        else:
            result = ActionResult(
                False,
                f"Validation FAIL ({report.summary()})",
                report.text(),
                [report_path],
                status="failed",
            )
        _say(progress, 100, result.summary)
        return result
    finally:
        client.close()


ACTIONS: tuple[tuple[int, str, Callable[..., ActionResult]], ...] = (
    (1, "Detect", detect),
    (2, "Load bootloader", load_bootloader),
    (3, "Load factory firmware", load_factory_firmware),
    (4, "Device info", device_info),
    (5, "System info", system_info),
    (6, "Calibration", calibration),
    (7, "Diagnostics", diagnostics),
    (8, "Validate", validate),
)


# --------------------------------------------------------------------------
# Runner (threading only; the Home page adapts it to Qt signals)


class TaskRunner:
    """Run one action at a time on a worker thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def start(
        self,
        action: Callable[..., ActionResult],
        state: Any,
        on_progress: Callable[[int, str], None],
        on_done: Callable[[ActionResult], None],
    ) -> bool:
        if self.running:
            return False
        self._cancel.clear()

        def work() -> None:
            try:
                result = action(state, on_progress, self._cancel)
            except Exception as exc:
                result = ActionResult(False, "Action failed", str(exc), status="failed")
            on_done(result)

        self._thread = threading.Thread(target=work, name="factory-action", daemon=True)
        self._thread.start()
        return True


__all__ = [
    "ACTIONS",
    "ActionError",
    "ActionResult",
    "TaskRunner",
    "calibration",
    "detect",
    "device_info",
    "diagnostics",
    "load_bootloader",
    "load_factory_firmware",
    "system_info",
    "validate",
    "write_block",
]
