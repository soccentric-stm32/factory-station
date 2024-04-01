"""Listen-window-safe boot hand-off (P0-06, R4)."""

from __future__ import annotations

import pytest

from hostcore import layout
from hostcore.boot import BootError, catch_boot, wait_for_application, wait_for_boot


class _Swd:
    def __init__(self) -> None:
        self.resets = 0
        self.halted: list[bool] = []

    def reset(self, halt: bool = False) -> None:
        self.resets += 1
        self.halted.append(halt)


class _Session:
    """Fake HwLinkSession that can answer BOOT first, then the application."""

    def __init__(self, roles, *, active_valid=True, detail="confirmed", fail_hellos=0):
        self.roles = list(roles)
        self.active_valid = active_valid
        self.detail = detail
        self.fail_hellos = fail_hellos
        self.hellos = 0
        self.resets = 0

    def hello(self, timeout=None):
        self.hellos += 1
        if self.fail_hellos:
            self.fail_hellos -= 1
            raise TimeoutError("nothing yet")
        role = self.roles.pop(0) if len(self.roles) > 1 else self.roles[0]
        return {
            "role": role,
            "role_name": role,
            "firmware_name": "BOOT" if role == "BOOTLOADER" else "APP",
            "version": "0.1.0",
        }

    def reset(self) -> None:
        self.resets += 1

    def boot_info(self):
        return {
            "active_valid": self.active_valid,
            "active_slot": 1,
            "slots": [
                {
                    "slot": 1,
                    "slot_name": "A",
                    "present": True,
                    "valid": self.active_valid,
                    "confirmed": self.active_valid,
                    "detail": self.detail,
                },
                {"slot": 2, "slot_name": "B", "present": False, "detail": "empty"},
            ],
        }


def test_application_answers_after_one_settle() -> None:
    session = _Session(["APP"])
    swd = _Swd()
    sleeps: list[float] = []
    hello = wait_for_application(
        session,
        swd=swd,
        timeout=5,
        sleep=sleeps.append,
    )
    assert hello["role"] == "APP"
    assert swd.resets == 1
    assert sleeps == [layout.BOOT_SETTLE_S]
    assert session.hellos == 1


def test_listen_window_boot_is_released_and_retried() -> None:
    session = _Session(["BOOTLOADER", "APP"])
    swd = _Swd()
    sleeps: list[float] = []
    hello = wait_for_application(session, swd=swd, timeout=5, sleep=sleeps.append)
    assert hello["role"] == "APP"
    # One reset at the start, one to release BOOT's listen window.
    assert swd.resets == 2
    assert sleeps == [layout.BOOT_SETTLE_S, layout.BOOT_SETTLE_S]


def test_boot_with_no_valid_slot_fails_with_slot_detail() -> None:
    session = _Session(["BOOTLOADER"], active_valid=False, detail="image is not signed")
    with pytest.raises(BootError) as error:
        wait_for_application(session, swd=_Swd(), timeout=5, sleep=lambda _s: None)
    message = str(error.value)
    assert "slot A" in message
    assert "image is not signed" in message
    assert "slot B: empty" in message


def test_boot_release_count_is_bounded() -> None:
    session = _Session(["BOOTLOADER"], active_valid=True)
    with pytest.raises(BootError, match="did not start an application"):
        wait_for_application(
            session,
            swd=_Swd(),
            timeout=5,
            max_releases=2,
            sleep=lambda _s: None,
        )
    assert session.hellos == 3  # initial + two releases


def test_wait_for_boot_returns_boot_info() -> None:
    session = _Session(["BOOTLOADER"], active_valid=False)
    hello, info = wait_for_boot(session, swd=_Swd(), timeout=5, sleep=lambda _s: None)
    assert hello["role"] == "BOOTLOADER"
    assert info["active_valid"] is False


def test_catch_boot_keeps_asking_until_boot_answers() -> None:
    session = _Session(["BOOTLOADER"], fail_hellos=3)
    hello, info = catch_boot(session, timeout=5, interval_s=0, sleep=lambda _s: None)
    assert hello["role"] == "BOOTLOADER"
    assert info["active_valid"] is True
    assert session.hellos == 4


def test_catch_boot_refuses_a_healthy_application() -> None:
    session = _Session(["APP"])
    with pytest.raises(BootError, match="expected BOOT"):
        catch_boot(session, timeout=5, interval_s=0, sleep=lambda _s: None)
