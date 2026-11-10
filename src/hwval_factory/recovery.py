"""Recovery tools for stuck, trial or read-protected boards (P1-08).

Every path is explicit and audited.  RDP level 2 is never touched; lowering
RDP 1 to 0 is a supervisor action with a reason code and it mass-erases the
chip.
"""

from __future__ import annotations

import contextlib
from typing import Any

from hostcore.boot import BootError, catch_boot, wait_for_application
from hostcore.hwlink import HwLinkError
from hostcore.openocd import OpenOcdError
from hwval_factory.protection import ProtectionError, lower_rdp_for_rework


class RecoveryError(RuntimeError):
    """A recovery action could not be completed."""


class RecoveryService:
    def __init__(self, station: Any) -> None:
        self.station = station

    def _open(self, fixture: str, *, connect_under_reset: bool | None = None) -> tuple[Any, Any]:
        backend = self.station._open_backend(
            fixture, connect_under_reset=connect_under_reset
        )
        runtime = _Runtime(backend)
        if self.station.simulation and self.station.simulator is not None:
            runtime.bank = self.station.simulator.bank(fixture)
            runtime.bank.power_cycle()
        return backend, runtime

    # -- BOOT catch ----------------------------------------------------
    def catch(self, fixture: str, *, timeout: float = 5.0) -> dict[str, Any]:
        """Catch BOOT with an SWD reset and 50 ms Hello persistence."""
        backend, runtime = self._open(fixture)
        session = None
        try:
            backend.start()
            session = self.station._open_session(fixture, runtime)
            hello, info = catch_boot(session, swd=backend, timeout=timeout)
            self.station.db.audit(
                "recovery",
                "recovery.catch",
                f"fixture={fixture} active={info.get('active_slot_name')} "
                f"outcome={info.get('last_outcome_name')}",
            )
            return {
                "hello": hello,
                "boot_info": info,
                "link_log": list(session.link_log),
            }
        except (BootError, HwLinkError, OpenOcdError) as exc:
            raise RecoveryError(f"BOOT was not caught: {exc}") from exc
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    session.close()
            with contextlib.suppress(Exception):
                backend.stop()

    # -- stuck trial ---------------------------------------------------
    def unstick_trial(self, fixture: str, *, timeout: float = 30.0) -> dict[str, Any]:
        """Reset and let BOOT finish/reject the trial, then return the app."""
        backend, runtime = self._open(fixture)
        session = None
        try:
            backend.start()
            session = self.station._open_session(fixture, runtime)
            hello = wait_for_application(session, swd=backend, timeout=timeout)
            status = {}
            if str(hello.get("role", "")).upper() in ("DEMO", "APP"):
                status = session.status()
            self.station.db.audit(
                "recovery",
                "recovery.unstick_trial",
                f"fixture={fixture} role={hello.get('role')} slot={status.get('slot_name')}",
            )
            return {"hello": hello, "status": status, "link_log": list(session.link_log)}
        except (BootError, HwLinkError, OpenOcdError) as exc:
            raise RecoveryError(f"trial did not end cleanly: {exc}") from exc
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    session.close()
            with contextlib.suppress(Exception):
                backend.stop()

    # -- RDP 1 -> 0 ----------------------------------------------------
    def unlock_rdp(
        self,
        fixture: str,
        *,
        operator: str,
        reason: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise RecoveryError("RDP unlock needs an explicit confirmation")
        if not reason.strip():
            raise RecoveryError("RDP unlock needs a reason code")
        backend, _runtime = self._open(fixture, connect_under_reset=True)
        try:
            backend.start()
            before = backend.read_option_bytes()
            if before.rdp == 2:
                raise RecoveryError("chip is RDP level 2 (permanent); no recovery is possible")
            values = lower_rdp_for_rework(backend, confirmed=True, operator=operator)
            self.station.db.audit(
                operator,
                "protection.rdp_lower",
                f"fixture={fixture} reason={reason}; chip mass-erased",
            )
            return {"before": {"rdp": before.rdp}, "after": values, "reason": reason}
        except (ProtectionError, OpenOcdError) as exc:
            raise RecoveryError(f"RDP unlock failed: {exc}") from exc
        finally:
            with contextlib.suppress(Exception):
                backend.stop()

    # -- connect-under-reset probe ------------------------------------
    def connect_under_reset(self, fixture: str) -> dict[str, Any]:
        backend, _runtime = self._open(fixture, connect_under_reset=True)
        try:
            backend.start()
            backend.halt()
            idcode = backend.read_u32(0xE0042000)
            return {
                "dev_id": idcode & 0xFFF,
                "rev_id": (idcode >> 16) & 0xFFFF,
                "rdp": backend.read_option_bytes().rdp,
            }
        except OpenOcdError as exc:
            raise RecoveryError(f"connect-under-reset failed: {exc}") from exc
        finally:
            with contextlib.suppress(Exception):
                backend.stop()


class _Runtime:
    """Minimal runtime shape for ``Station._open_session``."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.session = None
        self.bank = None


__all__ = ["RecoveryError", "RecoveryService"]
