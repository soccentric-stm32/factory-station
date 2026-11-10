"""EOL step implementation: bench configuration and test firmware (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

from hostcore.openocd import OpenOcdError
from hwval_factory.models import (
    RunContext,
    RunKind,
)
from hwval_factory.profile import ProtectionSpec
from hwval_factory.protection import (
    ProtectionError,
    apply_protection,
    lower_rdp_for_rework,
)
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome


class PrepareSteps:
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
        try:
            apply_protection(backend, bench)
        except (ProtectionError, OpenOcdError) as exc:
            ctx.metadata["safe_state"] = "UNKNOWN_CONFIG"
            raise StepFailure(
                f"bench option-byte write failed: {exc}",
                cause="power loss or a failed option-byte write",
                action="use the recovery tool to inspect the option bytes before retrying",
            ) from exc
        self._power_cycle(ctx.fixture, rt)
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
