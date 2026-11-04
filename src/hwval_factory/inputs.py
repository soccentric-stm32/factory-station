"""Fixture safety inputs: lid/clamp closed and board present (F-04).

Drivers:

* ``none`` — always safe (simulator or a fixture without sensors).
* ``gpio`` — read a Linux sysfs GPIO value file (``/sys/class/gpio/gpioN/value``).

A production station can add a vendor driver behind the same ``InputDriver``
protocol.  Opening the lid or removing the board stops the run before the next
step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class InputError(RuntimeError):
    """A fixture input could not be read."""


class InputDriver(Protocol):
    def closed(self) -> bool: ...

    def present(self) -> bool: ...

    def close(self) -> None: ...


class AlwaysInput:
    def closed(self) -> bool:
        return True

    def present(self) -> bool:
        return True

    def close(self) -> None:
        return


class GpioInput:
    def __init__(self, value_path: str | Path, *, active_high: bool = True) -> None:
        self.value_path = Path(value_path)
        self.active_high = active_high

    def _value(self) -> bool:
        try:
            text = self.value_path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise InputError(f"cannot read {self.value_path}: {exc}") from exc
        if text not in ("0", "1"):
            raise InputError(f"{self.value_path} contains {text!r}, expected 0 or 1")
        return (text == "1") == self.active_high

    def closed(self) -> bool:
        return self._value()

    def present(self) -> bool:
        return self._value()

    def close(self) -> None:
        return


def _driver(raw: dict[str, Any]) -> InputDriver:
    kind = str(raw.get("driver") or "none").strip().lower()
    if kind in ("none", ""):
        return AlwaysInput()
    if kind == "gpio":
        path = str(raw.get("value_path") or raw.get("path") or "")
        if not path:
            raise InputError("gpio input needs value_path")
        return GpioInput(path, active_high=bool(raw.get("active_high", True)))
    raise InputError(f"unknown input driver {kind!r}")


def create_inputs(config: dict[str, Any]) -> dict[str, InputDriver]:
    """Build the configured inputs; unknown drivers fail loudly at start-up."""
    inputs: dict[str, InputDriver] = {}
    for name, raw in config.items():
        if isinstance(raw, dict):
            inputs[str(name)] = _driver(raw)
    return inputs


__all__ = ["AlwaysInput", "GpioInput", "InputDriver", "InputError", "create_inputs"]
