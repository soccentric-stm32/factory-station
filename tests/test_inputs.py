from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.inputs import GpioInput, InputError, create_inputs


def test_always_inputs_are_safe() -> None:
    inputs = create_inputs({"lid": {"driver": "none"}, "board_present": {"driver": "none"}})
    assert inputs["lid"].closed()
    assert inputs["board_present"].present()


def test_gpio_input_reads_value_and_polarity(tmp_path: Path) -> None:
    value = tmp_path / "value"
    value.write_text("1\n", encoding="ascii")
    assert GpioInput(value).closed()
    value.write_text("0\n", encoding="ascii")
    assert not GpioInput(value).closed()
    assert GpioInput(value, active_high=False).closed()


def test_gpio_input_rejects_bad_value_and_missing_path(tmp_path: Path) -> None:
    value = tmp_path / "value"
    value.write_text("x", encoding="ascii")
    with pytest.raises(InputError, match="expected 0 or 1"):
        GpioInput(value).closed()
    with pytest.raises(InputError, match="cannot read"):
        GpioInput(tmp_path / "missing").closed()
