"""Step registry, recipe validation and failure text (P1-02/P1-03/P1-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwval_factory.profile import ProfileError, load_profile
from hwval_factory.steps.base import StepFailure
from hwval_factory.steps.registry import (
    DEFAULT_RECIPE,
    STEP_SPECS,
    build_steps,
    standalone_steps,
    validate_recipe,
)

ROOT = Path(__file__).resolve().parents[1]


def test_default_recipe_is_valid_and_ordered() -> None:
    assert validate_recipe(list(DEFAULT_RECIPE)) == DEFAULT_RECIPE


def test_recipe_refuses_an_unknown_step() -> None:
    with pytest.raises(ProfileError, match="not a known step"):
        validate_recipe(["scan", "teleport", "record"])


def test_recipe_refuses_a_fact_before_it_is_provided() -> None:
    with pytest.raises(ProfileError, match="requires uid"):
        validate_recipe(["scan", "boot_check", "record"])


def test_recipe_refuses_duplicates() -> None:
    with pytest.raises(ProfileError, match="repeats"):
        validate_recipe(["scan", "scan", "record"])


def test_calibrate_drops_out_without_rules() -> None:
    assert "calibrate" not in validate_recipe(list(DEFAULT_RECIPE), has_calibration=False)


def test_every_step_has_a_handler_and_metadata() -> None:
    for key, spec in STEP_SPECS.items():
        assert spec.key == key
        assert spec.handler_name
        assert spec.name
    assert {spec.key for spec in standalone_steps()} >= {
        "scan",
        "identify",
        "test",
        "program",
        "boot_check",
        "protect",
    }


def test_build_steps_uses_the_profile_recipe() -> None:
    profile = load_profile(ROOT / "examples/profile-sc767-demo")
    profile.recipe = ["scan", "identify", "record"]

    class Fake:
        def _step_scan(self, ctx, rt): ...
        def _step_identify(self, ctx, rt): ...
        def _step_record(self, ctx, rt): ...

    steps = build_steps(profile, Fake())
    assert [step.key for step in steps] == ["scan", "identify", "record"]


def test_invalid_recipe_is_refused_by_the_loader(tmp_path: Path) -> None:
    import shutil

    import yaml

    target = tmp_path / "profile"
    shutil.copytree(ROOT / "examples/profile-sc767-demo", target)
    raw = yaml.safe_load((target / "manifest.yaml").read_text(encoding="utf-8"))
    raw["recipe"] = ["scan", "boot_check", "record"]
    (target / "manifest.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="requires uid"):
        load_profile(target)


def test_every_failure_has_a_cause_and_an_action() -> None:
    failure = StepFailure("wrong part")
    text = failure.as_text()
    assert failure.cause == "wrong part"
    assert "action:" in text
    specific = StepFailure(
        "signature mismatch",
        cause="the profile key is not BOOT's key",
        action="release with the product key",
        measured="0xfaa7171b",
        expected="0x12345678",
    )
    assert "measured 0xfaa7171b" in specific.as_text()
    assert "release with the product key" in specific.as_text()
