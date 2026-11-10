"""Concrete hardware adapters, kept outside the step modules (P1-01)."""

from __future__ import annotations

import contextlib
from typing import Any

from hostcore.hwlink import HwLinkSession, SerialTransport, SimulatedTransport
from hostcore.openocd import OpenOcdBackend, OpenOcdProcess
from hwval_factory.steps.base import RunRuntime


class HardwareAdapters:
    def _open_backend(
        self, fixture: str, *, connect_under_reset: bool | None = None
    ) -> OpenOcdBackend:
        if self.simulation and self.simulator is not None:
            bank = self.simulator.bank(fixture)
            bank.openocd.log_callback = lambda text, name=fixture: self._log(name, text)
            return bank.openocd
        config = self.config.fixture(fixture)

        def _log_callback(text: str) -> None:
            self._log(fixture, text)

        return OpenOcdProcess(
            config.probe_serial,
            openocd=self.config.openocd.binary,
            interface_cfg=self.config.openocd.interface_cfg,
            target_cfg=self.config.openocd.target_cfg,
            telnet_port=int(getattr(config, "telnet_port", 4444)),
            connect_under_reset=(
                self.config.openocd.connect_under_reset
                if connect_under_reset is None
                else connect_under_reset
            ),
            log_callback=_log_callback,
        )

    def _restart_backend(self, worker: Any) -> None:
        """Restart one fixture's OpenOCD after a crash (P2-02)."""
        runtime = worker.runtime
        if runtime.backend is not None:
            with contextlib.suppress(Exception):
                runtime.backend.stop()
        runtime.backend = self._open_backend(worker.fixture)
        runtime.backend.start()

    def _open_session(self, fixture: str, rt: RunRuntime) -> HwLinkSession:
        transport: Any
        if self.simulation and self.simulator is not None and rt.bank is not None:
            transport = SimulatedTransport(rt.bank.device)
        else:
            config = self.config.fixture(fixture)
            if config.serial_port:
                transport = SerialTransport(config.serial_port)
            else:
                transport = SerialTransport.find_by_probe_serial(config.probe_serial)
        session = HwLinkSession(
            transport,
            prompt_handler=lambda payload: self._prompt(fixture, payload),
            log_handler=lambda text: self._log(fixture, text),
            timeout=5.0,
        )
        rt.session = session
        return session
