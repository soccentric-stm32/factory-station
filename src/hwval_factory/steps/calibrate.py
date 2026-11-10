"""EOL step implementation: calibration (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from hwval_factory.models import (
    RunContext,
    StepResult,
    TestResult,
)
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome


class CalibrateSteps:
    def _step_calibrate(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        if not profile.calibration:
            return StepOutcome(status=StepResult.SKIP.value)
        measured = {result.test_id: result for result in ctx.test_results}
        parts: list[str] = []
        for rule in profile.calibration:
            result = self._find_test_result(measured, rule.source)
            if result is None or result.measured_value is None:
                raise StepFailure(f"calibration source {rule.source} has no measured value")
            value = rule.evaluate(float(result.measured_value))
            unit = rule.unit or result.measured_unit
            ctx.calibration[rule.field_id] = (value, unit)
            parts.append(f"{rule.field_id:#04x}={value:g}{unit}")
        return StepOutcome(detail=", ".join(parts))

    def _find_test_result(self, measured: dict[str, TestResult], source: str) -> TestResult | None:
        if "." not in source:
            return measured.get(source)
        candidates = sorted(measured, key=len, reverse=True)
        for test_id in candidates:
            if source == test_id or source.startswith(test_id + "."):
                return measured[test_id]
        return None
