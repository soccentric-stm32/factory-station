"""Flow B: run the EOL test under BOOT (P3-04).

The product is configured once, a factory HEX programs the signed factory
image into slot A (CONFIRMED) and HWVAL into slot B (ACTIVATED, counter 0,
not CONFIRMED).  BOOT boots slot B on trial; the station links to HWVAL,
runs the test, then erases slot B so BOOT falls back to the factory image in
slot A.  Flow B is simulator-only until the validation-firmware readiness
gate (P3-01) passes on hardware.
"""

from __future__ import annotations

from hostcore import layout
from hostcore.boot import BootError, wait_for_application
from hostcore.openocd import OpenOcdError
from hwval_factory.models import RunContext
from hwval_factory.profile import ProtectionSpec
from hwval_factory.protection import ProtectionError, apply_protection
from hwval_factory.steps.base import RunRuntime, StepFailure, StepOutcome
from hwval_factory.steps.util import uid_word_hex


class FlowBSteps:
    def _step_product_config(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        protection = profile.protection
        spec = ProtectionSpec(
            ndbank=protection.ndbank,
            ndboot=protection.ndboot,
            bor_lev=protection.bor_lev,
            wrp_bank1=[],
            wrp_bank2=[],
            rdp=0,
            boot_addr0=protection.boot_addr0,
            boot_addr1=protection.boot_addr1,
        )
        try:
            apply_protection(backend, spec)
        except (ProtectionError, OpenOcdError) as exc:
            ctx.metadata["safe_state"] = "UNKNOWN_CONFIG"
            raise StepFailure(
                f"product configuration failed: {exc}",
                cause="power loss or a failed option-byte write",
                action="use the recovery tool before re-powering the board",
            ) from exc
        self._power_cycle(ctx.fixture, rt)
        backend.mass_erase()
        ctx.metadata["safe_state"] = "product_blank"
        return StepOutcome(
            detail=f"nDBANK={protection.ndbank}, nDBOOT={protection.ndboot}, erased"
        )

    def _step_program_flowb(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        factory = profile.factory_image
        if factory is None:
            raise StepFailure(
                "Flow B needs images.factory (BOOT + slot A + trial slot B)",
                action="build the profile's factory HEX with build-factory",
            )
        factory_path = profile.image_path("factory")
        app = profile.app_image
        test_slot = profile.images.get("test_slot")
        if app.image is None:
            raise StepFailure("application image was not parsed")
        if test_slot is None or test_slot.image is None:
            raise StepFailure("Flow B needs images.test_slot (signed HWVAL)")
        if test_slot.image.expected_floor != 0:
            raise StepFailure(
                "the test-slot image must use security counter 0 so it can never "
                "beat the confirmed factory image"
            )
        backend.program(factory_path, verify=True)
        backend.verify_image(factory_path)
        rt.app_image = app.image
        ctx.metadata["program_mode"] = "factory-flowb"
        ctx.metadata["factory_image"] = {
            "file": factory.file,
            "sha256": factory.sha256,
            "key_id": app.image.key_id,
        }
        ctx.metadata["security_counter"] = app.image.security_counter
        ctx.metadata["expected_floor"] = app.image.expected_floor
        ctx.metadata["test_slot_counter"] = test_slot.image.security_counter
        ctx.metadata["boot_metadata"] = {
            "address": self._metadata_address(profile),
            "verified_crc32": app.image.crc32,
            "sequence": 1,
            "writer": "factory-hex-flowb",
        }
        boot_image = profile.boot_image.image
        ctx.image_versions["boot"] = boot_image.version_string() if boot_image else ""
        ctx.image_versions["app"] = app.image.version_string()
        ctx.image_versions["test_slot"] = test_slot.image.version_string()
        ctx.metadata["safe_state"] = "flowb_programmed"
        if rt.bank is not None:
            rt.bank.prepare_test_image(
                test_slot.sha256,
                test_slot.image.name or "hwval",
                test_slot.image.version,
                profile.image_path("test_slot"),
            )
        return StepOutcome(
            detail=(
                f"factory HEX {factory.file} programmed; slot A v"
                f"{app.image.version_string()} confirmed, slot B HWVAL v"
                f"{test_slot.image.version_string()} on trial"
            )
        )

    def _step_link_up(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
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
            role = str(hello.get("role", "")).upper()
            if role != "HWVAL":
                raise StepFailure(
                    f"BOOT booted {role or 'nothing'}, expected the HWVAL trial image",
                    action="check that slot B is activated and not confirmed",
                )
            info = session.device_info()
            reported = str(info.get("uid", "")).upper().removeprefix("0X")
            expected = uid_word_hex(ctx.uid)
            if reported and reported != expected:
                raise StepFailure(
                    f"HWVAL reports UID {reported} != SWD UID {expected}",
                    cause="the trial image is running on a different board",
                )
        except BootError as exc:
            raise StepFailure(str(exc)) from exc
        finally:
            self._close_session(rt)
        ctx.metadata["flowb_link_log"] = list(session.link_log)
        ctx.metadata["safe_state"] = "link_up"
        return StepOutcome(detail=f"HWVAL trial linked (UID {expected})")

    def _step_end_test(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        if profile.slot_b_after_test != "erase":
            ctx.metadata["safe_state"] = "tested"
            return StepOutcome(detail="slot B left superseded by profile policy")
        self._close_session(rt)
        backend.reset(halt=False)
        backend.erase_address(layout.META_B_BASE, layout.META_SIZE)
        backend.erase_address(layout.SLOT_B_BASE, layout.SLOT_SIZE)
        backend.reset(halt=False)
        if rt.bank is not None:
            rt.bank.prepare_app_image(
                profile.app_image.sha256,
                profile.app_image.image.name if profile.app_image.image else "app",
                profile.app_image.version,
                profile.image_path("app"),
            )
        ctx.metadata["safe_state"] = "slot_b_erased"
        return StepOutcome(detail="slot B and metadata B erased; factory image in slot A remains")
