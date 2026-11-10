"""EOL step implementation: EOL test plan (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from typing import Any

from hostcore.hwlink import HwLinkError
from hwval_factory.models import (
    RunContext,
    TestResult,
)
from hwval_factory.steps.base import AbortedError, RunRuntime, StepFailure, StepOutcome
from hwval_factory.steps.util import (
    optional_float as _optional_float,
)
from hwval_factory.steps.util import (
    uid_word_hex as _uid_word_hex,
)


class TestSteps:
    def _step_test(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        self._close_session(rt)
        session = self._open_session(ctx.fixture, rt)
        hello = self._wait_hello(
            session,
            timeout=self._hello_timeout(profile),
            abort_event=rt.abort,
            expected_role="HWVAL",
        )
        # P0-10: keep the CLI/link-up trace with the run so a cold-boot
        # failure shows whether HWVAL was still in its boot test suite.
        ctx.metadata["test_link_log"] = list(session.link_log)
        role = str(hello.get("role", ""))
        if role.upper() != "HWVAL":
            raise StepFailure(f"test firmware reported role {role!r}, expected HWVAL")
        info = session.device_info()
        reported_uid = str(info.get("uid", "")).upper().removeprefix("0X")
        expected_uid = _uid_word_hex(ctx.uid)
        if reported_uid != expected_uid:
            raise StepFailure(
                f"HWVAL reports UID {reported_uid or '(none)'} != SWD UID {expected_uid}"
            )
        if int(info.get("flash_size_kb", 0)) not in (0, profile.parts.flash_kb):
            raise StepFailure(
                f"HWVAL reports {info.get('flash_size_kb')} KB flash, "
                f"profile expects {profile.parts.flash_kb} KB"
            )
        if not info.get("firmware_name") and not hello.get("firmware_name"):
            raise StepFailure("test firmware HelloReply has no firmware name")
        plan = profile.test_plan
        try:
            selected = session.resolve_tests(plan.tests)
        except HwLinkError as exc:
            raise StepFailure(str(exc)) from exc
        ctx.test_results = []
        selected_ids = {str(test["id"]) for test in selected}

        def _on_result(raw: dict[str, Any]) -> None:
            test = self._test_result_from_raw(raw)
            ctx.test_results.append(test)
            self._update_status(
                ctx.fixture,
                message=f"{test.test_id}: {test.result}",
            )

        try:
            result = session.run(
                plan.tests,
                plan.limits,
                allow_operator=plan.allow_operator,
                timeout=plan.timeout_s,
                abort_event=rt.abort,
                on_result=_on_result,
                allow_destructive=plan.destructive_allowed,
                selection=selected,
            )
        except HwLinkError as exc:
            if rt.abort is not None and rt.abort.is_set():
                raise AbortedError("aborted by operator") from exc
            raise
        first_failure: TestResult | None = None
        attempt_results: list[TestResult] = []
        for raw in result.get("tests", []):
            test = self._test_result_from_raw(raw)
            attempt_results.append(test)
            if test.result != "PASS" and first_failure is None:
                first_failure = test
        missing = selected_ids - {test.test_id for test in attempt_results}
        self._close_session(rt)
        if missing:
            raise StepFailure(
                "device did not return a result for: " + ", ".join(sorted(missing)),
                test_results=attempt_results,
            )
        if result.get("result") != "PASS" or first_failure is not None:
            if first_failure is not None:
                detail = first_failure.reason or "test failed"
                if first_failure.measured_value is not None:
                    detail += (
                        f"; measured {first_failure.measured_value} {first_failure.measured_unit}"
                    )
                if first_failure.limit_min is not None or first_failure.limit_max is not None:
                    detail += (
                        f"; limits {first_failure.limit_min}..{first_failure.limit_max} "
                        f"{first_failure.measured_unit}"
                    )
                raise StepFailure(
                    f"{first_failure.test_id}: {detail}",
                    test_results=attempt_results,
                )
            raise StepFailure(
                str(result.get("reason") or "EOL test plan failed"),
                test_results=attempt_results,
            )
        ctx.test_results = attempt_results
        self._record_test_results(ctx, attempt_results)
        ctx.metadata["safe_state"] = "tested"
        ctx.metadata["test_firmware"] = {
            "name": info.get("firmware_name", hello.get("firmware_name", "")),
            "version": info.get("version", hello.get("version", "")),
            "uid": reported_uid,
        }
        self._save_extras(ctx)
        return StepOutcome(detail=f"{len(ctx.test_results)} tests passed")

    def _test_result_from_raw(self, raw: dict[str, Any]) -> TestResult:
        profile = self.profile
        test_id = str(raw.get("test_id", "") or raw.get("id", ""))
        measured = _optional_float(raw.get("measured_value"))
        measured_raw = raw.get("measured")
        if measured is None and isinstance(measured_raw, dict) and measured_raw.get("valid"):
            measured = _optional_float(measured_raw.get("value"))
        unit = str(raw.get("measured_unit", "") or "")
        if not unit and isinstance(measured_raw, dict):
            unit = str(measured_raw.get("unit", ""))
        limit_min = _optional_float(raw.get("limit_min"))
        limit_max = _optional_float(raw.get("limit_max"))
        result = str(raw.get("result", raw.get("result_name", "FAIL")))
        reason = str(raw.get("reason", ""))
        # A profile may tighten or supply the acceptance window.  The device
        # verdict is authoritative when there is no profile limit.
        if profile is not None and measured is not None:
            limit = profile.test_plan.limits.get(test_id)
            if limit:
                limit_min = limit.get("min", limit_min)
                limit_max = limit.get("max", limit_max)
                if limit_min is not None and measured < limit_min:
                    result = "FAIL"
                    reason = reason or f"measured {measured:g}{unit} below {limit_min:g}{unit}"
                if limit_max is not None and measured > limit_max:
                    result = "FAIL"
                    reason = reason or f"measured {measured:g}{unit} above {limit_max:g}{unit}"
        return TestResult(
            test_id=test_id,
            result=result,
            measured_value=measured,
            measured_unit=unit,
            limit_min=limit_min,
            limit_max=limit_max,
            reason=reason,
            operator_answer=(
                None if raw.get("operator_answer") is None else str(raw.get("operator_answer"))
            ),
        )

    def _record_test_results(self, ctx: RunContext, results: list[TestResult]) -> None:
        for test in results:
            self.db.add_test_result(
                ctx.run_id,
                test_id=test.test_id,
                result=test.result,
                measured_value=test.measured_value,
                measured_unit=test.measured_unit,
                limit_min=test.limit_min,
                limit_max=test.limit_max,
                reason=test.reason,
                operator_answer=test.operator_answer,
            )
