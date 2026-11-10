"""EOL step implementation: protection (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from hostcore.boot import BootError, wait_for_application
from hostcore.openocd import OpenOcdError, option_bytes_to_dict
from hwval_factory.models import (
    RunContext,
)
from hwval_factory.profile import ProtectionSpec
from hwval_factory.protection import (
    ProtectionError,
    apply_protection,
)
from hwval_factory.steps.base import AbortedError, RunRuntime, StepFailure, StepOutcome


class ProtectSteps:
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
            try:
                apply_protection(backend, before)
            except (ProtectionError, OpenOcdError) as exc:
                ctx.metadata["safe_state"] = "UNKNOWN_CONFIG"
                raise StepFailure(
                    f"write-protection write failed: {exc}",
                    cause="power loss or a failed option-byte write",
                    action="use the recovery tool before re-powering the board",
                ) from exc
        try:
            values = apply_protection(backend, spec)
        except (ProtectionError, OpenOcdError) as exc:
            ctx.metadata["safe_state"] = "UNKNOWN_CONFIG"
            raise StepFailure(
                f"protection write failed: {exc}",
                cause="power loss or a failed option-byte write",
                action="use the recovery tool before re-powering the board",
            ) from exc
        self._power_cycle(ctx.fixture, rt)
        # Power-cycle equivalent: read the options once more so a write that
        # only landed in a register is caught.  RDP 1 blocks a normal reset
        # halt/re-examine; read while the target is still running instead.
        if spec.rdp == 0:
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
            self._close_session(rt)
            session = self._open_session(ctx.fixture, rt)
            try:
                hello = wait_for_application(
                    session,
                    swd=backend,
                    timeout=self._boot_timeout(profile),
                    on_log=lambda text: self._log(ctx.fixture, text),
                    abort_event=rt.abort,
                )
            except BootError as exc:
                if rt.abort is not None and rt.abort.is_set():
                    raise AbortedError("aborted by operator") from exc
                raise StepFailure(f"after protection: {exc}") from exc
            finally:
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
