"""EOL step implementation: identity (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

import hashlib

from hostcore import layout
from hostcore.openocd import (
    OpenOcdError,
)
from hwval_factory.identity import (
    ProvisioningData,
    RecordError,
    build_record,
    now_utc,
    parse_record,
)
from hwval_factory.models import (
    RunContext,
    RunKind,
)
from hwval_factory.protection import (
    provisioning_address,
)
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome


class ProvisionSteps:
    def _step_provision(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        work_order = ctx.work_order
        if not work_order:
            raise StepFailure("no work order is selected")
        if not ctx.uid:
            raise StepFailure("chip UID is unknown; identify the unit first")
        if rt.allocation is None:
            rt.allocation = self.serial_service.allocate(
                work_order,
                uid=ctx.uid,
                run_id=ctx.run_id,
                rework=ctx.kind == RunKind.REWORK,
                need_mac=profile.identity.mac_enabled,
                local_mac=profile.identity.mac_source == "local",
            )
        if rt.allocation is None:
            raise StepFailure("no serial number available (range exhausted or MAC pool empty)")
        ctx.serial_number = rt.allocation.serial_number
        ctx.mac = rt.allocation.mac
        address = provisioning_address(profile)
        # PRV-005: derive the next revision from both the database and the
        # record already on the chip, so a rework always increases it even if
        # a previous database write was interrupted.
        on_chip_revision = 0
        try:
            existing_record = parse_record(
                backend.read_memory(address, 16384), expect_uid=bytes.fromhex(ctx.uid)
            )
            on_chip_revision = existing_record.revision
        except (RecordError, OpenOcdError):
            on_chip_revision = 0
        latest = self.db.latest_record(ctx.uid)
        db_revision = 0 if latest is None else int(latest["revision"])
        revision = max(db_revision, on_chip_revision) + 1
        ctx.record_revision = revision
        app_version = profile.app_image.image.version if profile.app_image.image is not None else 0
        customer: dict[int, str] = {}
        provisioning_cfg = profile.manifest.get("provisioning") or {}
        if isinstance(provisioning_cfg, dict):
            for key, value in (provisioning_cfg.get("customer") or {}).items():
                try:
                    field_id = int(str(key), 0)
                except ValueError:
                    continue
                customer[field_id] = str(value)
            if ctx.work_order:
                customer.setdefault(0x40, ctx.work_order)
            custom: dict[str, str] = {}
            for key, value in (provisioning_cfg.get("custom") or {}).items():
                custom[str(key)] = str(value)
        else:
            custom = {}
        if profile.manifest.get("secrets"):
            raise StepFailure(
                "profile requests secret provisioning; OPEN-F04 is out of scope for v0.1"
            )
        otp_cfg = profile.manifest.get("otp") or {}
        if isinstance(otp_cfg, dict) and otp_cfg.get("enabled"):
            if not otp_cfg.get("approved"):
                raise StepFailure("OTP marker is enabled but not engineer-approved in the profile")
            if not ctx.serial_number:
                raise StepFailure("cannot write OTP marker without a serial number")
        data = ProvisioningData(
            serial_number=ctx.serial_number,
            product_id=profile.profile_id,
            hw_revision=ctx.hw_revision,
            manufacturing_time=now_utc(),
            station_fixture=f"{self.config.station_id}/{ctx.fixture}",
            profile=f"{profile.profile_id}@{profile.version}",
            firmware_version=app_version,
            uid=bytes.fromhex(ctx.uid),
            mac=(bytes.fromhex(ctx.mac.replace(":", "")) if ctx.mac else None),
            calibration=dict(ctx.calibration),
            customer=customer,
            custom=custom,
            revision=revision,
        )
        try:
            blob = build_record(data)
        except RecordError as exc:
            raise StepFailure(str(exc)) from exc
        try:
            # A-01/PRV-001: the identity sector is erased once, then the
            # record is programmed with the flash driver and read back.
            backend.erase_address(address, layout.IDENTITY_SIZE)
            backend.write_memory(address, blob)
            readback = backend.read_memory(address, len(blob))
            if readback != blob:
                raise StepFailure("provisioning record read-back is not byte-identical")
            parsed = parse_record(readback, expect_uid=data.uid)
        except (RecordError, OpenOcdError) as exc:
            raise StepFailure(str(exc)) from exc
        if parsed.serial_number != ctx.serial_number:
            raise StepFailure("provisioning record serial number does not match")
        # The serial is consumed once the record is durable on the chip, even
        # if a later optional step (OTP marker, boot check, label) fails.  A
        # serial written to a unit must stay bound to it (SN-003).
        self.serial_service.consume(rt.allocation, ctx.uid, ctx.run_id)
        rt.serial_consumed = True
        ctx.provisioning_blob = blob
        if isinstance(otp_cfg, dict) and otp_cfg.get("enabled"):
            marker_address = int(str(otp_cfg.get("address") or "0x1FF0F800"), 0)
            marker = hashlib.sha256(
                f"{ctx.serial_number}:{ctx.uid}:{data.manufacturing_time}".encode()
            ).digest()
            backend.write_memory(marker_address, marker)
            if backend.read_memory(marker_address, len(marker)) != marker:
                raise StepFailure("OTP marker read-back mismatch")
            ctx.metadata["otp_written"] = True
        self.db.add_record(ctx.uid, revision, ctx.run_id, blob)
        self.db.audit(
            ctx.operator,
            "serial.consume",
            f"run {ctx.run_id} serial={ctx.serial_number} mac={ctx.mac} uid={ctx.uid}",
        )
        self.db.upsert_unit(
            ctx.uid,
            pcb_serial=ctx.pcb_serial,
            serial_number=ctx.serial_number,
            mac=ctx.mac,
            product_id=profile.profile_id,
            hw_revision=ctx.hw_revision,
        )
        ctx.metadata["safe_state"] = "provisioned"
        self._save_extras(ctx)
        self._update_status(ctx.fixture, serial_number=ctx.serial_number, pcb_serial=ctx.pcb_serial)
        return StepOutcome(
            detail=(
                f"serial={ctx.serial_number} mac={ctx.mac or '—'} rev={revision} at {address:#010x}"
            )
        )
