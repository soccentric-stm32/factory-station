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
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hwval_factory.adapters import HardwareAdapters
from hwval_factory.central import PostgresPool
from hwval_factory.config import StationConfig, validate_station_config
from hwval_factory.database import Database, utcnow
from hwval_factory.debuglog import create_debug_log
from hwval_factory.inputs import InputDriver, InputError, create_inputs
from hwval_factory.labels import LabelService
from hwval_factory.lifecycle import StationLifecycle
from hwval_factory.maintenance import MaintenanceHelpers
from hwval_factory.mes import MesService
from hwval_factory.models import (
    FixtureState,
    FixtureStatus,
    RunContext,
    RunKind,
    WorkOrder,
)
from hwval_factory.pipeline import execute_step, run_fixture
from hwval_factory.power import PowerDriver, PowerError, create_power_driver
from hwval_factory.profile import (
    ProductProfile,
    load_profile,
)
from hwval_factory.serials import (
    SerialService,
    format_mac,
    parse_oui_block,
)
from hwval_factory.simulator import SimulatedStation
from hwval_factory.steps.base import (
    RunRuntime,
    StepDef,
    StepOutcome,
)
from hwval_factory.steps.boot_check import BootCheckSteps
from hwval_factory.steps.calibrate import CalibrateSteps
from hwval_factory.steps.flowb import FlowBSteps
from hwval_factory.steps.helpers import HelperSteps
from hwval_factory.steps.identify import IdentifySteps
from hwval_factory.steps.label import LabelSteps
from hwval_factory.steps.prepare import PrepareSteps
from hwval_factory.steps.program import ProgramSteps
from hwval_factory.steps.protect import ProtectSteps
from hwval_factory.steps.provision import ProvisionSteps
from hwval_factory.steps.record import RecordSteps
from hwval_factory.steps.registry import build_steps, validate_recipe
from hwval_factory.steps.scan import ScanSteps
from hwval_factory.steps.self_test import SelfTestSteps
from hwval_factory.steps.test import TestSteps


class StationError(RuntimeError):
    """Station-level action could not be performed."""


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


class Station(
    HardwareAdapters,
    StationLifecycle,
    HelperSteps,
    FlowBSteps,
    ScanSteps,
    SelfTestSteps,
    IdentifySteps,
    PrepareSteps,
    TestSteps,
    CalibrateSteps,
    ProgramSteps,
    ProvisionSteps,
    BootCheckSteps,
    ProtectSteps,
    LabelSteps,
    RecordSteps,
    MaintenanceHelpers,
):



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
        self.central_pool = None
        if config.central_pool is not None and getattr(config.central_pool, "enabled", False):
            self.central_pool = PostgresPool(
                config.central_pool.url,
                station_id=config.central_pool.station_id or config.station_id,
            )
        self.serial_service = SerialService(db, central=self.central_pool)
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
        self._inputs: dict[str, dict[str, InputDriver]] = {}
        self._debug = {
            f.name: create_debug_log(f.debug_uart) for f in config.fixtures
        }
        for fixture in config.fixtures:
            try:
                self._power[fixture.name] = create_power_driver(fixture.power)
            except PowerError:
                self._power[fixture.name] = create_power_driver({})
            try:
                self._inputs[fixture.name] = create_inputs(fixture.inputs)
            except InputError:
                self._inputs[fixture.name] = {}
        self.mes.start()

    # -- profile and work order ---------------------------------------
    def set_profile(self, profile: ProductProfile) -> None:
        profile.check_factory_version()
        if profile.recipe:
            validate_recipe(profile.recipe, has_calibration=bool(profile.calibration))
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
        if not self.simulation:
            # F-05: production is blocked when the golden-board self-test is
            # missing or older than the configured interval.
            interval = self.config.self_test_interval_hours
            age = self.db.self_test_age_hours(fixture)
            if interval > 0 and (age is None or age > interval):
                raise StationError(
                    f"fixture {fixture} self-test is due "
                    f"({'never passed' if age is None else f'{age:.1f} h > {interval} h'})"
                )
            # F-06: warn at the maintenance count and block beyond the margin.
            stats = self.db.fixture_stats(fixture)
            insertions = 0 if stats is None else int(stats["insertions"])
            if insertions >= self.config.maintenance_count + self.config.maintenance_margin:
                raise StationError(
                    f"fixture {fixture} maintenance limit exceeded "
                    f"({insertions} insertions, reset required)"
                )
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


    def abort(self, fixture: str) -> None:
        with self._lock:
            worker = self._workers.get(fixture)
            if worker is not None and not worker.done.is_set():
                worker.abort.set()
                worker.status.message = "abort requested"
                self._emit("abort", fixture=fixture)


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
        run_fixture(self, worker)

    def _execute_step(self, worker: _Worker, step: StepDef, seq: int) -> StepOutcome:
        return execute_step(self, worker, step, seq)


    def step_names(self, profile: ProductProfile | None = None) -> list[str]:
        profile = profile or self.require_profile()
        return [step.name for step in self._build_steps(profile, None)]

    def _build_steps(self, profile: ProductProfile, ctx: RunContext | None) -> list[StepDef]:
        return build_steps(profile, self)

    # -- backend/session helpers --------------------------------------

    # -- self test and probes ----------------------------------------

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

    def _debug_tail(self, fixture: str, count: int = 20) -> list[str]:
        log = self._debug.get(fixture)
        return [] if log is None else log.tail(count)

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
        for drivers in self._inputs.values():
            for input_driver in drivers.values():
                with contextlib.suppress(Exception):
                    input_driver.close()
        for log in self._debug.values():
            with contextlib.suppress(Exception):
                log.stop()
