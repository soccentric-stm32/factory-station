"""EOL step implementation: production programming (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

import zlib

from hostcore import layout
from hostcore.boot import BootError, wait_for_boot
from hostcore.hwlink import HwLinkError
from hostcore.images import load_key
from hostcore.openocd import OpenOcdError
from hwval_factory.models import (
    RunContext,
    RunKind,
)
from hwval_factory.profile import ProductProfile, ProtectionSpec
from hwval_factory.protection import (
    ProtectionError,
    apply_protection,
    lower_rdp_for_rework,
)
from hwval_factory.steps.base import AbortedError, RunRuntime, StepFailure, StepOutcome


def app_path_stem(profile: ProductProfile) -> str:
    return profile.image_path("app").stem


class ProgramSteps:
    def _step_program(self, ctx: RunContext, rt: RunRuntime) -> StepOutcome:
        profile = self.require_profile()
        backend = self._backend(rt)
        app = profile.app_image
        if app.image is None:
            raise StepFailure("application image was not parsed")
        if profile.production and not app.image.is_signed:
            raise StepFailure("production profiles require a signed (version 2) application image")
        floor_before = self._secure_floor(backend)
        ctx.metadata["floor_before"] = floor_before
        if ctx.kind == RunKind.REWORK and floor_before and app.image.expected_floor < floor_before:
            raise StepFailure(
                f"profile security counter {app.image.expected_floor} is below this "
                f"unit's anti-rollback floor {floor_before}; use a newer profile"
            )
        public_key_path = profile.signing_public_key_path
        if (
            app.image.is_signed
            and public_key_path is not None
            and not app.image.verify_signature(public_key_path)
        ):
            raise StepFailure("application signature does not verify with the profile key")
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
        try:
            apply_protection(backend, production)
        except (ProtectionError, OpenOcdError) as exc:
            ctx.metadata["safe_state"] = "UNKNOWN_CONFIG"
            raise StepFailure(
                f"product option-byte write failed: {exc}",
                cause="power loss or a failed option-byte write",
                action="use the recovery tool to inspect the option bytes before retrying",
            ) from exc
        self._power_cycle(ctx.fixture, rt)
        backend.mass_erase()
        ctx.metadata["safe_state"] = "erased"
        factory = profile.factory_image
        step_config = profile.manifest.get("steps") or {}
        program_mode = str(
            step_config.get("program_mode", "factory" if factory else "records")
        )
        if (
            factory is not None
            and program_mode == "factory"
            and self._app_load(profile) != "boot"
        ):
            factory_path = profile.image_path("factory")
            backend.program(factory_path, verify=True)
            backend.verify_image(factory_path)
            boot_image = profile.boot_image.image
            app = profile.app_image
            if app.image is None:
                raise StepFailure("application image was not parsed")
            rt.app_image = app.image
            if rt.bank is not None:
                rt.bank.prepare_app_image(
                    app.sha256,
                    app.image.name or app_path_stem(profile),
                    app.image.version,
                    profile.image_path("app"),
                )
            ctx.metadata["program_mode"] = "factory"
            ctx.metadata["factory_image"] = {
                "file": factory.file,
                "sha256": factory.sha256,
                "key_id": app.image.key_id,
            }
            ctx.metadata["security_counter"] = app.image.security_counter
            ctx.metadata["expected_floor"] = app.image.expected_floor
            ctx.metadata["key_id"] = app.image.key_id
            ctx.metadata["boot_metadata"] = {
                "address": self._metadata_address(profile),
                "verified_crc32": app.image.crc32,
                "sequence": 1,
                "writer": "factory-hex",
            }
            ctx.image_versions["boot"] = (
                boot_image.version_string() if boot_image else ""
            )
            ctx.image_versions["app"] = app.image.version_string()
            ctx.metadata["safe_state"] = "app_programmed"
            backend.reset(halt=True)
            return StepOutcome(
                detail=(
                    f"factory HEX {factory.file} programmed and verified; "
                    f"app v{app.image.version_string()}, "
                    f"floor={app.image.expected_floor}"
                )
            )
        boot_path = profile.image_path("boot")
        backend.program(boot_path, verify=True)
        boot_image = profile.boot_image.image
        if boot_image is not None:
            self._verify_segments(backend, boot_image)
        app_path = profile.image_path("app")
        app_load = self._app_load(profile)
        metadata_base = self._metadata_address(profile)
        if app_load == "boot":
            # B-13: BOOT is already flashed and the board was mass-erased, so
            # it stays in update mode.  Upload the signed IBWH image and let
            # BOOT erase the slot, verify the signature and write metadata.
            if rt.bank is not None:
                rt.bank.device.set_role("BOOT", "BOOT", 0)
                rt.bank.device.signed_images = profile.production
                key_path = profile.signing_public_key_path
                if key_path is not None:
                    rt.bank.device.signing_public_key = load_key(key_path)
            self._close_session(rt)
            session = self._open_session(ctx.fixture, rt)
            try:
                hello, boot_info = wait_for_boot(
                    session,
                    swd=backend,
                    timeout=self._boot_timeout(profile),
                    on_log=lambda text: self._log(ctx.fixture, text),
                    abort_event=rt.abort,
                )
            except BootError as exc:
                if rt.abort is not None and rt.abort.is_set():
                    raise AbortedError("aborted by operator") from exc
                raise StepFailure(str(exc)) from exc
            if str(hello.get("role", "")).upper() not in ("BOOTLOADER", "BOOT"):
                raise StepFailure("BOOT did not enter update mode for the VCP load")
            try:
                result = session.update_image(
                    app.image,
                    force=False,
                    abort_event=rt.abort,
                    progress=lambda done, total: self._update_status(
                        ctx.fixture, message=f"BOOT update {done}/{total}"
                    ),
                )
            except HwLinkError as exc:
                raise StepFailure(f"BOOT refused the update: {exc}") from exc
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
            ctx.metadata["security_counter"] = app.image.security_counter
            ctx.metadata["expected_floor"] = app.image.expected_floor
            ctx.metadata["key_id"] = app.image.key_id
            ctx.metadata["boot_signed_images"] = bool(boot_info.get("signed_images"))
            ctx.image_versions["boot"] = boot_image.version_string() if boot_image else ""
            ctx.image_versions["app"] = app.image.version_string()
            ctx.metadata["safe_state"] = "app_programmed"
            return StepOutcome(
                detail=(
                    f"BOOT + app v{app.image.version_string()} loaded over VCP, "
                    f"CRC32=0x{app.image.crc32:08X}, floor={app.image.expected_floor}"
                )
            )
        address = self._slot_a_address(profile)
        self._program_payload(backend, app.image, address)
        readback = backend.read_memory(address, len(app.image.data))
        if readback != app.image.data:
            raise StepFailure("application read-back mismatch")

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
        ctx.metadata["security_counter"] = app.image.security_counter
        ctx.metadata["expected_floor"] = app.image.expected_floor
        ctx.metadata["key_id"] = app.image.key_id
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
