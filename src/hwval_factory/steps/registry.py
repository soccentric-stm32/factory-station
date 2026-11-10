"""Step registry, recipe validation and the default Flow A recipe (P1-03).

A recipe is an ordered list of step keys.  ``validate_recipe`` refuses a
recipe that uses a fact before a step provides it, which is what the profile
tool checks at load time and what ``build_steps`` assumes at run time.
"""

from __future__ import annotations

from typing import Any

from hwval_factory.profile import ProductProfile, ProfileError
from hwval_factory.steps.base import StepDef, StepSpec

FLOW_B_RECIPE = [
    "scan",
    "identify",
    "product_config",
    "program_flowb",
    "link_up",
    "test",
    "calibrate",
    "provision",
    "end_test",
    "boot_check",
    "protect",
    "label",
    "record",
]

DEFAULT_RECIPE = [
    "scan",
    "identify",
    "prepare",
    "test",
    "calibrate",
    "program",
    "provision",
    "boot_check",
    "protect",
    "label",
    "record",
]

STEP_SPECS: dict[str, StepSpec] = {
    "scan": StepSpec(
        key="scan",
        name="Scan board",
        provides=("pcb_serial",),
        standalone=True,
        safe_state_after="scanned",
        handler_name="_step_scan",
    ),
    "identify": StepSpec(
        key="identify",
        name="Identify chip",
        provides=("uid", "option_bytes", "hw_revision", "floor_before"),
        standalone=True,
        safe_state_after="identified",
        default_retries=1,
        handler_name="_step_identify",
    ),
    "prepare": StepSpec(
        key="prepare",
        name="Prepare test image",
        requires=("uid",),
        provides=("test_image",),
        standalone=True,
        safe_state_after="test_loaded",
        default_retries=1,
        handler_name="_step_prepare",
    ),
    "test": StepSpec(
        key="test",
        name="Run EOL test plan",
        requires=("uid",),
        provides=("test_results",),
        standalone=True,
        safe_state_after="tested",
        default_retries=1,
        handler_name="_step_test",
    ),
    "calibrate": StepSpec(
        key="calibrate",
        name="Compute calibration",
        requires=("test_results",),
        provides=("calibration",),
        safe_state_after="calibrated",
        handler_name="_step_calibrate",
    ),
    "program": StepSpec(
        key="program",
        name="Program production",
        requires=("uid",),
        provides=("app_image", "expected_floor", "floor_after"),
        standalone=True,
        safe_state_after="programmed",
        default_retries=1,
        handler_name="_step_program",
    ),
    "provision": StepSpec(
        key="provision",
        name="Provision unit",
        requires=("uid", "pcb_serial"),
        provides=("serial_number", "mac", "provisioning_blob"),
        standalone=True,
        safe_state_after="personalised",
        handler_name="_step_provision",
    ),
    "boot_check": StepSpec(
        key="boot_check",
        name="Boot check",
        requires=("uid", "serial_number"),
        provides=("boot_checked",),
        standalone=True,
        safe_state_after="boot_checked",
        default_retries=2,
        handler_name="_step_boot_check",
    ),
    "protect": StepSpec(
        key="protect",
        name="Apply protection",
        requires=("boot_checked",),
        provides=("protected",),
        standalone=True,
        safe_state_after="protected",
        handler_name="_step_protect",
    ),
    "label": StepSpec(
        key="label",
        name="Print label",
        requires=("serial_number",),
        provides=("label",),
        standalone=True,
        default_retries=1,
        handler_name="_step_label",
    ),
    "product_config": StepSpec(
        key="product_config",
        name="Product configuration",
        requires=("uid",),
        provides=("product_blank",),
        safe_state_after="product_blank",
        handler_name="_step_product_config",
    ),
    "program_flowb": StepSpec(
        key="program_flowb",
        name="Program Flow B slots",
        requires=("uid",),
        provides=("app_image", "expected_floor", "test_slot"),
        standalone=True,
        safe_state_after="flowb_programmed",
        default_retries=1,
        handler_name="_step_program_flowb",
    ),
    "link_up": StepSpec(
        key="link_up",
        name="Link to HWVAL trial",
        requires=("uid",),
        provides=("linked",),
        standalone=True,
        safe_state_after="link_up",
        handler_name="_step_link_up",
    ),
    "end_test": StepSpec(
        key="end_test",
        name="End test image",
        requires=("test_results",),
        provides=("slot_b_erased",),
        safe_state_after="slot_b_erased",
        handler_name="_step_end_test",
    ),
    "self_test": StepSpec(
        key="self_test",
        name="Golden-board self-test",
        provides=("self_test_passed",),
        standalone=True,
        handler_name="_step_self_test",
    ),
    "record": StepSpec(
        key="record",
        name="Record run",
        provides=("done",),
        standalone=True,
        handler_name="_step_record",
    ),
}


def _fact_set(keys: list[str]) -> set[str]:
    facts: set[str] = set()
    for key in keys:
        spec = STEP_SPECS.get(key)
        if spec is not None:
            facts.update(spec.provides)
    return facts


def default_recipe(profile: ProductProfile) -> list[str]:
    return list(FLOW_B_RECIPE if profile.test_mode == "under_boot" else DEFAULT_RECIPE)


def validate_recipe(recipe: list[str], *, has_calibration: bool = True) -> list[str]:
    """Return the effective recipe or raise ``ProfileError``.

    Unknown steps, duplicate steps and facts used before they are provided are
    refused.  ``calibrate`` is dropped when the profile defines no calibration
    rules, matching the historical Flow A behaviour.
    """
    if not recipe:
        recipe = list(DEFAULT_RECIPE)
    effective: list[str] = []
    for key in recipe:
        if key not in STEP_SPECS:
            raise ProfileError(f"recipe step {key!r} is not a known step")
        if key in effective:
            raise ProfileError(f"recipe repeats step {key!r}")
        if key == "calibrate" and not has_calibration:
            continue
        effective.append(key)
    provided: set[str] = set()
    for key in effective:
        spec = STEP_SPECS[key]
        missing = [fact for fact in spec.requires if fact not in provided]
        if missing:
            raise ProfileError(
                f"recipe step {key!r} requires {', '.join(missing)} before it is provided"
            )
        provided.update(spec.provides)
    return effective


def build_steps(
    profile: ProductProfile,
    station: Any,
    *,
    recipe: list[str] | None = None,
) -> list[StepDef]:
    """Build the concrete handlers for a profile, applying its step options."""
    config = profile.manifest.get("steps") or {}
    if not isinstance(config, dict):
        config = {}
    enabled = config.get("enabled") or {}
    retries_cfg = config.get("retries") or {}
    retries = (
        {str(k): int(v) for k, v in retries_cfg.items()}
        if isinstance(retries_cfg, dict)
        else {}
    )
    effective = validate_recipe(
        recipe if recipe is not None else (profile.recipe or default_recipe(profile)),
        has_calibration=bool(profile.calibration),
    )
    steps: list[StepDef] = []
    for key in effective:
        if key in ("protect", "label") and enabled.get(key) is False:
            continue
        spec = STEP_SPECS[key]
        handler = getattr(station, spec.handler_name)
        steps.append(
            StepDef(
                key=key,
                name=spec.name,
                handler=handler,
                retries=retries.get(key, spec.default_retries),
            )
        )
    return steps


def standalone_steps() -> list[StepSpec]:
    return [spec for spec in STEP_SPECS.values() if spec.standalone]


__all__ = [
    "DEFAULT_RECIPE",
    "FLOW_B_RECIPE",
    "STEP_SPECS",
    "build_steps",
    "default_recipe",
    "standalone_steps",
    "validate_recipe",
]
