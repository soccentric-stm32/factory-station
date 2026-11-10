"""EOL step implementation: identify (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from hwval_factory.models import (
    RunContext,
    RunKind,
)
from hwval_factory.protection import (
    read_options,
)
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome
from hwval_factory.steps.constants import (
    DBGMCU_IDCODE,
    FLASH_SIZE_REG,
    UID_BASE,
)


class IdentifySteps:
    def _step_identify(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        # A connect-under-reset OpenOCD needs one reset to examine the target;
        # a normal attach is also safe here because identification is read-only.
        backend.reset(halt=True)
        idcode = backend.read_u32(DBGMCU_IDCODE)
        dev_id = idcode & 0xFFF
        rev_id = (idcode >> 16) & 0xFFFF
        flash_kb = backend.read_u16(FLASH_SIZE_REG)
        uid = backend.read_memory(UID_BASE, 12)
        if len(uid) != 12:
            raise StepFailure("could not read the 96-bit chip UID")
        rt.options = backend.read_option_bytes()
        if rt.options.rdp == 2:
            raise StepFailure("chip is RDP level 2 (permanent); no recovery is possible")
        ctx.uid = uid.hex().upper()
        existing = self.db.get_unit(ctx.uid)
        if existing is not None and existing["hw_revision"] and not ctx.hw_revision:
            ctx.hw_revision = str(existing["hw_revision"])
        if not ctx.hw_revision and profile.hw_revisions:
            ctx.hw_revision = profile.hw_revisions[-1]
        if ctx.hw_revision and not profile.accepts_revision(ctx.hw_revision):
            raise StepFailure(
                f"hardware revision {ctx.hw_revision} not in profile {profile.hw_revisions}"
            )
        accepted = profile.parts.accepts(dev_id, rev_id, flash_kb)
        if not accepted:
            raise StepFailure(
                f"wrong part: DEV_ID=0x{dev_id:03X} REV_ID=0x{rev_id:04X} "
                f"flash={flash_kb} KB; profile expects "
                f"DEV_ID=0x{profile.parts.dev_id:03X}, revisions "
                f"{[hex(r) for r in profile.parts.rev_ids]}, {profile.parts.flash_kb} KB"
            )
        ctx.option_bytes = read_options(backend)
        floor = self._secure_floor(backend)
        if floor:
            ctx.metadata["floor_before"] = floor
        self._save_extras(ctx)
        self.db.upsert_unit(
            ctx.uid,
            pcb_serial=ctx.pcb_serial,
            product_id=profile.profile_id,
            hw_revision=ctx.hw_revision,
        )
        self.db.update_run_identity(ctx.run_id, uid=ctx.uid)
        if ctx.kind == RunKind.REWORK:
            ctx.serial_number = self.db.serial_by_uid(ctx.uid) or ""
            ctx.mac = self.db.mac_by_uid(ctx.uid) or ""
        self._update_status(
            ctx.fixture,
            serial_number=ctx.serial_number,
            pcb_serial=ctx.pcb_serial,
        )
        return StepOutcome(
            detail=(
                f"DEV_ID=0x{dev_id:03X} REV_ID=0x{rev_id:04X} flash={flash_kb}KB "
                f"UID={ctx.uid} RDP={rt.options.rdp}"
            )
        )
