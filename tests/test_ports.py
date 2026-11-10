"""The real and simulated adapters satisfy the station ports (P1-01)."""

from __future__ import annotations

from hostcore.hwlink import HwLinkSession, SimulatedTransport
from hostcore.simulator_device import SimulatedDevice
from hwval_factory.ports import ClockPort, LinkPort, PowerPort, SwdPort
from hwval_factory.simulator import SimulatedChip, SimulatedOpenOcd


class _Clock:
    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        _ = seconds


def test_simulated_swd_satisfies_the_swd_port() -> None:
    backend = SimulatedOpenOcd(SimulatedChip.create("F1"))
    assert isinstance(backend, SwdPort)


def test_hwlink_session_satisfies_the_link_port() -> None:
    session = HwLinkSession(SimulatedTransport(SimulatedDevice("hwval")))
    assert isinstance(session, LinkPort)
    session.close()


def test_clock_satisfies_the_clock_port() -> None:
    assert isinstance(_Clock(), ClockPort)


def test_null_power_satisfies_the_power_port() -> None:
    from hwval_factory.power import create_power_driver

    driver = create_power_driver({})
    assert isinstance(driver, PowerPort)
    driver.close()
