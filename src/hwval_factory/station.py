"""Station engine: one independent worker per fixture (ARCH-003, EOL-*).

The engine is deliberate about the order of operations and about safe states:

* every step is timed, logged, persisted and retried according to the profile;
* a failure stops the run and records the failing step, reason, value and
  limit, together with the flash state (``erased``, ``app_programmed``,
  ``provisioned``, ``protected``);
* a serial number becomes CONSUMED only after the provisioning record has been
  read back and checked; if the run fails before that point it returns to the
  pool;
* RDP level 2 cannot be selected.
"""

from __future__ import annotations

import contextlib
import hashlib
import queue
import re
import subprocess
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hostcore import layout
from hostcore.hwlink import (
    HwLinkError,
    HwLinkSession,
    SerialTransport,
    SimulatedTransport,
)
from hostcore.images import FirmwareImage, ImageError, parse_metadata_record
from hostcore.openocd import (
    OpenOcdBackend,
    OpenOcdError,
    OpenOcdProcess,
    OptionBytes,
    option_bytes_to_dict,
)
from hwval_factory.config import StationConfig, validate_station_config
from hwval_factory.database import Database, utcnow
from hwval_factory.identity import (
    ProvisioningData,
    RecordError,
    build_record,
    now_utc,
    parse_record,
)
from hwval_factory.labels import LabelService, label_data
from hwval_factory.mes import MesService
from hwval_factory.models import (
    FixtureState,
    FixtureStatus,
    RunContext,
    RunKind,
    RunResult,
    StepResult,
    TestResult,
    WorkOrder,
)
from hwval_factory.power import PowerDriver, PowerError, create_power_driver
from hwval_factory.profile import (
    ProductProfile,
    ProfileError,
    ProtectionSpec,
    load_profile,
)
from hwval_factory.protection import (
    ProtectionError,
    apply_protection,
    lower_rdp_for_rework,
    provisioning_address,
    read_options,
)
from hwval_factory.serials import (
    Allocation,
    SerialService,
    format_mac,
    parse_oui_block,
)
from hwval_factory.simulator import SimulatedStation, SimulationBank

DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FF0F442
UID_BASE = 0x1FF0F420
DEFAULT_SLOT_A = layout.SLOT_A_BASE
DEFAULT_METADATA_BASE = layout.META_A_BASE
SCAN_TIMEOUT_S = 120.0


class StationError(RuntimeError):
    """Station-level action could not be performed."""


class StepFailure(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        test_results: list[TestResult] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.test_results = test_results or []


class AbortedError(RuntimeError):
    pass


@dataclass(slots=True)
class StepOutcome:
    status: str = StepResult.PASS.value
    detail: str = ""


@dataclass(slots=True)
class StepDef:
    key: str
    name: str
    handler: Callable[[RunContext, RunRuntime], StepOutcome | str | None]
    retries: int = 0


@dataclass(slots=True)
class RunRuntime:
    backend: OpenOcdBackend | None = None
    session: HwLinkSession | None = None
    bank: SimulationBank | None = None
    options: OptionBytes | None = None
    allocation: Allocation | None = None
    serial_consumed: bool = False
    test_image: FirmwareImage | None = None
    app_image: FirmwareImage | None = None
    power: PowerDriver | None = None
    abort: threading.Event | None = None

    def close(self) -> None:
        if self.session is not None:
            with contextlib.suppress(Exception):
                self.session.close()
            self.session = None
        if self.backend is not None:
            with contextlib.suppress(Exception):
                self.backend.stop()
            self.backend = None
        if self.power is not None:
            with contextlib.suppress(Exception):
                self.power.close()
            self.power = None


@dataclass(slots=True)
class _Worker:
    fixture: str
    ctx: RunContext
    abort: threading.Event = field(default_factory=threading.Event)
    scan_queue: queue.Queue[str] = field(default_factory=queue.Queue)
    thread: threading.Thread | None = None
    runtime: RunRuntime = field(default_factory=RunRuntime)
    status: FixtureStatus = field(default_factory=lambda: FixtureStatus(name=""))
    done: threading.Event = field(default_factory=threading.Event)
    result: str = ""
    error: str = ""
    started_monotonic: float = 0.0


class Station:
    def __init__(
        self,
        config: StationConfig,
        db: Database,
        profile: ProductProfile | None = None,
        *,
        prompt_handler: Callable[[str, dict[str, Any]], str] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        log_handler: Callable[[str, str], None] | None = None,
        simulation: bool | None = None,
        fail_tests: set[str] | None = None,
        prompt_tests: set[str] | None = None,
        fault_rate: float = 0.0,
    ) -> None:
        validate_station_config(config)
        self.config = config
        self.db = db
        self.profile = profile
        self.prompt_handler = prompt_handler
        self.on_event = on_event
        self.log_handler = log_handler
        self.simulation = config.simulation if simulation is None else simulation
        self.simulator: SimulatedStation | None = None
        if self.simulation:
            simulated_prompts = set(prompt_tests or [])
            if profile is not None:
                simulated_prompts |= {
                    test
                    for test in profile.test_plan.tests
                    if test.upper().startswith("BTN")
                    or "PROMPT" in test.upper()
                    or "OPERATOR" in test.upper()
                }
            self.simulator = SimulatedStation(
                [f.name for f in config.fixtures if f.enabled],
                fault_rate=fault_rate,
                fail_tests=fail_tests,
                prompt_tests=simulated_prompts,
            )
        self.serial_service = SerialService(db)
        self.label_service = LabelService(db, config.printer, labels_dir=config.labels_dir)
        self.mes = MesService(db, config.mes, on_queue_change=self._mes_queue_changed)
        self.work_order: WorkOrder | None = None
        self._workers: dict[str, _Worker] = {}
        self._statuses: dict[str, FixtureStatus] = {
            f.name: FixtureStatus(name=f.name, probe_serial=f.probe_serial) for f in config.fixtures
        }
        self._lock = threading.RLock()
        self._closed = False
        self._log_buffers: dict[str, list[str]] = {f.name: [] for f in config.fixtures}
        self._last_mes_queue = 0
        self._power: dict[str, PowerDriver] = {}
        for fixture in config.fixtures:
            try:
                self._power[fixture.name] = create_power_driver(fixture.power)
            except PowerError:
                self._power[fixture.name] = create_power_driver({})
        self.mes.start()

    # -- profile and work order ---------------------------------------
    def set_profile(self, profile: ProductProfile) -> None:
        profile.check_factory_version()
        previous = self.profile
        self.profile = profile
        if previous is not None and previous is not profile:
            previous.cleanup()
        self._emit("profile", profile=f"{profile.profile_id}@{profile.version}")

    def load_profile(
        self,
        identifier: str,
        *,
        require_signature: bool = False,
        user: str = "engineer",
    ) -> ProductProfile:
        candidate = Path(identifier)
        signature_required = require_signature or self.config.require_profile_signature
        if candidate.exists():
            profile = load_profile(
                candidate,
                require_signature=signature_required,
                public_key=self.config.profile_public_key or None,
            )
        else:
            from hwval_factory.profile import find_profile

            profile = find_profile(
                self.config.profiles_dir,
                identifier,
                require_signature=signature_required,
                public_key=self.config.profile_public_key or None,
            )
        self.set_profile(profile)
        self.db.audit(
            user,
            "profile.import",
            f"{profile.profile_id}@{profile.version} from {identifier}",
        )
        return profile

    def require_profile(self) -> ProductProfile:
        if self.profile is None:
            raise StationError("no product profile is selected")
        return self.profile

    def select_work_order(
        self,
        name: str,
        *,
        profile_id: str = "",
        quantity: int = 0,
        serial_prefix: str = "",
        serial_start: int = 1,
        serial_end: int = 0,
        mac_start: str = "",
        mac_count: int = 0,
        user: str = "supervisor",
    ) -> WorkOrder:
        profile = self.require_profile()
        if profile_id and profile_id != profile.profile_id:
            from hwval_factory.profile import find_profile

            profile = find_profile(
                self.config.profiles_dir,
                profile_id,
                require_signature=self.config.require_profile_signature,
                public_key=self.config.profile_public_key or None,
            )
            self.set_profile(profile)
        wo = WorkOrder(
            name=name,
            profile_id=profile.profile_id,
            quantity=quantity,
            serial_prefix=serial_prefix,
            serial_start=serial_start,
            serial_end=serial_end,
            mac_start=mac_start,
            mac_count=mac_count,
            created_at=utcnow(),
        )
        self.serial_service.prepare_work_order(wo, profile)
        self.work_order = wo
        counts = self.serial_service.usage(name)
        self.db.audit(
            user,
            "work_order.select",
            f"{name} profile={profile.profile_id} quantity={quantity} serials={counts}",
        )
        self._emit("work_order", work_order=name, counts=counts)
        return wo

    def auto_work_order(self, *, quantity: int = 10000, user: str = "system") -> WorkOrder:
        profile = self.require_profile()
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        start = self.db.reserve_auto_counter("auto_serial_counter", quantity)
        mac_start = ""
        if profile.identity.mac_enabled:
            prefix, _mask, _capacity = parse_oui_block(profile.identity.oui_block)
            mac_start = format_mac(prefix + start - 1)
        return self.select_work_order(
            f"AUTO-{profile.profile_id}-{stamp}",
            quantity=quantity,
            serial_start=start,
            serial_end=start + quantity - 1,
            mac_start=mac_start,
            mac_count=quantity if mac_start else 0,
            user=user,
        )

    def ensure_work_order(self) -> WorkOrder:
        if self.work_order is not None:
            return self.work_order
        return self.auto_work_order()

    def close_work_order(self, user: str = "supervisor") -> None:
        if self.work_order is None:
            return
        self.db.close_work_order(self.work_order.name)
        self.db.audit(user, "work_order.close", self.work_order.name)
        self._emit("work_order_closed", work_order=self.work_order.name)
        self.work_order = None

    # -- status and scan routing --------------------------------------
    def status(self, fixture: str) -> FixtureStatus:
        with self._lock:
            return self._statuses[fixture]

    def snapshots(self) -> list[FixtureStatus]:
        with self._lock:
            return [self._statuses[f.name] for f in self.config.fixtures]

    def fixture_names(self) -> list[str]:
        return [f.name for f in self.config.fixtures]

    def submit_scan(self, text: str, fixture: str | None = None) -> str | None:
        text = text.strip()
        if not text:
            return None
        with self._lock:
            if fixture is not None:
                worker = self._workers.get(fixture)
                if worker is not None and not worker.done.is_set():
                    worker.scan_queue.put(text)
                    return fixture
                return None
            for name in self.fixture_names():
                worker = self._workers.get(name)
                if (
                    worker is not None
                    and not worker.done.is_set()
                    and worker.status.state == FixtureState.LOAD
                    and worker.scan_queue.empty()
                ):
                    worker.scan_queue.put(text)
                    return name
        return None

    def waiting_fixtures(self) -> list[str]:
        with self._lock:
            return [s.name for s in self._statuses.values() if s.state == FixtureState.LOAD]

    # -- run lifecycle ------------------------------------------------
    def start_run(
        self,
        fixture: str,
        *,
        pcb_serial: str = "",
        operator: str = "operator",
        kind: RunKind = RunKind.FIRST,
        retest_approved: bool = False,
        rework_approved: bool = False,
        hw_revision: str = "",
    ) -> int:
        profile = self.require_profile()
        if (
            self.work_order is not None
            and self.work_order.profile_id
            and self.work_order.profile_id != profile.profile_id
        ):
            raise StationError(
                f"work order {self.work_order.name} belongs to profile "
                f"{self.work_order.profile_id}, active profile is {profile.profile_id}"
            )
        config = self.config.fixture(fixture)
        if not config.enabled:
            raise StationError(f"fixture {fixture} is disabled")
        with self._lock:
            existing = self._workers.get(fixture)
            if existing is not None and not existing.done.is_set():
                raise StationError(f"fixture {fixture} is already running")
            if pcb_serial and kind == RunKind.FIRST and self.db.run_count_for_pcb(pcb_serial):
                raise StationError(
                    f"board {pcb_serial} already has runs in the database; "
                    "start a retest or rework instead"
                )
            if kind == RunKind.RETEST and not retest_approved:
                failures = self.db.failure_count_for_pcb(pcb_serial) if pcb_serial else 0
                if failures >= self.config.retest_after_failures:
                    raise StationError(
                        f"retest requires supervisor approval after {failures} failure(s)"
                    )
            if kind == RunKind.REWORK and not rework_approved:
                raise StationError("rework requires supervisor approval")
            if self.work_order is None:
                self.auto_work_order()
            started = utcnow()
            run_id = self.db.create_run(
                uid="",
                pcb_serial=pcb_serial,
                station_id=self.config.station_id,
                fixture=fixture,
                operator=operator,
                work_order=self.work_order.name if self.work_order else None,
                profile_id=profile.profile_id,
                profile_version=profile.version,
                kind=kind.value,
                started_at=started,
            )
            ctx = RunContext(
                run_id=run_id,
                fixture=fixture,
                operator=operator,
                kind=kind,
                profile_id=profile.profile_id,
                profile_version=profile.version,
                work_order=self.work_order.name if self.work_order else None,
                pcb_serial=pcb_serial,
                hw_revision=hw_revision,
                started_at=started,
            )
            if kind == RunKind.REWORK:
                ctx.metadata["rework_approved"] = True
            status = FixtureStatus(
                name=fixture,
                state=FixtureState.LOAD if not pcb_serial else FixtureState.RUNNING,
                probe_serial=config.probe_serial,
                run_id=run_id,
                pcb_serial=pcb_serial,
                message="waiting for board scan" if not pcb_serial else "starting",
            )
            worker = _Worker(fixture=fixture, ctx=ctx, status=status)
            self._workers[fixture] = worker
            self._statuses[fixture] = status
            worker.started_monotonic = time.monotonic()
            self.db.audit(
                operator,
                "run.start",
                f"{run_id} fixture={fixture} kind={kind.value} work_order="
                f"{self.work_order.name if self.work_order else ''}",
            )
            worker.thread = threading.Thread(
                target=self._run_fixture,
                args=(worker,),
                name=f"fixture-{fixture}",
                daemon=True,
            )
            worker.thread.start()
            self._emit("run_started", fixture=fixture, run_id=run_id)
            return run_id

    def start_all(
        self,
        *,
        operator: str = "operator",
        kind: RunKind = RunKind.FIRST,
    ) -> list[str]:
        started: list[str] = []
        errors: list[str] = []
        for fixture in self.fixture_names():
            try:
                self.start_run(fixture, operator=operator, kind=kind)
                started.append(fixture)
            except StationError as exc:
                errors.append(f"{fixture}: {exc}")
        if errors and not started:
            raise StationError("; ".join(errors))
        return started

    def abort(self, fixture: str) -> None:
        with self._lock:
            worker = self._workers.get(fixture)
            if worker is not None and not worker.done.is_set():
                worker.abort.set()
                worker.status.message = "abort requested"
                self._emit("abort", fixture=fixture)

    def abort_all(self) -> None:
        for name in self.fixture_names():
            self.abort(name)

    def wait(self, fixture: str, timeout: float | None = None) -> str:
        worker = self._workers.get(fixture)
        if worker is None:
            raise StationError(f"fixture {fixture} has not run")
        if not worker.done.wait(timeout):
            raise StationError(f"fixture {fixture} did not finish in time")
        return worker.result

    def last_result(self, fixture: str) -> str:
        worker = self._workers.get(fixture)
        return "" if worker is None else worker.result

    def worker_ctx(self, fixture: str) -> RunContext | None:
        worker = self._workers.get(fixture)
        return None if worker is None else worker.ctx

    def run_log(self, fixture: str) -> list[str]:
        with self._lock:
            extra = list(self._log_buffers.get(fixture, []))
        if self.simulation and self.simulator is not None:
            bank = self.simulator.banks.get(fixture)
            if bank is not None:
                return list(bank.openocd.log[-1000:])
        return extra[-1000:]

    # -- fixture execution --------------------------------------------
    def _run_fixture(self, worker: _Worker) -> None:
        ctx = worker.ctx
        profile = self.require_profile()
        runtime = worker.runtime
        fixture = worker.fixture
        result = RunResult.ERROR
        failed_step: str | None = None
        reason = ""
        try:
            self._update_status(
                fixture,
                state=FixtureState.RUNNING,
                message="registering insertion",
                run_id=ctx.run_id,
            )
            insertions = self.db.register_insertion(fixture)
            if insertions >= self.config.maintenance_count:
                self._emit(
                    "maintenance",
                    fixture=fixture,
                    message=f"fixture maintenance due after {insertions} insertions",
                )
            runtime.abort = worker.abort
            runtime.power = self._power.get(fixture)
            if runtime.power is not None and self.config.fixture(fixture).power:
                try:
                    runtime.power.on()
                except PowerError as exc:
                    self._log(fixture, f"power on failed: {exc}")
            runtime.backend = self._open_backend(fixture)
            runtime.backend.start()
            if self.simulation and self.simulator is not None:
                runtime.bank = self.simulator.bank(fixture)
                runtime.bank.power_cycle()
            steps = self._build_steps(profile, ctx)
            total = len(steps)
            for index, step in enumerate(steps, 1):
                if worker.abort.is_set():
                    raise AbortedError("aborted by operator")
                self._update_status(
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
                outcome = self._execute_step(worker, step, index)
                if outcome is None:
                    outcome = StepOutcome()
                self._log(
                    fixture,
                    f"step {index:02d} {step.name}: {outcome.status}"
                    + (f" ({outcome.detail})" if outcome.detail else ""),
                )
                self._update_status(
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
            reason = self._safe_reason(ctx, exc.reason)
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
            reason = self._safe_reason(ctx, str(exc))
        except Exception as exc:  # pragma: no cover - last-resort isolation
            result = RunResult.ERROR
            failed_step = worker.status.step_name or None
            reason = self._safe_reason(ctx, f"{type(exc).__name__}: {exc}")
            self._log(fixture, traceback.format_exc())
        finally:
            if runtime.allocation is not None and not runtime.serial_consumed:
                try:
                    self.serial_service.release(runtime.allocation)
                except Exception as exc:
                    self._log(fixture, f"could not release allocation: {exc}")
            runtime.close()
            worker.result = result.value
            worker.error = reason
            finished = utcnow()
            try:
                self.db.close_run(
                    ctx.run_id,
                    result.value,
                    failed_step=failed_step,
                    reason=reason,
                    finished_at=finished,
                    label_pending=ctx.label_pending,
                )
                if ctx.uid:
                    self.db.upsert_unit(
                        ctx.uid,
                        pcb_serial=ctx.pcb_serial,
                        serial_number=ctx.serial_number,
                        mac=ctx.mac,
                        product_id=profile.profile_id,
                        hw_revision=ctx.hw_revision,
                        last_result=result.value,
                    )
                self._save_extras(ctx)
                self.db.register_run(fixture, result.value)
                self.db.audit(
                    ctx.operator,
                    "run.finish",
                    f"{ctx.run_id} {result.value} {failed_step or ''} {reason}",
                )
                if self.mes.enabled:
                    self.mes.queue(ctx.run_id)
            except Exception as exc:  # pragma: no cover
                self._log(fixture, f"finalisation failed: {exc}")
            try:
                elapsed = time.monotonic() - worker.started_monotonic
                self._update_status(
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
            self._emit(
                "run_finished",
                fixture=fixture,
                run_id=ctx.run_id,
                result=result.value,
                failed_step=failed_step,
                reason=reason,
                serial=ctx.serial_number,
            )

    def _execute_step(self, worker: _Worker, step: StepDef, seq: int) -> StepOutcome:
        started_at = utcnow()
        started = time.monotonic()
        self.db.add_step(
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
                self.db.add_step(
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
                self.db.add_step(
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
                self._log(
                    worker.fixture,
                    f"step {step.name} attempt {attempt}/{attempts} failed: {exc.reason}",
                )
                if attempt >= attempts:
                    duration = int((time.monotonic() - started) * 1000)
                    self.db.add_step(
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
                        self._record_test_results(worker.ctx, exc.test_results)
                    raise
                time.sleep(0.3)
            except (OpenOcdError, HwLinkError, OSError) as exc:
                last_exc = exc
                self._log(
                    worker.fixture,
                    f"step {step.name} attempt {attempt}/{attempts} I/O error: {exc}",
                )
                if attempt >= attempts:
                    duration = int((time.monotonic() - started) * 1000)
                    self.db.add_step(
                        worker.ctx.run_id,
                        seq,
                        step.name,
                        StepResult.ERROR.value,
                        started_at,
                        duration,
                        str(exc),
                    )
                    raise StepFailure(str(exc)) from exc
                time.sleep(0.5 * attempt)
        raise StepFailure(str(last_exc) if last_exc else "step failed")

    def step_names(self, profile: ProductProfile | None = None) -> list[str]:
        profile = profile or self.require_profile()
        return [step.name for step in self._build_steps(profile, None)]

    def _build_steps(self, profile: ProductProfile, ctx: RunContext | None) -> list[StepDef]:
        step_config = profile.manifest.get("steps") or {}
        if not isinstance(step_config, dict):
            step_config = {}
        enabled = step_config.get("enabled") or {}
        retries_cfg = step_config.get("retries") or {}
        retries: dict[str, int] = {}
        if isinstance(retries_cfg, dict):
            retries = {str(k): int(v) for k, v in retries_cfg.items()}
        defaults = {
            "scan": 0,
            "identify": 1,
            "prepare": 1,
            "test": 1,
            "calibrate": 0,
            "program": 1,
            "provision": 0,
            "boot_check": 2,
            "protect": 0,
            "label": 1,
            "record": 0,
        }
        definitions = [
            ("scan", "Scan board", self._step_scan),
            ("identify", "Identify chip", self._step_identify),
            ("prepare", "Prepare test image", self._step_prepare),
            ("test", "Run EOL test plan", self._step_test),
            ("calibrate", "Compute calibration", self._step_calibrate),
            ("program", "Program production", self._step_program),
            ("provision", "Provision unit", self._step_provision),
            ("boot_check", "Boot check", self._step_boot_check),
            ("protect", "Apply protection", self._step_protect),
            ("label", "Print label", self._step_label),
            ("record", "Record run", self._step_record),
        ]
        steps: list[StepDef] = []
        for key, name, handler in definitions:
            if key == "calibrate" and not profile.calibration:
                continue
            if key in ("protect", "label") and enabled.get(key) is False:
                continue
            if key == "calibrate" and enabled.get(key) is False:
                continue
            steps.append(
                StepDef(
                    key=key,
                    name=name,
                    handler=handler,
                    retries=retries.get(key, defaults.get(key, 0)),
                )
            )
        return steps

    def _save_extras(self, ctx: RunContext) -> None:
        try:
            self.db.save_run_extras(
                ctx.run_id,
                option_bytes=ctx.option_bytes,
                calibration=ctx.calibration,
                image_versions=ctx.image_versions,
                metadata=ctx.metadata,
                provisioning_blob=ctx.provisioning_blob,
                record_revision=ctx.record_revision,
                label_pending=ctx.label_pending,
            )
        except Exception as exc:
            self._log(ctx.fixture, f"could not persist run extras: {exc}")

    # -- individual steps --------------------------------------------
    def _step_scan(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        worker = self._worker(ctx.fixture)
        if not ctx.pcb_serial:
            ctx.pcb_serial = self._wait_for_scan(worker)
        pattern = ""
        if self.profile is not None:
            identity_raw = self.profile.manifest.get("identity") or {}
            if isinstance(identity_raw, dict):
                pattern = str(identity_raw.get("pcb_pattern") or "")
        pattern = pattern or r"^[A-Za-z0-9][A-Za-z0-9._:/-]{3,63}$"
        if not re.fullmatch(pattern, ctx.pcb_serial):
            raise StepFailure(f"invalid board barcode {ctx.pcb_serial!r}")
        previous = [
            row for row in self.db.runs_for_pcb(ctx.pcb_serial) if int(row["id"]) != ctx.run_id
        ]
        if previous and ctx.kind == RunKind.FIRST:
            raise StepFailure(f"board {ctx.pcb_serial} already has runs; start a retest or rework")
        passed = [row for row in previous if str(row["result"]) == "PASS"]
        if passed and ctx.kind != RunKind.REWORK:
            raise StepFailure(
                f"board {ctx.pcb_serial} already passed in run {passed[0]['id']}; use rework"
            )
        if self.simulation and self.simulator is not None:
            self.simulator.bank(ctx.fixture).select_board(ctx.pcb_serial)
        self.db.update_run_identity(ctx.run_id, pcb_serial=ctx.pcb_serial)
        self._update_status(ctx.fixture, pcb_serial=ctx.pcb_serial, state=FixtureState.RUNNING)
        return StepOutcome(detail=f"pcb={ctx.pcb_serial}")

    def _wait_for_scan(self, worker: _Worker) -> str:
        self._update_status(
            worker.fixture,
            state=FixtureState.LOAD,
            message="scan the board barcode",
        )
        deadline = time.monotonic() + SCAN_TIMEOUT_S
        while time.monotonic() < deadline:
            if worker.abort.is_set():
                raise AbortedError("aborted while waiting for a scan")
            try:
                return worker.scan_queue.get(timeout=0.2)
            except queue.Empty:
                continue
        raise StepFailure("no board scan received")

    def _step_identify(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        backend.halt()
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

    def _step_prepare(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        options = backend.read_option_bytes()
        if options.rdp != 0:
            if ctx.kind != RunKind.REWORK or not ctx.metadata.get("rework_approved"):
                raise StepFailure(
                    f"chip is read-protected (RDP {options.rdp}); a supervisor rework "
                    "must lower RDP first (all flash content will be lost)"
                )
            self._log(ctx.fixture, "Lowering RDP for rework; all flash content is lost")
            lower_rdp_for_rework(backend, confirmed=True, operator=ctx.operator)
            self.db.audit(
                ctx.operator,
                "protection.rdp_lower",
                f"run {ctx.run_id}: RDP lowered to 0 for rework; chip mass-erased",
            )
        bench = ProtectionSpec(
            ndbank=1,
            ndboot=0,
            bor_lev=3,
            wrp_bank1=[],
            wrp_bank2=[],
            rdp=0,
        )
        apply_protection(backend, bench)
        backend.mass_erase()
        ctx.metadata["safe_state"] = "erased"
        test_path = profile.image_path("test")
        backend.program(test_path, verify=True)
        image = profile.test_image.image
        if image is None:
            raise StepFailure("test image was not parsed")
        self._verify_segments(backend, image)
        rt.test_image = image
        if rt.bank is not None:
            rt.bank.prepare_test_image(
                profile.test_image.sha256,
                image.name or test_path.stem,
                image.version,
                test_path,
            )
        backend.reset(halt=False)
        ctx.metadata["safe_state"] = "test_programmed"
        self._save_extras(ctx)
        return StepOutcome(detail=f"nDBANK=1, erased, programmed {test_path.name}, verified")

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

    def _step_program(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        options = backend.read_option_bytes()
        if options.rdp != 0:
            if ctx.kind != RunKind.REWORK or not ctx.metadata.get("rework_approved"):
                raise StepFailure("chip is read-protected; rework is required")
            lower_rdp_for_rework(backend, confirmed=True, operator=ctx.operator)
            self.db.audit(
                ctx.operator,
                "protection.rdp_lower",
                f"run {ctx.run_id}: RDP lowered to 0 for rework; chip mass-erased",
            )
        protection = profile.protection
        production = ProtectionSpec(
            ndbank=protection.ndbank,
            ndboot=protection.ndboot,
            bor_lev=protection.bor_lev,
            wrp_bank1=[],
            wrp_bank2=[],
            rdp=0,
            boot_addr0=protection.boot_addr0,
            boot_addr1=protection.boot_addr1,
        )
        apply_protection(backend, production)
        backend.mass_erase()
        ctx.metadata["safe_state"] = "erased"
        boot_path = profile.image_path("boot")
        backend.program(boot_path, verify=True)
        boot_image = profile.boot_image.image
        if boot_image is not None:
            self._verify_segments(backend, boot_image)
        app_path = profile.image_path("app")
        app = profile.app_image
        if app.image is None:
            raise StepFailure("application image was not parsed")
        app_load = self._app_load(profile)
        metadata_base = self._metadata_address(profile)
        if app_load == "boot":
            # B-13: BOOT is already flashed and the board was mass-erased, so
            # it stays in update mode.  Upload the IBWH image and let BOOT
            # erase the slot, verify the CRC and write its own metadata.
            if rt.bank is not None:
                rt.bank.device.set_role("BOOT", "BOOT", 0)
            backend.reset(halt=False)
            self._close_session(rt)
            session = self._open_session(ctx.fixture, rt)
            hello = self._wait_hello(
                session,
                timeout=self._boot_timeout(profile),
                abort_event=rt.abort,
                expected_role="BOOTLOADER",
            )
            if str(hello.get("role", "")).upper() not in ("BOOTLOADER", "BOOT"):
                raise StepFailure("BOOT did not enter update mode for the VCP load")
            try:
                result = session.update_image(
                    app.image,
                    abort_event=rt.abort,
                    progress=lambda done, total: self._update_status(
                        ctx.fixture, message=f"BOOT update {done}/{total}"
                    ),
                )
            finally:
                self._close_session(rt)
            if not bool(result.get("success")):
                raise StepFailure(f"BOOT update failed: {result}")
            if rt.bank is not None:
                rt.bank.prepare_app_image(
                    app.sha256,
                    app.image.name or app_path.stem,
                    app.image.version,
                    app_path,
                )
            ctx.metadata["app_load"] = "boot"
            ctx.metadata["boot_update"] = {
                "crc32": int(result.get("image_crc32", 0)),
                "detail": str(result.get("detail", "")),
            }
            ctx.metadata["boot_metadata"] = {
                "address": metadata_base,
                "verified_crc32": app.image.crc32,
                "sequence": 1,
                "writer": "BOOT",
            }
            ctx.image_versions["boot"] = boot_image.version_string() if boot_image else ""
            ctx.image_versions["app"] = app.image.version_string()
            ctx.metadata["safe_state"] = "app_programmed"
            return StepOutcome(
                detail=(
                    f"BOOT + app v{app.image.version_string()} loaded over VCP, "
                    f"CRC32=0x{app.image.crc32:08X}"
                )
            )
        address = self._slot_a_address(profile)
        self._program_payload(backend, app.image, address)
        readback = backend.read_memory(address, len(app.image.data))
        if readback != app.image.data:
            raise StepFailure("application read-back mismatch")
        import zlib

        if zlib.crc32(readback) & 0xFFFFFFFF != app.image.crc32:
            raise StepFailure("application CRC32 mismatch after programming")
        records = app.image.to_metadata_records(slot=0, sequence=1)
        offsets = app.image.metadata_offsets()
        # Appendix B: erase the metadata sector once, then program the header
        # and records append-only.  Erasing per record would destroy the
        # header written immediately before it.
        backend.erase_address(metadata_base, layout.META_SIZE)
        for index, (offset, record) in enumerate(zip(offsets, records, strict=True)):
            meta_address = metadata_base + offset
            backend.write_memory(meta_address, record)
            if backend.read_memory(meta_address, len(record)) != record:
                raise StepFailure(f"boot metadata record {index} read-back mismatch")
        header_magic = backend.read_u32(metadata_base)
        if header_magic != layout.IMAGE_MAGIC:
            raise StepFailure("BOOT metadata header magic was not written")
        if rt.bank is not None:
            rt.bank.prepare_app_image(
                app.sha256,
                app.image.name or app_path.stem,
                app.image.version,
                app_path,
            )
        ctx.metadata["app_load"] = "swd"
        rt.app_image = app.image
        ctx.image_versions["boot"] = boot_image.version_string() if boot_image else ""
        ctx.image_versions["app"] = app.image.version_string()
        ctx.metadata["boot_metadata"] = {
            "address": metadata_base,
            "verified_crc32": app.image.crc32,
            "sequence": 1,
            "writer": "factory",
        }
        ctx.metadata["safe_state"] = "app_programmed"
        backend.reset(halt=True)
        return StepOutcome(
            detail=(
                f"BOOT + app v{app.image.version_string()} at {address:#010x}, "
                f"CRC32=0x{app.image.crc32:08X}, metadata at {metadata_base:#010x}"
            )
        )

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

    def _step_boot_check(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        self._close_session(rt)
        backend.reset(halt=False)
        session = self._open_session(ctx.fixture, rt)
        hello = self._wait_hello(
            session,
            timeout=self._boot_timeout(profile),
            abort_event=rt.abort,
            expected_role=None,
        )
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
                raise StepFailure(f"booted chip UID {reported_uid:#x} does not match {_uid64(ctx.uid):#x}")
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
            rec_magic, _value, present = parse_metadata_record(meta[offset : offset + layout.META_RECORD_SIZE])
            if not present or rec_magic != expected_magic:
                raise StepFailure(f"BOOT metadata {label} record is missing or invalid")
        expected_version = (
            "0.0.0"
            if profile.app_image.image is None
            else profile.app_image.image.version_string()
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

    def _step_protect(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        spec = profile.protection
        if spec.rdp == 2:
            raise StepFailure("RDP level 2 is never allowed")
        # E-01: every SWD readback happens before RDP 1.  Apply WRP/BOR first
        # (RDP still 0), read it back, then lower the debugger last.
        if spec.rdp == 1:
            before = ProtectionSpec(
                ndbank=spec.ndbank,
                ndboot=spec.ndboot,
                bor_lev=spec.bor_lev,
                wrp_bank1=spec.wrp_bank1,
                wrp_bank2=spec.wrp_bank2,
                rdp=0,
                boot_addr0=spec.boot_addr0,
                boot_addr1=spec.boot_addr1,
            )
            apply_protection(backend, before)
        values = apply_protection(backend, spec)
        # Power-cycle equivalent: reset, re-examine and read the options once
        # more so a write that only landed in a register is caught.
        backend.reset(halt=True)
        actual = backend.read_option_bytes()
        if actual.rdp != spec.rdp:
            raise StepFailure(f"RDP read-back {actual.rdp} != {spec.rdp} after reset")
        if actual.rdp == 2:
            raise StepFailure("chip unexpectedly reports RDP level 2")
        ctx.option_bytes = values
        ctx.metadata["safe_state"] = "protected"
        ctx.metadata["protection_readback"] = option_bytes_to_dict(actual)
        # E-04: with RDP 1 + WRP applied, the product must still boot and
        # answer HWLINK.  No SWD flash read is attempted here.
        if spec.rdp == 1:
            backend.reset(halt=False)
            self._close_session(rt)
            session = self._open_session(ctx.fixture, rt)
            hello = self._wait_hello(
                session,
                timeout=self._boot_timeout(profile),
                abort_event=rt.abort,
            )
            self._close_session(rt)
            protected_role = str(hello.get("role", "")).upper()
            if protected_role in ("HWVAL", "BOOTLOADER", "BOOT"):
                raise StepFailure(
                    f"after protection the unit boots {protected_role}, not the application"
                )
            ctx.metadata["protection_boot_check"] = {
                "role": protected_role,
                "firmware": hello.get("firmware_name", ""),
                "version": hello.get("version", ""),
            }
            ctx.metadata["safe_state"] = "protected_boot_checked"
        self._save_extras(ctx)
        self.db.audit(
            ctx.operator,
            "protection.apply",
            f"run {ctx.run_id}: {spec.as_dict()}",
        )
        return StepOutcome(detail=str(values))

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

    def _step_record(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        self.db.checkpoint()
        self.db.audit(
            ctx.operator,
            "run.record",
            f"{ctx.run_id} profile={ctx.profile_id}@{ctx.profile_version}",
        )
        ctx.metadata["committed"] = True
        return StepOutcome(detail="run committed; MES queue updated at close")

    # -- backend/session helpers --------------------------------------
    def _open_backend(self, fixture: str) -> OpenOcdBackend:
        if self.simulation and self.simulator is not None:
            bank = self.simulator.bank(fixture)
            bank.openocd.log_callback = lambda text, name=fixture: self._log(name, text)
            return bank.openocd
        config = self.config.fixture(fixture)

        def _log_callback(text: str) -> None:
            self._log(fixture, text)

        backend = OpenOcdProcess(
            config.probe_serial,
            openocd=self.config.openocd.binary,
            interface_cfg=self.config.openocd.interface_cfg,
            target_cfg=self.config.openocd.target_cfg,
            telnet_port=config.telnet_port,
            log_callback=_log_callback,
        )
        return backend

    def _backend(self, rt: RunRuntime) -> OpenOcdBackend:
        if rt.backend is None:
            raise StepFailure("OpenOCD backend is not available")
        return rt.backend

    def _open_session(self, fixture: str, rt: RunRuntime) -> HwLinkSession:
        transport: Any
        if self.simulation and self.simulator is not None and rt.bank is not None:
            transport = SimulatedTransport(rt.bank.device)
        else:
            config = self.config.fixture(fixture)
            if config.serial_port:
                transport = SerialTransport(config.serial_port)
            else:
                transport = SerialTransport.find_by_probe_serial(config.probe_serial)
        session = HwLinkSession(
            transport,
            prompt_handler=lambda payload: self._prompt(fixture, payload),
            log_handler=lambda text: self._log(fixture, text),
            timeout=5.0,
        )
        rt.session = session
        return session

    def _close_session(self, rt: RunRuntime) -> None:
        if rt.session is not None:
            with contextlib.suppress(Exception):
                rt.session.close()
            rt.session = None

    def _wait_hello(
        self,
        session: HwLinkSession,
        *,
        timeout: float,
        abort_event: threading.Event | None = None,
        expected_role: str | int | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            if abort_event is not None and abort_event.is_set():
                raise AbortedError("aborted by operator")
            try:
                return session.connect(
                    expected_role,
                    timeout=min(3.0, max(0.5, deadline - time.monotonic())),
                )
            except HwLinkError as exc:
                last = exc
                if "expected" in str(exc) and expected_role is not None:
                    # Wrong firmware is a real failure, not a transient one.
                    raise StepFailure(str(exc)) from exc
                time.sleep(0.2)
        raise StepFailure(f"firmware did not answer HWLINK hello: {last}")

    def _hello_timeout(self, profile: ProductProfile) -> float:
        raw = profile.manifest.get("hello_timeout_s")
        try:
            return float(raw) if raw else 10.0
        except (TypeError, ValueError):
            return 10.0

    def _boot_timeout(self, profile: ProductProfile) -> float:
        raw = (profile.manifest.get("boot_check") or {}).get("timeout_s")
        try:
            return float(raw) if raw else 15.0
        except (TypeError, ValueError, AttributeError):
            return 15.0

    def _prompt(self, fixture: str, payload: dict[str, Any]) -> str:
        self._log(fixture, f"operator prompt: {payload.get('text', '')}")
        if self.prompt_handler is None:
            answers = payload.get("answers_list") or payload.get("choices") or ["OK"]
            answer = str(answers[0])
        else:
            answer = str(self.prompt_handler(fixture, payload))
        ctx = self.worker_ctx(fixture)
        if ctx is not None:
            ctx.metadata.setdefault("operator_prompts", []).append(
                {
                    "test_id": str(payload.get("test_id", "")),
                    "prompt_id": str(payload.get("prompt_id", "")),
                    "text": str(payload.get("text", "")),
                    "answer": answer,
                }
            )
        return answer

    def _verify_segments(self, backend: OpenOcdBackend, image: FirmwareImage) -> None:
        import zlib

        for segment in image.segments:
            actual = backend.read_memory(segment.address, len(segment.data))
            if actual != segment.data:
                raise StepFailure(f"flash verify failed at {segment.address:#010x}")
        combined = b"".join(
            backend.read_memory(segment.address, len(segment.data)) for segment in image.segments
        )
        if zlib.crc32(combined) & 0xFFFFFFFF != image.crc32:
            raise StepFailure("flash CRC32 does not match the image")

    def _program_payload(self, backend: OpenOcdBackend, image: FirmwareImage, address: int) -> None:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
            handle.write(image.data)
            name = Path(handle.name)
        try:
            backend.program(name, address=address, verify=True)
        finally:
            with contextlib.suppress(OSError):
                name.unlink()

    def _app_load(self, profile: ProductProfile) -> str:
        value = str(profile.manifest.get("app_load") or "swd").strip().lower()
        if value not in ("swd", "boot"):
            raise StepFailure(f"unsupported app_load {value!r}; use 'swd' or 'boot'")
        return value

    def _slot_a_address(self, profile: ProductProfile) -> int:
        raw = profile.manifest.get("layout") or {}
        if isinstance(raw, dict) and raw.get("slot_a"):
            value = int(str(raw["slot_a"]), 0)
            if value != layout.SLOT_A_BASE:
                raise StepFailure(
                    f"profile slot A {value:#010x} != layout {layout.SLOT_A_BASE:#010x}"
                )
        app = profile.images.get("app")
        if app is not None and app.address not in (None, layout.SLOT_A_BASE):
            raise StepFailure(
                f"profile app address {app.address:#010x} != layout {layout.SLOT_A_BASE:#010x}"
            )
        return layout.SLOT_A_BASE

    def _metadata_address(self, profile: ProductProfile) -> int:
        raw = profile.manifest.get("boot_metadata") or profile.manifest.get("metadata") or {}
        if isinstance(raw, dict) and raw.get("address"):
            value = int(str(raw["address"]), 0)
            if value != layout.META_A_BASE:
                raise StepFailure(
                    f"profile metadata {value:#010x} != layout {layout.META_A_BASE:#010x}"
                )
        return layout.META_A_BASE

    # -- self test and probes ----------------------------------------
    def detect_probes(self) -> list[str]:
        if self.simulation:
            return [f.probe_serial for f in self.config.fixtures if f.probe_serial]
        probes: set[str] = set()
        try:
            from serial.tools import list_ports

            for info in list_ports.comports():
                haystack = " ".join(
                    str(x) for x in (info.serial_number, info.hwid, info.description)
                )
                if (
                    "st-link" in haystack.lower() or "stlink" in haystack.lower()
                ) and info.serial_number:
                    probes.add(info.serial_number)
        except Exception:
            pass
        try:
            proc = subprocess.run(
                [
                    self.config.openocd.binary,
                    "-c",
                    "adapter list",
                    "-c",
                    "shutdown",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in (proc.stdout + proc.stderr).splitlines():
                match = re.search(r"serial(?: number)?[=: ]+([0-9A-Za-z:-]+)", line, re.I)
                if match:
                    probes.add(match.group(1))
        except (OSError, subprocess.TimeoutExpired):
            pass
        return sorted(probes)

    def missing_probes(self, detected: list[str] | None = None) -> list[str]:
        probes = set(detected if detected is not None else self.detect_probes())
        return [
            f.name for f in self.config.fixtures if f.probe_serial and f.probe_serial not in probes
        ]

    def probe_fixture(self, fixture: str) -> list[dict[str, Any]]:
        """B-12/G-02: step-by-step SWD + VCP bring-up for one fixture."""
        config = self.config.fixture(fixture)
        steps: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            steps.append({"name": name, "ok": ok, "detail": detail})

        add("probe configured", bool(config.probe_serial), config.probe_serial or "missing")
        backend = self._open_backend(fixture)
        try:
            backend.start()
            add("OpenOCD start", True, f"telnet {config.telnet_port}")
            backend.halt()
            idcode = backend.read_u32(DBGMCU_IDCODE)
            dev_id = idcode & 0xFFF
            rev_id = (idcode >> 16) & 0xFFFF
            add("IDCODE", True, f"DEV_ID=0x{dev_id:03X} REV_ID=0x{rev_id:04X}")
            flash_kb = backend.read_u16(FLASH_SIZE_REG)
            add("flash size", True, f"{flash_kb} KB")
            uid = backend.read_memory(UID_BASE, 12)
            add("UID", len(uid) == 12, uid.hex().upper())
            options = backend.read_option_bytes()
            add("option bytes", True, str(option_bytes_to_dict(options)))
        except (OpenOcdError, OSError) as exc:
            add("SWD", False, str(exc))
        finally:
            with contextlib.suppress(Exception):
                backend.stop()
        transport: Any
        try:
            if self.simulation and self.simulator is not None:
                transport = SimulatedTransport(self.simulator.bank(fixture).device)
            elif config.serial_port:
                transport = SerialTransport(config.serial_port)
            else:
                transport = SerialTransport.find_by_probe_serial(config.probe_serial)
        except HwLinkError as exc:
            add("VCP open", False, str(exc))
            return steps
        session = HwLinkSession(transport, timeout=5.0)
        try:
            hello = session.connect(timeout=10.0)
            add(
                "HWLINK Hello",
                True,
                f"role={hello.get('role')} fw={hello.get('firmware_name')} "
                f"version={hello.get('version')}",
            )
            role = str(hello.get("role", "")).upper()
            if role == "HWVAL":
                info = session.device_info()
                add(
                    "GetDeviceInfo",
                    True,
                    f"uid={info.get('uid')} optcr=0x{int(info.get('optcr', 0)):08X}",
                )
            elif role in ("DEMO", "APP"):
                status = session.status()
                add(
                    "GetStatus",
                    True,
                    f"slot={status.get('slot_name')} trial={status.get('trial_name')}",
                )
            else:
                boot = session.boot_info()
                add("GetBootInfo", True, f"active={boot.get('active_slot_name')}")
        except HwLinkError as exc:
            add("HWLINK Hello", False, str(exc))
        finally:
            session.close()
        return steps

    def run_self_test(self, fixture: str, operator: str = "supervisor") -> bool:
        config = self.config.fixture(fixture)
        detail = ""
        passed = False
        backend: OpenOcdBackend | None = None
        try:
            backend = self._open_backend(fixture)
            backend.start()
            backend.halt()
            idcode = backend.read_u32(DBGMCU_IDCODE)
            uid = backend.read_memory(UID_BASE, 12).hex().upper()
            dev_id = idcode & 0xFFF
            golden = getattr(config, "golden_uid", "") or ""
            passed = dev_id == 0x451 and (not golden or uid == golden.upper())
            detail = f"DEV_ID=0x{dev_id:03X} UID={uid}"
            if not passed:
                detail += f" (expected UID {golden})"
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            passed = False
        finally:
            if backend is not None:
                with contextlib.suppress(Exception):
                    backend.stop()
        self.db.add_self_test(fixture, "PASS" if passed else "FAIL", operator, detail)
        self.db.audit(operator, "station.self_test", f"{fixture} {'PASS' if passed else 'FAIL'}")
        self._emit(
            "self_test",
            fixture=fixture,
            result="PASS" if passed else "FAIL",
            detail=detail,
        )
        return passed

    def self_test_warnings(self) -> list[str]:
        warnings: list[str] = []
        interval = self.config.self_test_interval_hours
        for fixture in self.fixture_names():
            age = self.db.self_test_age_hours(fixture)
            if age is None:
                warnings.append(f"{fixture}: no passing self-test recorded")
            elif age > interval:
                warnings.append(
                    f"{fixture}: no passing self-test for {age:.1f} h (interval {interval} h)"
                )
        return warnings

    def maintenance_warnings(self) -> list[str]:
        warnings: list[str] = []
        for fixture in self.fixture_names():
            stats = self.db.fixture_stats(fixture)
            if stats is None:
                continue
            insertions = int(stats["insertions"])
            if insertions >= self.config.maintenance_count:
                warnings.append(
                    f"{fixture}: {insertions} insertions, maintenance due at "
                    f"{self.config.maintenance_count}"
                )
        return warnings

    # -- status/log/events --------------------------------------------
    def _worker(self, fixture: str) -> _Worker:
        worker = self._workers.get(fixture)
        if worker is None:
            raise StationError(f"fixture {fixture} has no active run")
        return worker

    def _update_status(self, fixture: str, **fields: Any) -> None:
        with self._lock:
            status = self._statuses.get(fixture)
            if status is None:
                return
            for key, value in fields.items():
                if hasattr(status, key):
                    setattr(status, key, value)
            self._emit("status", fixture=fixture, status=status)

    def _log(self, fixture: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            buf = self._log_buffers.setdefault(fixture, [])
            buf.append(text)
            if len(buf) > 2000:
                del buf[:1000]
        if self.log_handler:
            with contextlib.suppress(Exception):
                self.log_handler(fixture, text)

    def _emit(self, kind: str, **payload: Any) -> None:
        if self.on_event is None:
            return
        with contextlib.suppress(Exception):
            self.on_event({"kind": kind, **payload})

    def _safe_reason(self, ctx: RunContext, reason: str) -> str:
        safe = ctx.metadata.get("safe_state", "unknown")
        return f"{reason} [flash state: {safe}]"

    def _mes_queue_changed(self, length: int) -> None:
        self._last_mes_queue = length
        self._emit("mes_queue", length=length)

    def mes_queue_length(self) -> int:
        return self.mes.queue_length()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.abort_all()
        for worker in list(self._workers.values()):
            if worker.thread is not None:
                worker.thread.join(timeout=10)
        self.mes.stop()
        if self.profile is not None:
            self.profile.cleanup()
        for driver in self._power.values():
            with contextlib.suppress(Exception):
                driver.close()


def _uid_word_hex(uid_hex: str) -> str:
    """Render the UID the way the firmware's DeviceInfo does: word0..word2."""
    raw = bytes.fromhex(uid_hex)
    if len(raw) < 12:
        return uid_hex.upper()
    return "".join(f"{int.from_bytes(raw[i : i + 4], 'little'):08X}" for i in (0, 4, 8))


def _uid64(uid_hex: str) -> int:
    """Match the firmware's UID64: UID word0 << 32 | UID word1."""
    raw = bytes.fromhex(uid_hex)
    if len(raw) < 8:
        return 0
    return (int.from_bytes(raw[0:4], "little") << 32) | int.from_bytes(raw[4:8], "little")


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
