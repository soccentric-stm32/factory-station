"""Lifecycle helpers kept out of the Station orchestrator (P1-02)."""

from __future__ import annotations

from hwval_factory.models import FixtureState, RunKind


class StationLifecycle:
    def start_all(
        self,
        *,
        operator: str = "operator",
        kind: RunKind = RunKind.FIRST,
    ) -> list[str]:
        from hwval_factory.station import StationError

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

    def abort_all(self) -> None:
        for name in self.fixture_names():
            self.abort(name)

    def waiting_fixtures(self) -> list[str]:
        with self._lock:
            return [s.name for s in self._statuses.values() if s.state == FixtureState.LOAD]
