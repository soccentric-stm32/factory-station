"""Probe and self-test maintenance helpers.

Moved verbatim from the Station engine; the handler still uses the station
services object it is mixed into, so behaviour is unchanged while the
orchestration lives in ``pipeline.py``.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
from typing import Any

from hostcore.hwlink import HwLinkError, HwLinkSession, SerialTransport, SimulatedTransport
from hostcore.openocd import (
    OpenOcdError,
    option_bytes_to_dict,
)
from hwval_factory.steps.constants import (
    DBGMCU_IDCODE,
    FLASH_SIZE_REG,
    UID_BASE,
)


class MaintenanceHelpers:
    def detect_probes(self) -> list[str]:
        if self.simulation:
            return [f.probe_serial for f in self.config.fixtures if f.probe_serial]
        probes: set[str] = set()
        try:
            from serial.tools import list_ports

            for info in list_ports.comports():
                haystack = " ".join(
                    str(x) for x in (info.serial_number, info.hwid, info.description)
                )
                if (
                    "st-link" in haystack.lower() or "stlink" in haystack.lower()
                ) and info.serial_number:
                    probes.add(info.serial_number)
        except Exception:
            pass
        try:
            proc = subprocess.run(
                [
                    self.config.openocd.binary,
                    "-c",
                    "adapter list",
                    "-c",
                    "shutdown",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in (proc.stdout + proc.stderr).splitlines():
                match = re.search(r"serial(?: number)?[=: ]+([0-9A-Za-z:-]+)", line, re.I)
                if match:
                    probes.add(match.group(1))
        except (OSError, subprocess.TimeoutExpired):
            pass
        return sorted(probes)

    def missing_probes(self, detected: list[str] | None = None) -> list[str]:
        probes = set(detected if detected is not None else self.detect_probes())
        return [
            f.name for f in self.config.fixtures if f.probe_serial and f.probe_serial not in probes
        ]

    def probe_fixture(self, fixture: str) -> list[dict[str, Any]]:
        """B-12/G-02: step-by-step SWD + VCP bring-up for one fixture."""
        config = self.config.fixture(fixture)
        steps: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            steps.append({"name": name, "ok": ok, "detail": detail})

        add("probe configured", bool(config.probe_serial), config.probe_serial or "missing")
        backend = self._open_backend(fixture)
        try:
            backend.start()
            add("OpenOCD start", True, f"telnet {config.telnet_port}")
            backend.halt()
            idcode = backend.read_u32(DBGMCU_IDCODE)
            dev_id = idcode & 0xFFF
            rev_id = (idcode >> 16) & 0xFFFF
            add("IDCODE", True, f"DEV_ID=0x{dev_id:03X} REV_ID=0x{rev_id:04X}")
            flash_kb = backend.read_u16(FLASH_SIZE_REG)
            add("flash size", True, f"{flash_kb} KB")
            uid = backend.read_memory(UID_BASE, 12)
            add("UID", len(uid) == 12, uid.hex().upper())
            options = backend.read_option_bytes()
            add("option bytes", True, str(option_bytes_to_dict(options)))
        except (OpenOcdError, OSError) as exc:
            add("SWD", False, str(exc))
        finally:
            with contextlib.suppress(Exception):
                backend.stop()
        transport: Any
        try:
            if self.simulation and self.simulator is not None:
                transport = SimulatedTransport(self.simulator.bank(fixture).device)
            elif config.serial_port:
                transport = SerialTransport(config.serial_port)
            else:
                transport = SerialTransport.find_by_probe_serial(config.probe_serial)
        except HwLinkError as exc:
            add("VCP open", False, str(exc))
            return steps
        session = HwLinkSession(transport, timeout=5.0)
        try:
            hello = session.connect(timeout=10.0)
            add(
                "HWLINK Hello",
                True,
                f"role={hello.get('role')} fw={hello.get('firmware_name')} "
                f"version={hello.get('version')}",
            )
            role = str(hello.get("role", "")).upper()
            if role == "HWVAL":
                info = session.device_info()
                add(
                    "GetDeviceInfo",
                    True,
                    f"uid={info.get('uid')} optcr=0x{int(info.get('optcr', 0)):08X}",
                )
            elif role in ("DEMO", "APP"):
                status = session.status()
                add(
                    "GetStatus",
                    True,
                    f"slot={status.get('slot_name')} trial={status.get('trial_name')}",
                )
            else:
                boot = session.boot_info()
                add("GetBootInfo", True, f"active={boot.get('active_slot_name')}")
        except HwLinkError as exc:
            add("HWLINK Hello", False, str(exc))
        finally:
            session.close()
        return steps

    def run_self_test(self, fixture: str, operator: str = "supervisor") -> bool:
        from hwval_factory.models import RunContext, RunKind
        from hwval_factory.steps.base import RunRuntime

        backend = self._open_backend(fixture)
        runtime = RunRuntime(backend=backend)
        ctx = RunContext(
            run_id=0,
            fixture=fixture,
            operator=operator,
            kind=RunKind.FIRST,
            profile_id="self-test",
            profile_version="1",
        )
        detail = ""
        passed = False
        try:
            backend.start()
            outcome = self._step_self_test(ctx, runtime)
            passed = True
            detail = outcome.detail
        except Exception as exc:
            detail = str(exc)
            passed = False
        finally:
            with contextlib.suppress(Exception):
                backend.stop()
        self.db.add_self_test(fixture, "PASS" if passed else "FAIL", operator, detail)
        self.db.audit(operator, "station.self_test", f"{fixture} {'PASS' if passed else 'FAIL'}")
        self._emit(
            "self_test",
            fixture=fixture,
            passed=passed,
            detail=detail,
        )
        self._log(fixture, f"self-test {'PASS' if passed else 'FAIL'}: {detail}")
        return passed

    def self_test_warnings(self) -> list[str]:
        warnings: list[str] = []
        interval = self.config.self_test_interval_hours
        for fixture in self.fixture_names():
            age = self.db.self_test_age_hours(fixture)
            if age is None:
                warnings.append(f"{fixture}: no passing self-test recorded")
            elif age > interval:
                warnings.append(
                    f"{fixture}: no passing self-test for {age:.1f} h (interval {interval} h)"
                )
        return warnings

    def maintenance_warnings(self) -> list[str]:
        warnings: list[str] = []
        for fixture in self.fixture_names():
            stats = self.db.fixture_stats(fixture)
            if stats is None:
                continue
            insertions = int(stats["insertions"])
            if insertions >= self.config.maintenance_count:
                warnings.append(
                    f"{fixture}: {insertions} insertions, maintenance due at "
                    f"{self.config.maintenance_count}"
                )
        return warnings
