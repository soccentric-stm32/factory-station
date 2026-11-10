"""EOL step implementation: shared hardware and session helpers (P1-02).

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

import contextlib
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any

from hostcore import layout
from hostcore.hwlink import HwLinkError, HwLinkSession
from hostcore.images import FirmwareImage, parse_metadata_record
from hostcore.openocd import (
    OpenOcdBackend,
    OpenOcdError,
)
from hwval_factory.inputs import InputError
from hwval_factory.models import (
    RunContext,
)
from hwval_factory.power import PowerError
from hwval_factory.profile import ProductProfile
from hwval_factory.steps.base import AbortedError, RunRuntime, StepFailure


class HelperSteps:
    def _save_extras(self, ctx: RunContext) -> None:
        try:
            self.db.save_run_extras(
                ctx.run_id,
                option_bytes=ctx.option_bytes,
                calibration=ctx.calibration,
                image_versions=ctx.image_versions,
                metadata=ctx.metadata,
                provisioning_blob=ctx.provisioning_blob,
                record_revision=ctx.record_revision,
                label_pending=ctx.label_pending,
            )
        except Exception as exc:
            self._log(ctx.fixture, f"could not persist run extras: {exc}")


    def _backend(self, rt: RunRuntime) -> OpenOcdBackend:
        if rt.backend is None:
            raise StepFailure("OpenOCD backend is not available")
        return rt.backend

    def _power_cycle(self, fixture: str, rt: RunRuntime) -> None:
        """Power-cycle after an option-byte write (P2-03).

        Without a power port this is a no-op; the simulator's bank models the
        same transition.
        """
        if rt.power is None:
            return
        try:
            rt.power.off()
            time.sleep(0.4)
            rt.power.on()
            time.sleep(0.4)
            self._log(fixture, "power cycled")
        except PowerError as exc:
            self._log(fixture, f"power cycle failed: {exc}")


    def _close_session(self, rt: RunRuntime) -> None:
        if rt.session is not None:
            with contextlib.suppress(Exception):
                rt.session.close()
            rt.session = None

    def _wait_hello(
        self,
        session: HwLinkSession,
        *,
        timeout: float,
        abort_event: threading.Event | None = None,
        expected_role: str | int | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            if abort_event is not None and abort_event.is_set():
                raise AbortedError("aborted by operator")
            try:
                return session.connect(
                    expected_role,
                    timeout=min(3.0, max(0.5, deadline - time.monotonic())),
                )
            except HwLinkError as exc:
                last = exc
                if "expected" in str(exc) and expected_role is not None:
                    # Wrong firmware is a real failure, not a transient one.
                    raise StepFailure(str(exc)) from exc
                time.sleep(0.2)
        raise StepFailure(f"firmware did not answer HWLINK hello: {last}")

    def _hello_timeout(self, profile: ProductProfile) -> float:
        raw = profile.manifest.get("hello_timeout_s")
        try:
            return float(raw) if raw else 10.0
        except (TypeError, ValueError):
            return 10.0

    def _boot_timeout(self, profile: ProductProfile) -> float:
        raw = (profile.manifest.get("boot_check") or {}).get("timeout_s")
        try:
            return float(raw) if raw else 15.0
        except (TypeError, ValueError, AttributeError):
            return 15.0

    def _prompt(self, fixture: str, payload: dict[str, Any]) -> str:
        self._log(fixture, f"operator prompt: {payload.get('text', '')}")
        if self.prompt_handler is None:
            answers = payload.get("answers_list") or payload.get("choices") or ["OK"]
            answer = str(answers[0])
        else:
            answer = str(self.prompt_handler(fixture, payload))
        ctx = self.worker_ctx(fixture)
        if ctx is not None:
            ctx.metadata.setdefault("operator_prompts", []).append(
                {
                    "test_id": str(payload.get("test_id", "")),
                    "prompt_id": str(payload.get("prompt_id", "")),
                    "text": str(payload.get("text", "")),
                    "answer": answer,
                }
            )
        return answer

    def _verify_segments(self, backend: OpenOcdBackend, image: FirmwareImage) -> None:

        for segment in image.segments:
            actual = backend.read_memory(segment.address, len(segment.data))
            if actual != segment.data:
                raise StepFailure(f"flash verify failed at {segment.address:#010x}")
        combined = b"".join(
            backend.read_memory(segment.address, len(segment.data)) for segment in image.segments
        )
        if zlib.crc32(combined) & 0xFFFFFFFF != image.crc32:
            raise StepFailure("flash CRC32 does not match the image")

    def _program_payload(self, backend: OpenOcdBackend, image: FirmwareImage, address: int) -> None:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
            handle.write(image.data)
            name = Path(handle.name)
        try:
            backend.program(name, address=address, verify=True)
        finally:
            with contextlib.suppress(OSError):
                name.unlink()

    def _app_load(self, profile: ProductProfile) -> str:
        value = str(profile.manifest.get("app_load") or "swd").strip().lower()
        if value not in ("swd", "boot"):
            raise StepFailure(f"unsupported app_load {value!r}; use 'swd' or 'boot'")
        return value

    def _slot_a_address(self, profile: ProductProfile) -> int:
        raw = profile.manifest.get("layout") or {}
        if isinstance(raw, dict) and raw.get("slot_a"):
            value = int(str(raw["slot_a"]), 0)
            if value != layout.SLOT_A_BASE:
                raise StepFailure(
                    f"profile slot A {value:#010x} != layout {layout.SLOT_A_BASE:#010x}"
                )
        app = profile.images.get("app")
        if app is not None and app.address not in (None, layout.SLOT_A_BASE):
            raise StepFailure(
                f"profile app address {app.address:#010x} != layout {layout.SLOT_A_BASE:#010x}"
            )
        return layout.SLOT_A_BASE

    def _secure_floor(self, backend: OpenOcdBackend) -> int:
        """Highest security counter among CONFIRMED metadata sectors (R5/P1-10).

        Read-only and best-effort: unreadable or legacy sectors contribute 0.
        """
        floor = 0
        for base in (layout.META_A_BASE, layout.META_B_BASE):
            try:
                header = backend.read_memory(base, layout.META_HEADER_SIZE)
                if len(header) < layout.META_HEADER_SIZE:
                    continue
                magic = int.from_bytes(header[:4], "little")
                version = int.from_bytes(header[4:6], "little")
                if magic != layout.IMAGE_MAGIC or version != layout.IMAGE_HEADER_VERSION:
                    continue
                rec_magic, _value, present = parse_metadata_record(
                    backend.read_memory(
                        base + layout.META_CONFIRMED_OFFSET, layout.META_RECORD_SIZE
                    )
                )
                if not present or rec_magic != layout.META_CONFIRMED_MAGIC:
                    continue
                counter = int.from_bytes(
                    header[
                        layout.IMAGE_SECURITY_COUNTER_OFFSET : layout.IMAGE_SECURITY_COUNTER_OFFSET
                        + 4
                    ],
                    "little",
                )
                floor = max(floor, counter)
            except OpenOcdError:
                continue
        return floor

    def _metadata_address(self, profile: ProductProfile) -> int:
        raw = profile.manifest.get("boot_metadata") or profile.manifest.get("metadata") or {}
        if isinstance(raw, dict) and raw.get("address"):
            value = int(str(raw["address"]), 0)
            if value != layout.META_A_BASE:
                raise StepFailure(
                    f"profile metadata {value:#010x} != layout {layout.META_A_BASE:#010x}"
                )
        return layout.META_A_BASE

    def _check_lid(self, fixture: str) -> None:
        lid = self._inputs.get(fixture, {}).get("lid")
        if lid is None:
            return
        try:
            closed = lid.closed()
        except InputError as exc:
            raise StepFailure(f"fixture lid input failed: {exc}") from exc
        if not closed:
            raise StepFailure("fixture lid/clamp is open; close it before continuing")

    def _check_board_present(self, fixture: str) -> None:
        present = self._inputs.get(fixture, {}).get("board_present")
        if present is None:
            return
        try:
            ok = present.present()
        except InputError as exc:
            raise StepFailure(f"board-present input failed: {exc}") from exc
        if not ok:
            raise StepFailure("no board detected in the fixture")
