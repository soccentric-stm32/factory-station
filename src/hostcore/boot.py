"""Listen-window-safe BOOT hand-off (P0-06, ENT-001).

BOOT waits 500 ms after reset for a Hello; a Hello that arrives in that
window *keeps BOOT in update mode* instead of booting the application.  A
host that starts talking to the port immediately after a reset can therefore
park BOOT by accident and then report "BOOT did not start an application" on
a perfectly good board (R4).  ``bootctl wait`` was fixed for this on
2026-11-06; this module is the station's copy of the rule:

1. reset the board;
2. stay off the port for ``BOOT_SETTLE_S`` (listen window + image checks);
3. only then start Hello;
4. if BOOT answers but reports a valid active slot, the Hello still caught
   the window: send ``Reset``, settle, and retry at most
   ``MAX_BOOT_RELEASES`` times;
5. if no slot is valid, fail with BOOT's own per-slot detail, which is what
   the operator needs to see.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from hostcore.layout import (
    BOOT_SETTLE_S,
    CATCH_INTERVAL_S,
    MAX_BOOT_RELEASES,
)

BOOT_ROLES = ("BOOT", "BOOTLOADER")


class BootError(RuntimeError):
    """BOOT did not reach the application, with an operator-readable cause."""


def _role(hello: dict[str, Any]) -> str:
    return str(hello.get("role", "")).upper()


def _reset(session: Any, swd: Any = None) -> None:
    if swd is not None:
        swd.reset(halt=False)
    else:
        session.reset()


def describe_boot_failure(info: dict[str, Any]) -> str:
    """Turn a BootInfo payload into the per-slot reason an operator can act on."""
    parts: list[str] = []
    for slot in info.get("slots") or []:
        name = slot.get("slot_name") or "?"
        detail = str(slot.get("detail") or "").strip()
        if slot.get("present"):
            state = "valid" if slot.get("valid") else "not valid"
            if slot.get("rejected"):
                state = "rejected"
            elif slot.get("pending"):
                state = "on trial"
            elif slot.get("confirmed"):
                state = "confirmed"
            parts.append(f"slot {name}: {state}" + (f" ({detail})" if detail else ""))
        else:
            parts.append(f"slot {name}: empty")
    suffix = "; ".join(parts) if parts else "BOOT reported no slots"
    return f"BOOT did not start an application: {suffix}"


def wait_for_application(
    session: Any,
    *,
    swd: Any = None,
    timeout: float = 15.0,
    settle_s: float = BOOT_SETTLE_S,
    max_releases: int = MAX_BOOT_RELEASES,
    on_log: Callable[[str], None] | None = None,
    abort_event: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Reset, wait out BOOT's listen window, and return the application Hello.

    ``session`` is an ``HwLinkSession``-like object with ``hello()``,
    ``reset()`` and ``boot_info()``.  ``swd`` is optional; when given it is
    used for the reset so the session never talks to BOOT's listen window.
    """
    log = on_log or (lambda _text: None)
    deadline = time.monotonic() + timeout
    releases = 0
    _reset(session, swd)
    log(f"boot check: reset; staying off the port for {settle_s:g} s")
    sleep(settle_s)

    while time.monotonic() < deadline:
        if abort_event is not None and abort_event.is_set():
            raise BootError("aborted by operator")
        try:
            hello = session.hello(timeout=min(2.0, max(0.3, deadline - time.monotonic())))
        except Exception as exc:  # LinkTimeout/LinkError and friends
            log(f"boot check: no Hello yet ({exc})")
            sleep(0.2)
            continue
        role = _role(hello)
        if role not in BOOT_ROLES:
            return cast(dict[str, Any], hello)
        try:
            info = session.boot_info()
        except Exception as exc:
            raise BootError(f"BOOT answered but BootInfo failed: {exc}") from exc
        if bool(info.get("active_valid")) and releases < max_releases:
            releases += 1
            log(
                "boot check: Hello caught BOOT's listen window; resetting so the "
                f"application boots (release {releases}/{max_releases})"
            )
            _reset(session, swd)
            sleep(settle_s)
            continue
        raise BootError(describe_boot_failure(info))
    raise BootError("application did not answer HWLINK after the boot window")


def wait_for_boot(
    session: Any,
    *,
    swd: Any = None,
    timeout: float = 15.0,
    settle_s: float = BOOT_SETTLE_S,
    on_log: Callable[[str], None] | None = None,
    abort_event: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reset, settle, and wait for BOOT in update mode (no valid slot).

    Used by the ``app_load: boot`` path: the part has just been mass-erased
    and BOOT written, so after the listen window BOOT reports no bootable
    slot and stays available for ``UpdateBegin``.
    """
    log = on_log or (lambda _text: None)
    _reset(session, swd)
    log(f"BOOT update: reset; settling for {settle_s:g} s")
    sleep(settle_s)
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        if abort_event is not None and abort_event.is_set():
            raise BootError("aborted by operator")
        try:
            hello = session.hello(timeout=min(2.0, max(0.3, deadline - time.monotonic())))
        except Exception as exc:
            last = exc
            sleep(0.2)
            continue
        if _role(hello) in BOOT_ROLES:
            return hello, session.boot_info()
        raise BootError(f"expected BOOT for the update, device reports {hello.get('role')}")
    raise BootError(f"BOOT did not answer for the update: {last}")


def catch_boot(
    session: Any,
    *,
    swd: Any = None,
    timeout: float = 5.0,
    interval_s: float = CATCH_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reset and keep sending Hello until BOOT answers (``bootctl catch``).

    A Hello inside BOOT's listen window keeps it in update mode, which is the
    only way to reach BOOT when the application does not answer HWLINK.
    """
    _reset(session, swd)
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            hello = session.hello(timeout=interval_s)
        except Exception as exc:
            last = exc
            sleep(interval_s)
            continue
        if _role(hello) in BOOT_ROLES:
            return hello, session.boot_info()
        # The application answered: it is healthy, but this is not BOOT.
        raise BootError(f"expected BOOT, device reports {hello.get('role')}")
    raise BootError(f"BOOT did not answer within {timeout:g} s: {last}")
