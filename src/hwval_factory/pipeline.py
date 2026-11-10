"""Per-fixture pipeline: run a recipe and record every step (P1-02).

This is the control flow that used to live inside ``Station``.  It takes the
station as a services object, so the step handlers stay unchanged while the
fixture worker only manages lifecycle and status.
"""

from __future__ import annotations

import contextlib
import time
import traceback
from typing import Any

from hostcore.hwlink import HwLinkError
from hostcore.images import ImageError
from hostcore.openocd import OpenOcdError
from hwval_factory.database import utcnow
from hwval_factory.identity import RecordError
from hwval_factory.models import FixtureState, RunResult, StepResult
from hwval_factory.power import PowerError
from hwval_factory.profile import ProfileError
from hwval_factory.protection import ProtectionError
from hwval_factory.steps.base import AbortedError, StepDef, StepFailure, StepOutcome


def run_fixture(station: Any, worker: Any) -> None:
    ctx = worker.ctx
    profile = station.require_profile()
    runtime = worker.runtime
    fixture = worker.fixture
    result = RunResult.ERROR
    failed_step: str | None = None
    reason = ""
    try:
        station._update_status(
            fixture,
            state=FixtureState.RUNNING,
            message="registering insertion",
            run_id=ctx.run_id,
        )
        insertions = station.db.register_insertion(fixture)
        if insertions >= station.config.maintenance_count:
            station._emit(
                "maintenance",
                fixture=fixture,
                message=f"fixture maintenance due after {insertions} insertions",
            )
        runtime.abort = worker.abort
        debug = station._debug.get(fixture)
        if debug is not None:
            debug.start()
        runtime.power = station._power.get(fixture)
        if runtime.power is not None and station.config.fixture(fixture).power:
            try:
                runtime.power.on()
            except PowerError as exc:
                station._log(fixture, f"power on failed: {exc}")
        runtime.backend = station._open_backend(fixture)
        runtime.backend.start()
        if station.simulation and station.simulator is not None:
            runtime.bank = station.simulator.bank(fixture)
            runtime.bank.power_cycle()
        steps = station._build_steps(profile, ctx)
        total = len(steps)
        for index, step in enumerate(steps, 1):
            if worker.abort.is_set():
                raise AbortedError("aborted by operator")
            station._check_lid(fixture)
            station._update_status(
                fixture,
                state=FixtureState.RUNNING,
                step_name=step.name,
                step_index=index,
                step_count=total,
                progress=int((index - 1) * 100 / max(1, total)),
                elapsed_s=time.monotonic() - worker.started_monotonic,
                pcb_serial=ctx.pcb_serial,
                message="",
            )
            outcome = station._execute_step(worker, step, index)
            if outcome is None:
                outcome = StepOutcome()
            station._log(
                fixture,
                f"step {index:02d} {step.name}: {outcome.status}"
                + (f" ({outcome.detail})" if outcome.detail else ""),
            )
            station._update_status(
                fixture,
                step_name=step.name,
                step_index=index,
                progress=int(index * 100 / max(1, total)),
                elapsed_s=time.monotonic() - worker.started_monotonic,
                pcb_serial=ctx.pcb_serial,
                serial_number=ctx.serial_number,
            )
        result = RunResult.PASS
        failed_step = None
        reason = ""
    except AbortedError as exc:
        result = RunResult.ABORTED
        failed_step = worker.status.step_name or None
        reason = str(exc)
    except StepFailure as exc:
        result = RunResult.FAIL
        failed_step = worker.status.step_name or None
        tail = station._debug_tail(fixture)
        if tail:
            exc.debug_tail = tail
            ctx.metadata["debug_tail"] = tail
        reason = station._safe_reason(ctx, exc.as_text())
    except (
        OpenOcdError,
        HwLinkError,
        ProfileError,
        ProtectionError,
        RecordError,
        ImageError,
    ) as exc:
        result = RunResult.ERROR
        failed_step = worker.status.step_name or None
        reason = station._safe_reason(ctx, str(exc))
    except Exception as exc:  # pragma: no cover - last-resort isolation
        result = RunResult.ERROR
        failed_step = worker.status.step_name or None
        reason = station._safe_reason(ctx, f"{type(exc).__name__}: {exc}")
        station._log(fixture, traceback.format_exc())
    finally:
        if runtime.allocation is not None and not runtime.serial_consumed:
            try:
                station.serial_service.release(runtime.allocation)
            except Exception as exc:
                station._log(fixture, f"could not release allocation: {exc}")
        runtime.close()
        debug = station._debug.get(fixture)
        if debug is not None:
            captured = debug.stop()
            if captured:
                ctx.metadata["debug_log"] = captured[-400:]
                ctx.metadata["debug_log_tags"] = debug.tags()
        worker.result = result.value
        worker.error = reason
        finished = utcnow()
        try:
            station.db.close_run(
                ctx.run_id,
                result.value,
                failed_step=failed_step,
                reason=reason,
                finished_at=finished,
                label_pending=ctx.label_pending,
            )
            if ctx.uid:
                station.db.upsert_unit(
                    ctx.uid,
                    pcb_serial=ctx.pcb_serial,
                    serial_number=ctx.serial_number,
                    mac=ctx.mac,
                    product_id=profile.profile_id,
                    hw_revision=ctx.hw_revision,
                    last_result=result.value,
                )
            station._save_extras(ctx)
            station.db.register_run(fixture, result.value)
            station.db.audit(
                ctx.operator,
                "run.finish",
                f"{ctx.run_id} {result.value} {failed_step or ''} {reason}",
            )
            if station.mes.enabled:
                station.mes.queue(ctx.run_id)
        except Exception as exc:  # pragma: no cover
            station._log(fixture, f"finalisation failed: {exc}")
        try:
            elapsed = time.monotonic() - worker.started_monotonic
            station._update_status(
                fixture,
                state=(
                    FixtureState.PASS
                    if result == RunResult.PASS
                    else FixtureState.FAIL
                    if result in (RunResult.FAIL, RunResult.ABORTED)
                    else FixtureState.ERROR
                ),
                step_name=worker.status.step_name,
                progress=100 if result == RunResult.PASS else worker.status.progress,
                elapsed_s=elapsed,
                pcb_serial=ctx.pcb_serial,
                serial_number=ctx.serial_number,
                last_result=result.value,
                failed_step=failed_step or "",
                message=reason,
                run_id=ctx.run_id,
            )
        except Exception:
            pass
        worker.done.set()
        station._emit(
            "run_finished",
            fixture=fixture,
            run_id=ctx.run_id,
            result=result.value,
            failed_step=failed_step,
            reason=reason,
            serial=ctx.serial_number,
        )


def _check_budget(station: Any, step: StepDef, duration_ms: int) -> None:
    profile = station.require_profile()
    config = profile.manifest.get("steps") or {}
    budgets = config.get("budget_ms") if isinstance(config, dict) else None
    if not isinstance(budgets, dict):
        return
    try:
        budget = float(budgets.get(step.key, 0))
    except (TypeError, ValueError):
        return
    if budget > 0 and duration_ms > budget:
        station._log(
            step.key,
            f"cycle-time budget exceeded: {duration_ms} ms > {budget:.0f} ms",
        )
        station._emit(
            "budget",
            fixture=None,
            step=step.key,
            duration_ms=duration_ms,
            budget_ms=budget,
        )


def execute_step(station: Any, worker: Any, step: StepDef, seq: int) -> StepOutcome:
    started_at = utcnow()
    started = time.monotonic()
    station.db.add_step(
        worker.ctx.run_id,
        seq,
        step.name,
        StepResult.RUNNING.value,
        started_at,
        None,
        "",
    )
    attempts = max(0, step.retries) + 1
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        if worker.abort.is_set():
            raise AbortedError("aborted by operator")
        try:
            outcome = step.handler(worker.ctx, worker.runtime)
            if outcome is None:
                outcome = StepOutcome()
            elif isinstance(outcome, str):
                outcome = StepOutcome(detail=outcome)
            duration = int((time.monotonic() - started) * 1000)
            station.db.add_step(
                worker.ctx.run_id,
                seq,
                step.name,
                outcome.status,
                started_at,
                duration,
                outcome.detail,
            )
            return outcome
        except AbortedError:
            duration = int((time.monotonic() - started) * 1000)
            station.db.add_step(
                worker.ctx.run_id,
                seq,
                step.name,
                StepResult.ABORTED.value,
                started_at,
                duration,
                "aborted",
            )
            raise
        except StepFailure as exc:
            last_exc = exc
            station._log(
                worker.fixture,
                f"step {step.name} attempt {attempt}/{attempts} failed: {exc.reason}",
            )
            if worker.ctx.metadata.get("safe_state") == "UNKNOWN_CONFIG":
                # P2-03: never retry an option-byte write that may have been
                # interrupted; the recovery tool must inspect it first.
                duration = int((time.monotonic() - started) * 1000)
                station.db.add_step(
                    worker.ctx.run_id,
                    seq,
                    step.name,
                    StepResult.ERROR.value,
                    started_at,
                    duration,
                    exc.as_text(),
                )
                raise
            if attempt >= attempts:
                duration = int((time.monotonic() - started) * 1000)
                _check_budget(station, step, duration)
                station.db.add_step(
                    worker.ctx.run_id,
                    seq,
                    step.name,
                    StepResult.FAIL.value,
                    started_at,
                    duration,
                    exc.detail or exc.reason,
                )
                if exc.test_results:
                    worker.ctx.test_results = exc.test_results
                    station._record_test_results(worker.ctx, exc.test_results)
                raise
            time.sleep(0.3)
        except (OpenOcdError, HwLinkError, OSError) as exc:
            last_exc = exc
            station._log(
                worker.fixture,
                f"step {step.name} attempt {attempt}/{attempts} I/O error: {exc}",
            )
            if attempt >= attempts:
                duration = int((time.monotonic() - started) * 1000)
                _check_budget(station, step, duration)
                station.db.add_step(
                    worker.ctx.run_id,
                    seq,
                    step.name,
                    StepResult.ERROR.value,
                    started_at,
                    duration,
                    str(exc),
                )
                raise StepFailure(str(exc)) from exc
            station._log(worker.fixture, f"restarting OpenOCD after: {exc}")
            with contextlib.suppress(Exception):
                station._restart_backend(worker)
            time.sleep(0.5 * attempt)
    raise StepFailure(str(last_exc) if last_exc else "step failed")
