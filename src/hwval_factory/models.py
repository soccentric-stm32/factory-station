"""Domain types used across the Factory layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Role(StrEnum):
    OPERATOR = "operator"
    SUPERVISOR = "supervisor"
    ENGINEER = "engineer"
    ADMIN = "administrator"

    @property
    def rank(self) -> int:
        return {
            Role.OPERATOR: 1,
            Role.SUPERVISOR: 2,
            Role.ENGINEER: 3,
            Role.ADMIN: 4,
        }[self]


class FixtureState(StrEnum):
    IDLE = "IDLE"
    LOAD = "LOAD"
    RUNNING = "RUNNING"
    ACTION = "ACTION"
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"

    @property
    def is_terminal(self) -> bool:
        return self in (FixtureState.PASS, FixtureState.FAIL, FixtureState.ERROR)


class RunKind(StrEnum):
    FIRST = "FIRST"
    RETEST = "RETEST"
    REWORK = "REWORK"


class RunResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ABORTED = "ABORTED"
    INCOMPLETE = "INCOMPLETE"
    ERROR = "ERROR"


class StepResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ABORTED = "ABORTED"
    ERROR = "ERROR"
    RUNNING = "RUNNING"


@dataclass(slots=True)
class TestResult:
    test_id: str
    result: str
    measured_value: float | None = None
    measured_unit: str = ""
    limit_min: float | None = None
    limit_max: float | None = None
    reason: str = ""
    operator_answer: str | None = None


@dataclass(slots=True)
class StepRecord:
    seq: int
    name: str
    result: str
    started_at: str
    duration_ms: int | None = None
    detail: str = ""


@dataclass(slots=True)
class RunContext:
    """Mutable state of one unit's pass through the EOL flow."""

    run_id: int
    fixture: str
    operator: str
    kind: RunKind
    profile_id: str
    profile_version: str
    work_order: str | None = None
    pcb_serial: str = ""
    uid: str = ""
    serial_number: str = ""
    mac: str = ""
    hw_revision: str = ""
    record_revision: int = 0
    provisioning_blob: bytes = b""
    label_data: dict[str, Any] = field(default_factory=dict)
    step_records: list[StepRecord] = field(default_factory=list)
    test_results: list[TestResult] = field(default_factory=list)
    calibration: dict[str, Any] = field(default_factory=dict)
    image_versions: dict[str, str] = field(default_factory=dict)
    option_bytes: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    rework_of_uid: str | None = None
    supersedes: int | None = None
    started_at: str = ""
    finished_at: str | None = None
    result: str | None = None
    failed_step: str | None = None
    reason: str | None = None
    label_pending: bool = False


@dataclass(slots=True)
class WorkOrder:
    name: str
    profile_id: str
    quantity: int = 0
    serial_prefix: str = ""
    serial_start: int = 1
    serial_end: int = 0
    mac_start: str = ""
    mac_count: int = 0
    created_at: str = ""
    closed: bool = False


@dataclass(slots=True)
class FixtureStatus:
    name: str
    state: FixtureState = FixtureState.IDLE
    step_name: str = ""
    step_index: int = 0
    step_count: int = 0
    elapsed_s: float = 0.0
    pcb_serial: str = ""
    serial_number: str = ""
    last_result: str = ""
    failed_step: str = ""
    message: str = ""
    probe_serial: str = ""
    run_id: int | None = None
    progress: int = 0


@dataclass(slots=True)
class User:
    username: str
    role: Role
    pin_hash: str = ""
    badge: str = ""
    active: bool = True


@dataclass(slots=True)
class LabelJob:
    run_id: int
    data: dict[str, Any]
    reprint: int = 0
    user: str = ""
    printed_at: str = ""
    status: str = "PENDING"
    output_path: Path | None = None
