"""EOL step implementation: label (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from hwval_factory.labels import label_data
from hwval_factory.models import (
    RunContext,
)
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome


class LabelSteps:
    def _step_label(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        app_version = (
            profile.app_image.image.version_string() if profile.app_image.image is not None else ""
        )
        data = label_data(
            serial_number=ctx.serial_number,
            mac=ctx.mac,
            product=profile.product,
            hw_revision=ctx.hw_revision,
            firmware_version=app_version,
            pcb_serial=ctx.pcb_serial,
            uid=ctx.uid,
            work_order=ctx.work_order or "",
            profile=profile.display,
            station=self.config.station_id,
            operator=ctx.operator,
        )
        ctx.label_data = data
        result = self.label_service.print_for_run(
            run_id=ctx.run_id,
            profile=profile,
            data=data,
            user=ctx.operator,
        )
        ctx.label_pending = bool(result.pending)
        if result.error and self.config.printer.enabled:
            raise StepFailure(f"label printer failed: {result.error}")
        self.db.audit(
            ctx.operator,
            "label.print",
            f"run {ctx.run_id} status={result.status} reprint=0",
        )
        detail = result.status
        if result.output_paths:
            detail += " " + ", ".join(str(path) for path in result.output_paths)
        return StepOutcome(detail=detail)
