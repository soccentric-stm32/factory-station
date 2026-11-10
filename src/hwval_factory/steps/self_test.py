"""Golden-board self-test as a step (P2-05)."""

from __future__ import annotations

from hwval_factory.models import RunContext
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome
from hwval_factory.steps.constants import DBGMCU_IDCODE, UID_BASE


class SelfTestSteps:
    def _step_self_test(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        backend = self._backend(rt)
        backend.halt()
        idcode = backend.read_u32(DBGMCU_IDCODE)
        uid = backend.read_memory(UID_BASE, 12).hex().upper()
        dev_id = idcode & 0xFFF
        golden = str(getattr(self.config.fixture(ctx.fixture), "golden_uid", "") or "")
        passed = dev_id == 0x451 and (not golden or uid == golden.upper())
        detail = f"DEV_ID=0x{dev_id:03X} UID={uid}"
        if not passed:
            raise StepFailure(
                detail + (f" (expected UID {golden})" if golden else ""),
                cause="the golden board is missing, wrong or has a different UID",
                action="fit the golden board for this fixture and rerun the self-test",
                measured=uid,
                expected=golden or "0x451",
            )
        ctx.metadata["self_test"] = {"uid": uid, "dev_id": dev_id}
        return StepOutcome(detail=detail)
