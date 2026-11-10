"""Signed-profile checks (P0-04, P0-09, §6.3)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest
import yaml

from hostcore import layout
from hostcore.images import (
    key_id,
    load_key,
    parse_image,
    public_key_pem,
    public_point,
    sha256_file,
    sign_header,
)
from hwval_factory.profile import ProfileError, load_profile
from hwval_factory.profile_tool import _previous_counter, _verify

ROOT = Path(__file__).resolve().parents[1]
SIGNED = ROOT / "profiles" / "sc767-1.1.0"


def _read_manifest(path: Path) -> dict:
    return yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))


def _write_manifest(path: Path, raw: dict) -> None:
    (path / "manifest.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _copy_profile(tmp_path: Path, name: str = "profile") -> Path:
    target = tmp_path / name
    shutil.copytree(SIGNED, target)
    return target


def _resign_app(profile_dir: Path, *, counter: int, key) -> None:
    """Replace the app with one signed by ``key`` at ``counter``."""
    raw = _read_manifest(profile_dir)
    app_name = raw["images"]["app"]["file"]
    app_path = profile_dir / app_name
    parsed = parse_image(app_path)
    signed = sign_header(parsed.header, parsed.data, key, counter) + parsed.data
    app_path.write_bytes(signed)
    key_path = profile_dir / "signing-public-key.pem"
    key_path.write_text(public_key_pem(key), encoding="ascii")
    raw["images"]["app"]["sha256"] = sha256_file(app_path)
    raw["signing"]["public_key"] = key_path.name
    raw["signing"]["key_id"] = f"0x{key_id(public_point(key)):08x}"
    raw["signing"]["security_counter"] = counter
    _write_manifest(profile_dir, raw)


def test_released_signed_profile_loads_and_verifies() -> None:
    profile = load_profile(SIGNED)
    image = profile.app_image.image
    assert image is not None and image.is_signed
    assert image.security_counter == 1
    assert image.expected_floor == 1
    assert image.key_id == layout.DEVELOPMENT_KEY_ID
    assert profile.signing.public_key == "signing-public-key.pem"
    assert profile.signing_public_key_path is not None
    assert profile.factory_image is not None
    assert profile.factory_image.file == "factory-1.1.0.hex"
    assert profile.test_mode == "bench"
    assert profile.slot_b_after_test == "erase"


def test_production_refuses_the_development_key(tmp_path: Path) -> None:
    target = _copy_profile(tmp_path)
    raw = _read_manifest(target)
    raw["production"] = True
    _write_manifest(target, raw)
    with pytest.raises(ProfileError, match="development key"):
        load_profile(target)


def test_production_refuses_an_unsigned_app(tmp_path: Path) -> None:
    target = tmp_path / "legacy"
    shutil.copytree(ROOT / "profiles" / "sc767-1.0.0", target)
    raw = _read_manifest(target)
    raw["production"] = True
    _write_manifest(target, raw)
    with pytest.raises(ProfileError, match="signed"):
        load_profile(target)


def test_wrong_public_key_is_refused(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    target = _copy_profile(tmp_path)
    other = ec.generate_private_key(ec.SECP256R1())
    (target / "signing-public-key.pem").write_text(public_key_pem(other), encoding="ascii")
    raw = _read_manifest(target)
    raw["signing"]["key_id"] = f"0x{key_id(public_point(other)):08x}"
    _write_manifest(target, raw)
    with pytest.raises(ProfileError, match="does not verify"):
        load_profile(target)


def test_missing_public_key_file_is_refused(tmp_path: Path) -> None:
    target = _copy_profile(tmp_path)
    raw = _read_manifest(target)
    raw["signing"]["public_key"] = "missing.pem"
    _write_manifest(target, raw)
    with pytest.raises(ProfileError, match="not a file"):
        load_profile(target)


def test_counter_below_the_previous_release_is_refused(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    parent = tmp_path / "profiles"
    parent.mkdir()
    older = parent / "sc767-1.1.0"
    shutil.copytree(SIGNED, older)
    _resign_app(older, counter=5, key=key)
    older_raw = _read_manifest(older)
    older_raw["version"] = "1.1.0"
    _write_manifest(older, older_raw)

    newer = parent / "sc767-1.2.0"
    shutil.copytree(SIGNED, newer)
    _resign_app(newer, counter=4, key=key)
    newer_raw = _read_manifest(newer)
    newer_raw["version"] = "1.2.0"
    _write_manifest(newer, newer_raw)

    profile = load_profile(newer)
    assert _previous_counter(profile) == 5
    code = _verify(argparse.Namespace(directory=newer, public_key=""))
    assert code == 1
