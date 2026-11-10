"""Standalone station tools built on the same steps and ports (P1-06).

Each function is a step run on its own: it opens only the hardware it needs,
writes an audit record and returns a JSON-serialisable result.  The full run
still goes through ``pipeline.py``; these functions keep the CLI and the
Tools page from duplicating protocol or flash logic.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from hostcore import layout
from hostcore.boot import wait_for_application, wait_for_boot
from hostcore.images import parse_image
from hostcore.openocd import OpenOcdError, option_bytes_to_dict
from hwval_factory.debuglog import create_debug_log
from hwval_factory.identity import (
    ProvisioningData,
    RecordError,
    build_record,
    now_utc,
    parse_record,
)
from hwval_factory.models import Role
from hwval_factory.profile import ProductProfile
from hwval_factory.protection import (
    apply_protection,
    provisioning_address,
)
from hwval_factory.recovery import RecoveryService

DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FF0F442
UID_BASE = 0x1FF0F420


class ToolError(RuntimeError):
    """A standalone tool could not complete."""


@dataclass(slots=True)
class ToolResult:
    tool: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "detail": self.detail,
            **self.data,
        }


class ToolRunner:
    TOOL_ROLES: ClassVar[dict[str, Role]] = {
        "probe": Role.OPERATOR,
        "identify": Role.OPERATOR,
        "erase": Role.SUPERVISOR,
        "program-boot": Role.ENGINEER,
        "program-factory": Role.ENGINEER,
        "load-app": Role.ENGINEER,
        "test": Role.OPERATOR,
        "identity": Role.ENGINEER,
        "boot-check": Role.OPERATOR,
        "protect": Role.SUPERVISOR,
        "recover": Role.SUPERVISOR,
        "capture-log": Role.OPERATOR,
    }

    def __init__(self, station: Any, operator: str = "operator") -> None:
        self.station = station
        self.operator = operator
        self.profile: ProductProfile = station.require_profile()

    # -- permission and audit -----------------------------------------
    def check_role(self, tool: str, role: Role) -> None:
        required = self.TOOL_ROLES.get(tool, Role.SUPERVISOR)
        if role.rank < required.rank:
            raise ToolError(f"{tool} requires {required.value} or higher (current {role.value})")

    def _audit(self, tool: str, fixture: str, detail: str) -> None:
        self.station.db.audit(self.operator, f"tool.{tool}", f"fixture={fixture} {detail}")

    def _result(self, tool: str, fixture: str, detail: str, **data: Any) -> ToolResult:
        self._audit(tool, fixture, detail)
        self.station.db.audit(
            self.operator,
            "tool.finish",
            f"fixture={fixture} tool={tool} ok=True",
        )
        return ToolResult(tool=tool, ok=True, detail=detail, data=data)

    # -- hardware helpers ---------------------------------------------
    @contextlib.contextmanager
    def _backend(self, fixture: str, *, connect_under_reset: bool | None = None):
        backend = self.station._open_backend(
            fixture, connect_under_reset=connect_under_reset
        )
        try:
            backend.start()
            yield backend
        finally:
            with contextlib.suppress(Exception):
                backend.stop()

    def _runtime(self, fixture: str, backend: Any) -> Any:
        class Runtime:
            pass

        runtime = Runtime()
        runtime.backend = backend
        runtime.session = None
        runtime.bank = None
        if self.station.simulation and self.station.simulator is not None:
            runtime.bank = self.station.simulator.bank(fixture)
            runtime.bank.power_cycle()
        return runtime

    @contextlib.contextmanager
    def _session(self, fixture: str, backend: Any):
        runtime = self._runtime(fixture, backend)
        session = self.station._open_session(fixture, runtime)
        try:
            yield session
        finally:
            with contextlib.suppress(Exception):
                session.close()

    def _image(self, path: Any, *, signed: bool = False) -> Any:
        key = self.profile.signing_public_key_path
        return parse_image(
            path,
            public_key=key if signed and key is not None else None,
            require_signature=signed,
        )

    # -- tools ---------------------------------------------------------
    def probe(self, fixture: str, **_: Any) -> ToolResult:
        checks = self.station.probe_fixture(fixture)
        ok = all(bool(check.get("ok")) for check in checks)
        detail = "; ".join(
            f"{check.get('name')}: {'PASS' if check.get('ok') else 'FAIL'}"
            for check in checks
        )
        return ToolResult(
            tool="probe",
            ok=ok,
            detail=detail,
            data={"checks": checks},
        )

    def identify(self, fixture: str, **_: Any) -> ToolResult:
        with self._backend(fixture) as backend:
            backend.halt()
            idcode = backend.read_u32(DBGMCU_IDCODE)
            flash_kb = backend.read_u16(FLASH_SIZE_REG)
            uid = backend.read_memory(UID_BASE, 12).hex().upper()
            options = backend.read_option_bytes()
            floor = self.station._secure_floor(backend)
            accepted = self.profile.parts.accepts(
                idcode & 0xFFF, (idcode >> 16) & 0xFFFF, flash_kb
            )
        if options.rdp == 2:
            raise ToolError("chip is RDP level 2 (permanent); no recovery is possible")
        data = {
            "dev_id": idcode & 0xFFF,
            "rev_id": (idcode >> 16) & 0xFFFF,
            "flash_kb": flash_kb,
            "uid": uid,
            "rdp": options.rdp,
            "option_bytes": option_bytes_to_dict(options),
            "security_floor": floor,
            "matches_profile": accepted,
        }
        detail = (
            f"DEV_ID=0x{idcode & 0xFFF:03X} REV_ID=0x{(idcode >> 16) & 0xFFFF:04X} "
            f"flash={flash_kb}KB UID={uid} RDP={options.rdp} floor={floor}"
        )
        return self._result("identify", fixture, detail, **data)

    def erase(self, fixture: str, *, confirm: bool = False, **_: Any) -> ToolResult:
        if not confirm:
            raise ToolError("mass erase needs --confirm (all flash content is lost)")
        with self._backend(fixture) as backend:
            options = backend.read_option_bytes()
            if options.rdp != 0:
                raise ToolError(
                    f"chip is read-protected (RDP {options.rdp}); use the recovery tool"
                )
            backend.mass_erase()
        return self._result("erase", fixture, "mass erase complete")

    def program_boot(self, fixture: str, **_: Any) -> ToolResult:
        path = self.profile.image_path("boot")
        with self._backend(fixture) as backend:
            backend.program(path, verify=True)
        return self._result("program-boot", fixture, f"programmed and verified {path.name}")

    def program_factory(self, fixture: str, **_: Any) -> ToolResult:
        spec = self.profile.factory_image
        if spec is None:
            raise ToolError("the profile has no factory image (see build-factory)")
        path = self.profile.image_path("factory")
        with self._backend(fixture) as backend:
            backend.program(path, verify=True)
        return self._result(
            "program-factory", fixture, f"programmed and verified {path.name}"
        )

    def load_app(
        self,
        fixture: str,
        *,
        image: str = "",
        via: str = "swd",
        activate: bool = True,
        **_: Any,
    ) -> ToolResult:
        spec = self.profile.app_image
        path = image or str(self.profile.image_path("app"))
        parsed = self._image(path, signed=self.profile.production)
        if via == "boot":
            with self._backend(fixture) as backend, self._session(fixture, backend) as session:
                wait_for_boot(session, swd=backend, timeout=20.0)
                result = session.update_image(parsed, activate=activate, force=False)
            if not result.get("success"):
                raise ToolError(f"BOOT refused the image: {result}")
            return self._result(
                "load-app",
                fixture,
                f"uploaded {path} over BOOT (crc32 0x{parsed.crc32:08X})",
                via="boot",
                crc32=parsed.crc32,
            )
        if parsed.load_address != layout.SLOT_A_BASE:
            raise ToolError(
                f"{path} loads at 0x{parsed.load_address:08X}, not slot A"
            )
        with self._backend(fixture) as backend:
            backend.mass_erase()
            backend.program(path, layout.SLOT_A_BASE, verify=False)
            backend.write_memory(layout.SLOT_A_BASE, parsed.data)
            readback = backend.read_memory(layout.SLOT_A_BASE, len(parsed.data))
            if readback != parsed.data:
                raise ToolError("application read-back mismatch")
            backend.erase_address(layout.META_A_BASE, layout.META_SIZE)
            for offset, record in zip(
                parsed.metadata_offsets(),
                parsed.to_metadata_records(sequence=1),
                strict=True,
            ):
                backend.write_memory(layout.META_A_BASE + offset, record)
        _ = spec
        return self._result(
            "load-app",
            fixture,
            f"programmed {path} into slot A (v{parsed.version_string()})",
            via="swd",
            crc32=parsed.crc32,
            security_counter=parsed.security_counter,
        )

    def test(
        self,
        fixture: str,
        *,
        tests: list[str] | None = None,
        timeout: float = 180.0,
        **_: Any,
    ) -> ToolResult:
        plan = self.profile.test_plan
        selected_patterns = tests or plan.tests
        with self._backend(fixture) as backend, self._session(fixture, backend) as session:
            hello = session.connect("HWVAL", timeout=20.0)
            info = session.device_info()
            catalogue = session.resolve_tests(selected_patterns)
            result = session.run(
                selected_patterns,
                plan.limits,
                allow_operator=plan.allow_operator,
                timeout=timeout,
                allow_destructive=plan.destructive_allowed,
                selection=catalogue,
            )
        rows = [
            {
                "id": row.get("id"),
                "result": row.get("result"),
                "measured": row.get("measured"),
                "limits": row.get("limits"),
                "reason": row.get("reason"),
            }
            for row in result.get("tests", [])
        ]
        failed = [row for row in rows if row["result"] != "PASS"]
        ok = not failed and bool(rows)
        detail = f"{len(rows)} test(s), {len(failed)} not PASS"
        return ToolResult(
            tool="test",
            ok=ok,
            detail=detail,
            data={
                "hello": hello,
                "device_info": info,
                "results": rows,
                "summary": {k: v for k, v in result.items() if k != "tests"},
            },
        )

    def identity(
        self,
        fixture: str,
        *,
        action: str = "read",
        serial: str = "",
        mac: str = "",
        hw_revision: str = "",
        **_: Any,
    ) -> ToolResult:
        address = provisioning_address(self.profile)
        if action in ("read", "verify"):
            with self._backend(fixture) as backend:
                raw = backend.read_memory(address, layout.IDENTITY_SIZE)
                uid = backend.read_memory(UID_BASE, 12).hex().upper()
            try:
                record = parse_record(raw, expect_uid=bytes.fromhex(uid))
            except RecordError as exc:
                raise ToolError(f"identity read failed: {exc}") from exc
            data = {
                "uid": record.uid_hex,
                "serial": record.serial_number,
                "mac": record.mac,
                "hw_revision": record.hw_revision,
                "revision": record.revision,
                "address": address,
            }
            if action == "verify":
                problems = []
                if serial and record.serial_number != serial:
                    problems.append(f"serial {record.serial_number!r} != {serial!r}")
                if mac and record.mac.upper() != mac.upper():
                    problems.append(f"MAC {record.mac!r} != {mac!r}")
                if problems:
                    raise ToolError("; ".join(problems))
            return self._result(
                "identity",
                fixture,
                f"serial={record.serial_number} mac={record.mac} rev={record.revision}",
                action=action,
                **data,
            )
        if action != "write":
            raise ToolError(f"unknown identity action {action!r}")
        with self._backend(fixture) as backend:
            uid_bytes = backend.read_memory(UID_BASE, 12)
            uid = uid_bytes.hex().upper()
            if not serial:
                if self.station.work_order is None:
                    self.station.ensure_work_order()
                allocation = self.station.serial_service.allocate(
                    self.station.work_order,
                    uid=uid,
                    run_id=0,
                    need_mac=self.profile.identity.mac_enabled,
                    local_mac=self.profile.identity.mac_source == "local",
                )
                if allocation is None:
                    raise ToolError("no serial/MAC available")
                serial = allocation.serial_number
                mac = allocation.mac
            mac_bytes = None
            if mac:
                mac_bytes = bytes(int(part, 16) for part in mac.replace("-", ":").split(":"))
            existing_revision = 0
            with contextlib.suppress(RecordError, OpenOcdError):
                existing_revision = parse_record(
                    backend.read_memory(address, layout.IDENTITY_SIZE),
                    expect_uid=uid_bytes,
                ).revision
            app = self.profile.app_image.image
            data = ProvisioningData(
                serial_number=serial,
                product_id=self.profile.profile_id,
                hw_revision=hw_revision or (self.profile.hw_revisions[-1] if self.profile.hw_revisions else ""),
                manufacturing_time=int(now_utc()),
                station_fixture=f"{self.station.config.station_id}/{fixture}",
                profile=f"{self.profile.profile_id}@{self.profile.version}",
                firmware_version=0 if app is None else app.version,
                uid=uid_bytes,
                mac=mac_bytes,
                calibration={},
                customer={},
                custom={},
                revision=existing_revision + 1,
            )
            blob = build_record(data)
            backend.erase_address(address, layout.IDENTITY_SIZE)
            backend.write_memory(address, blob)
            raw = backend.read_memory(address, layout.IDENTITY_SIZE)
            parsed = parse_record(raw, expect_uid=uid_bytes)
        if parsed.serial_number != serial:
            raise ToolError("identity read-back mismatch")
        return self._result(
            "identity",
            fixture,
            f"wrote serial={serial} mac={parsed.mac} rev={parsed.revision}",
            action="write",
            uid=uid,
            serial=serial,
            mac=parsed.mac,
            revision=parsed.revision,
        )

    def boot_check(self, fixture: str, **_: Any) -> ToolResult:
        with self._backend(fixture) as backend, self._session(fixture, backend) as session:
            hello = wait_for_application(session, swd=backend, timeout=20.0)
            role = str(hello.get("role", "")).upper()
            if role in ("HWVAL", "BOOTLOADER", "BOOT"):
                raise ToolError(f"unit boots {role}, not the application")
            status = session.status() if role in ("DEMO", "APP") else {}
        app = self.profile.app_image.image
        expected = "" if app is None else app.version_string()
        problems = []
        if status:
            if str(status.get("slot_name", "")).upper() != "A":
                problems.append(f"slot {status.get('slot_name')} != A")
            if str(status.get("trial_name", "")).upper() != "CONFIRMED":
                problems.append(f"trial {status.get('trial_name')} != CONFIRMED")
            if expected and str(status.get("version")) != expected:
                problems.append(f"version {status.get('version')} != {expected}")
        if problems:
            raise ToolError("; ".join(problems))
        return self._result(
            "boot-check",
            fixture,
            f"role={role} slot={status.get('slot_name')} fw={status.get('version')}",
            hello=hello,
            status=status,
        )

    def protect(self, fixture: str, **_: Any) -> ToolResult:
        spec = self.profile.protection
        if spec.rdp == 2:
            raise ToolError("RDP level 2 is never allowed")
        with self._backend(fixture) as backend:
            values = apply_protection(backend, spec)
            actual = backend.read_option_bytes()
        if actual.rdp != spec.rdp:
            raise ToolError(f"RDP read-back {actual.rdp} != {spec.rdp}")
        return self._result(
            "protect", fixture, str(values), option_bytes=option_bytes_to_dict(actual)
        )

    def recover(
        self,
        fixture: str,
        *,
        action: str = "catch",
        reason: str = "",
        confirm: bool = False,
        timeout: float = 10.0,
        **_: Any,
    ) -> ToolResult:
        service = RecoveryService(self.station)
        if action == "catch":
            data = service.catch(fixture, timeout=timeout)
        elif action == "unstick":
            data = service.unstick_trial(fixture, timeout=timeout)
        elif action == "unlock-rdp":
            data = service.unlock_rdp(
                fixture, operator=self.operator, reason=reason, confirmed=confirm
            )
        elif action == "connect":
            data = service.connect_under_reset(fixture)
        else:
            raise ToolError(f"unknown recovery action {action!r}")
        return self._result("recover", fixture, f"{action} complete", action=action, **data)

    def capture_log(
        self,
        fixture: str,
        *,
        port: str = "",
        seconds: float = 10.0,
        **_: Any,
    ) -> ToolResult:
        config = self.station.config.fixture(fixture)
        target = port or config.debug_uart
        if not target:
            raise ToolError("no debug UART configured for this fixture (set debug_uart)")
        log = create_debug_log(target)
        log.start()
        time.sleep(max(0.1, seconds))
        lines = log.stop()
        if log.error:
            raise ToolError(f"debug UART {target}: {log.error}")
        return self._result(
            "capture-log",
            fixture,
            f"{len(lines)} line(s) from {target}",
            port=target,
            lines=lines,
            tags=log.tags(),
        )


__all__ = ["ToolError", "ToolResult", "ToolRunner"]
