"""Step primitives shared by the station and the standalone tools.

The 2026-11-06 rethink (P1-02) moves each EOL step into ``hwval_factory.steps``.
A step is a named unit with explicit ``requires``/``provides`` facts, a retry
policy and a safe state.  ``RunRuntime`` keeps the per-run hardware handles;
``StepFailure`` carries the cause and the suggested action shown to the
operator (P1-09).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hostcore.images import FirmwareImage
from hostcore.openocd import OpenOcdBackend, OptionBytes
from hwval_factory.models import RunContext, StepResult, TestResult
from hwval_factory.power import PowerDriver
from hwval_factory.serials import Allocation


class StepFailure(RuntimeError):
    """A step failed, with an operator-readable cause and next action."""

    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        test_results: list[TestResult] | None = None,
        cause: str = "",
        action: str = "",
        expected: str = "",
        measured: str = "",
        debug_tail: list[str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.test_results = test_results or []
        self.cause = cause or reason
        # Every failure needs an action (P1-09).  A step can replace this with
        # something more specific.
        self.action = action or (
            "Check the fixture wiring and the board state, then retry; if it "
            "persists, use the recovery tool and record the station log."
        )
        self.expected = expected
        self.measured = measured
        self.debug_tail = debug_tail or []

    def as_text(self) -> str:
        parts = [self.reason]
        if self.measured or self.expected:
            parts.append(f"measured {self.measured or '—'}, expected {self.expected or '—'}")
        if self.cause and self.cause != self.reason:
            parts.append(f"cause: {self.cause}")
        parts.append(f"action: {self.action}")
        if self.debug_tail:
            parts.append("debug log: " + " | ".join(self.debug_tail[-20:]))
        return "; ".join(parts)


class AbortedError(RuntimeError):
    """The operator aborted the run."""


@dataclass(slots=True)
class StepOutcome:
    status: str = StepResult.PASS.value
    detail: str = ""


@dataclass(slots=True)
class RunRuntime:
    backend: OpenOcdBackend | None = None
    session: Any = None
    bank: Any = None
    options: OptionBytes | None = None
    allocation: Allocation | None = None
    serial_consumed: bool = False
    test_image: FirmwareImage | None = None
    app_image: FirmwareImage | None = None
    power: PowerDriver | None = None
    abort: Any = None

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


StepHandler = Callable[[RunContext, RunRuntime], StepOutcome | str | None]


@dataclass(slots=True)
class StepDef:
    key: str
    name: str
    handler: StepHandler
    retries: int = 0


@dataclass(slots=True)
class StepSpec:
    """Registry metadata for recipe validation and standalone tools."""

    key: str
    name: str
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    standalone: bool = False
    safe_state_after: str = ""
    default_retries: int = 0
    timeout_s: float = 0.0
    handler_name: str = ""
    handler: StepHandler | None = None


def describe_runtime(runtime: RunRuntime) -> dict[str, Any]:
    """Small debug helper used by the tools page and failure text."""
    return {
        "backend": runtime.backend is not None,
        "session": runtime.session is not None,
        "simulation": runtime.bank is not None,
        "power": runtime.power is not None,
    }


__all__ = [
    "AbortedError",
    "RunRuntime",
    "StepDef",
    "StepFailure",
    "StepHandler",
    "StepOutcome",
    "StepSpec",
    "describe_runtime",
]
