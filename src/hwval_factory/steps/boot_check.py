"""EOL step implementation: boot check (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from typing import Any

from hostcore import layout
from hostcore.boot import BootError, wait_for_application
from hostcore.hwlink import HwLinkError
from hostcore.images import parse_metadata_record
from hostcore.openocd import (
    OpenOcdError,
)
from hwval_factory.identity import (
    RecordError,
    parse_record,
)
from hwval_factory.models import (
    RunContext,
)
from hwval_factory.protection import (
    provisioning_address,
)
from hwval_factory.steps.base import AbortedError, RunRuntime, StepFailure, StepOutcome
from hwval_factory.steps.util import (
    parse_identity_detail as _parse_identity_detail,
)
from hwval_factory.steps.util import (
    uid64 as _uid64,
)


class BootCheckSteps:
    def _step_boot_check(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        self._close_session(rt)
        session = self._open_session(ctx.fixture, rt)
        try:
            # P0-06: reset, stay off the port for BOOT's listen window, and
            # only then send Hello.  If BOOT answers with a valid slot the
            # Hello caught the window; release it and retry (like bootctl).
            hello = wait_for_application(
                session,
                swd=backend,
                timeout=self._boot_timeout(profile),
                on_log=lambda text: self._log(ctx.fixture, text),
                abort_event=rt.abort,
            )
        except BootError as exc:
            if rt.abort is not None and rt.abort.is_set():
                raise AbortedError("aborted by operator") from exc
            raise StepFailure(str(exc)) from exc
        role = str(hello.get("role", "")).upper()
        if role == "HWVAL":
            raise StepFailure("unit still boots the HWVAL test firmware, not the product")
        if role in ("BOOTLOADER", "BOOT"):
            raise StepFailure("BOOT did not start an application")
        expected_role = str((profile.manifest.get("boot_check") or {}).get("role", "APP"))
        if expected_role and role not in (expected_role.upper(), "APP", "DEMO"):
            raise StepFailure(f"booted firmware reported role {role!r}, expected {expected_role}")
        status: dict[str, Any] = {}
        if role in ("DEMO", "APP"):
            try:
                status = session.status()
            except HwLinkError as exc:
                raise StepFailure(f"application did not answer GetStatus: {exc}") from exc
            slot_name = str(status.get("slot_name", "")).upper()
            trial_name = str(status.get("trial_name", "")).upper()
            if slot_name != "A":
                raise StepFailure(f"application reports slot {slot_name or 'UNKNOWN'}, expected A")
            if trial_name != "CONFIRMED":
                raise StepFailure(
                    f"application reports trial state {trial_name or 'UNKNOWN'}, expected CONFIRMED"
                )
            reported_uid = int(status.get("uid", 0))
            if reported_uid and reported_uid != _uid64(ctx.uid):
                raise StepFailure(
                    f"booted chip UID {reported_uid:#x} does not match {_uid64(ctx.uid):#x}"
                )
        reported_identity = _parse_identity_detail(str(hello.get("detail", "")))
        if reported_identity.get("serial"):
            if reported_identity["serial"] != ctx.serial_number:
                raise StepFailure(
                    f"application identity serial {reported_identity['serial']!r} "
                    f"!= provisioned {ctx.serial_number!r}"
                )
            if ctx.mac and reported_identity.get("mac", "").upper() != ctx.mac.upper():
                raise StepFailure(
                    f"application identity MAC {reported_identity.get('mac')!r} "
                    f"!= provisioned {ctx.mac!r}"
                )
        self._close_session(rt)
        # B-10/B-14: read the identity record back over SWD and compare it to
        # the serial/MAC allocated for this run.  This runs before RDP 1.
        address = provisioning_address(profile)
        try:
            raw = backend.read_memory(address, 16384)
            record = parse_record(raw, expect_uid=bytes.fromhex(ctx.uid))
        except (RecordError, OpenOcdError) as exc:
            raise StepFailure(f"identity readback failed: {exc}") from exc
        if record.serial_number != ctx.serial_number:
            raise StepFailure(
                f"identity serial {record.serial_number!r} != provisioned {ctx.serial_number!r}"
            )
        if ctx.mac and record.mac.upper() != ctx.mac.upper():
            raise StepFailure(f"identity MAC {record.mac!r} != provisioned {ctx.mac!r}")
        # Confirm slot A is marked verified/activated/confirmed in metadata.
        meta = backend.read_memory(layout.META_A_BASE, layout.META_HEADER_SIZE + 0x60)
        magic = int.from_bytes(meta[:4], "little") if len(meta) >= 4 else 0
        if magic != layout.IMAGE_MAGIC:
            raise StepFailure("BOOT metadata header is missing after programming")
        for offset, expected_magic, label in (
            (layout.META_VERIFIED_OFFSET, layout.META_VERIFIED_MAGIC, "VERIFIED"),
            (layout.META_ACTIVATED_OFFSET, layout.META_ACTIVATED_MAGIC, "ACTIVATED"),
            (layout.META_CONFIRMED_OFFSET, layout.META_CONFIRMED_MAGIC, "CONFIRMED"),
        ):
            rec_magic, _value, present = parse_metadata_record(
                meta[offset : offset + layout.META_RECORD_SIZE]
            )
            if not present or rec_magic != expected_magic:
                raise StepFailure(f"BOOT metadata {label} record is missing or invalid")
        expected_version = (
            "0.0.0" if profile.app_image.image is None else profile.app_image.image.version_string()
        )
        reported_version = str(status.get("version", hello.get("version", "")))
        if expected_version not in ("", "0.0.0") and reported_version not in (
            "",
            expected_version,
        ):
            raise StepFailure(
                f"booted firmware version {reported_version!r} != expected {expected_version!r}"
            )
        ctx.metadata["safe_state"] = "boot_checked"
        ctx.metadata["identity_over_hwlink"] = reported_identity
        ctx.metadata["identity_readback"] = {
            "address": address,
            "revision": record.revision,
            "serial": record.serial_number,
            "mac": record.mac,
            "uid": record.uid_hex,
        }
        self._save_extras(ctx)
        return StepOutcome(
            detail=(
                f"role={role} slot=A confirmed serial={ctx.serial_number} "
                f"fw={reported_version} identity@{address:#010x}"
            )
        )
