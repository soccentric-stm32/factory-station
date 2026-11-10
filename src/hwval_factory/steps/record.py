"""EOL step implementation: record (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from hwval_factory.models import (
    RunContext,
)
from hwval_factory.steps.base import RunRuntime, StepOutcome


class RecordSteps:
    def _step_record(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        self.db.checkpoint()
        self.db.audit(
            ctx.operator,
            "run.record",
            f"{ctx.run_id} profile={ctx.profile_id}@{ctx.profile_version}",
        )
        ctx.metadata["committed"] = True
        return StepOutcome(detail="run committed; MES queue updated at close")
