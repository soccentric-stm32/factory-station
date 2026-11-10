"""EOL step implementation: scan (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

import queue
import re
import time
from typing import Any

from hwval_factory.models import (
    FixtureState,
    RunContext,
    RunKind,
)
from hwval_factory.steps.base import AbortedError, RunRuntime, StepFailure, StepOutcome
from hwval_factory.steps.constants import (
    SCAN_TIMEOUT_S,
)


class ScanSteps:
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
        self._check_board_present(ctx.fixture)
        if self.simulation and self.simulator is not None:
            self.simulator.bank(ctx.fixture).select_board(ctx.pcb_serial)
        self.db.update_run_identity(ctx.run_id, pcb_serial=ctx.pcb_serial)
        self._update_status(ctx.fixture, pcb_serial=ctx.pcb_serial, state=FixtureState.RUNNING)
        return StepOutcome(detail=f"pcb={ctx.pcb_serial}")

    def _wait_for_scan(self, worker: Any) -> str:
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
