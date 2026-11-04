"""Product profiles: versioned, hash-pinned, read-only packages (PRF-*).

A profile is a directory (or a ``.zip``/``.tar.gz`` archive) with a
``manifest.yaml`` in the format of SRS Appendix B.  Loading always checks the
SHA-256 of every pinned file.  Optionally an Ed25519 signature is verified
against a station-trusted public key (PRF-005 / OPEN-F03).
"""

from __future__ import annotations

import ast
import base64
import contextlib
import math
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from hostcore.images import FirmwareImage, ImageError, parse_image, sha256_file
from hwval_factory import __version__


class ProfileError(RuntimeError):
    """Invalid, tampered or incompatible product profile."""


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(p) for p in parts[:4]) or (0,)


def factory_version_allows(minimum: str) -> bool:
    return _version_tuple(__version__) >= _version_tuple(minimum)


@dataclass(slots=True)
class ImageSpec:
    role: str
    file: str
    sha256: str
    slot: str = "A"
    address: int | None = None
    image: FirmwareImage | None = None

    @property
    def version(self) -> int:
        return 0 if self.image is None else self.image.version


@dataclass(slots=True)
class PartSpec:
    dev_id: int = 0x451
    rev_ids: list[int] = field(default_factory=lambda: [0x1001])
    flash_kb: int = 2048

    def accepts(self, dev_id: int, rev_id: int, flash_kb: int) -> bool:
        allowed_dev = self.dev_id if isinstance(self.dev_id, int) else 0
        return (
            dev_id == allowed_dev
            and (not self.rev_ids or rev_id in self.rev_ids)
            and flash_kb == self.flash_kb
        )


@dataclass(slots=True)
class TestPlan:
    tests: list[str] = field(default_factory=list)
    allow_operator: bool = True
    limits: dict[str, dict[str, float]] = field(default_factory=dict)
    timeout_s: float = 120.0
    destructive_allowed: bool = False


@dataclass(slots=True)
class CalibrationRule:
    field_id: int
    source: str
    rule: str = "round(value)"
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""

    def evaluate(self, value: float) -> float:
        result = safe_eval(self.rule, value)
        if self.minimum is not None and result < self.minimum:
            raise ProfileError(
                f"calibration {self.source} = {result} below {self.minimum}"
            )
        if self.maximum is not None and result > self.maximum:
            raise ProfileError(
                f"calibration {self.source} = {result} above {self.maximum}"
            )
        return result


_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Constant,
    ast.Call,
    ast.Name,
    ast.Load,
)
_ALLOWED_FUNCS = {
    "round": round,
    "abs": abs,
    "int": int,
    "float": float,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
}


def safe_eval(expression: str, value: float) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ProfileError(f"invalid calibration rule {expression!r}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ProfileError(f"unsupported expression in calibration rule {expression!r}")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS
        ):
            raise ProfileError(f"unsupported function in calibration rule {expression!r}")
        if isinstance(node, ast.Name) and node.id not in ({"value"} | set(_ALLOWED_FUNCS)):
            raise ProfileError(f"unknown name {node.id!r} in calibration rule")
    env = {"value": value, **_ALLOWED_FUNCS}
    try:
        result = eval(compile(tree, "<calibration>", "eval"), {"__builtins__": {}}, env)
    except Exception as exc:
        raise ProfileError(f"calibration rule failed: {exc}") from exc
    if not isinstance(result, (int, float)) or not math.isfinite(float(result)):
        raise ProfileError("calibration rule did not produce a finite number")
    return float(result)


@dataclass(slots=True)
class IdentitySpec:
    serial_format: str = "SC767-{yyww}-{counter:06}-{luhn}"
    mac_enabled: bool = False
    oui_block: str = ""
    allow_local: bool = True
    mac_start: int = 0


@dataclass(slots=True)
class ProtectionSpec:
    ndbank: int = 0
    ndboot: int = 0
    bor_lev: int = 3
    wrp_bank1: list[int] = field(default_factory=lambda: [0, 1])
    wrp_bank2: list[int] = field(default_factory=lambda: [1])
    rdp: int = 0
    boot_addr0: int = 0x0080
    boot_addr1: int = 0x0040
    optcr: int | None = None
    optcr1: int | None = None

    def validate(self) -> None:
        if self.rdp not in (0, 1):
            if self.rdp == 2:
                raise ProfileError("RDP level 2 is permanent and is never allowed (PROT-003)")
            raise ProfileError(f"invalid RDP level {self.rdp}")
        if self.bor_lev not in (0, 1, 2, 3):
            raise ProfileError(f"invalid BOR level {self.bor_lev}")
        for sector in self.wrp_bank1 + self.wrp_bank2:
            if not 0 <= sector <= 11:
                raise ProfileError(f"invalid WRP sector {sector}")

    @staticmethod
    def _bor_field(level: int) -> int:
        # RM0410 FLASH_OPTCR.BOR_LEV: 00 off, 01 level 3, 10 level 2,
        # 11 level 1.  Profiles name the level, not the raw field.
        return {0: 0, 3: 1, 2: 2, 1: 3}.get(level, 1)

    def computed_optcr(self) -> int:
        if self.optcr is not None:
            return self.optcr & 0xFFFFFFFF
        wrp1 = 0xFFF
        for sector in self.wrp_bank1:
            wrp1 &= ~(1 << sector)
        value = (wrp1 & 0xFFF) << 16
        # RDP level 0 is 0xAA on the wire; 0x00 always means level 1.
        value |= (0xAA if self.rdp == 0 else 0x00) << 8
        value |= 1 << 5  # WDG_SW
        value |= 1 << 6  # nRST_STOP
        value |= 1 << 7  # nRST_STDBY
        value |= (self._bor_field(self.bor_lev) & 0x3) << 2
        value |= (self.ndboot & 1) << 28
        value |= (self.ndbank & 1) << 29
        return value & 0xFFFFFFFF

    def computed_optcr1(self) -> int:
        if self.optcr1 is not None:
            return self.optcr1 & 0xFFFFFFFF
        # OPTCR1 is BOOT_ADD0[15:0] | BOOT_ADD1[31:16]; keep both.  Writing
        # only the WRP field here used to zero them and lose the ST ROM
        # bootloader entry (BOOT_ADD1 default 0x0040).
        return ((self.boot_addr0 & 0xFFFF) |
                ((self.boot_addr1 & 0xFFFF) << 16)) & 0xFFFFFFFF

    def as_dict(self) -> dict[str, Any]:
        return {
            "nDBANK": self.ndbank,
            "nDBOOT": self.ndboot,
            "BOR_LEV": self.bor_lev,
            "wrp_sectors": {"bank1": self.wrp_bank1, "bank2": self.wrp_bank2},
            "rdp": self.rdp,
            "OPTCR": self.computed_optcr(),
            "OPTCR1": self.computed_optcr1(),
        }


@dataclass(slots=True)
class LabelSpec:
    template: str = ""
    fields: list[str] = field(default_factory=list)
    width_mm: int = 50
    height_mm: int = 25


@dataclass(slots=True)
class ProductProfile:
    profile_id: str
    version: str
    product: str
    hw_revisions: list[str]
    min_factory_version: str
    released_by: str
    released_at: str
    parts: PartSpec
    images: dict[str, ImageSpec]
    test_plan: TestPlan
    calibration: list[CalibrationRule]
    identity: IdentitySpec
    protection: ProtectionSpec
    label: LabelSpec
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    signature_public_key: str = ""
    _tempdir: tempfile.TemporaryDirectory[str] | None = None

    @property
    def display(self) -> str:
        return f"{self.profile_id} v{self.version}"

    @property
    def test_image(self) -> ImageSpec:
        return self.images["test"]

    @property
    def boot_image(self) -> ImageSpec:
        return self.images["boot"]

    @property
    def app_image(self) -> ImageSpec:
        return self.images["app"]

    def accepts_revision(self, revision: str) -> bool:
        return not self.hw_revisions or revision.upper() in {r.upper() for r in self.hw_revisions}

    def image_path(self, role: str) -> Path:
        return self.path / self.images[role].file

    def verify_signature(self, public_key_b64: str | None = None) -> bool:
        key_text = public_key_b64 or self.signature_public_key
        if not self.signature or not key_text:
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError as exc:  # pragma: no cover
            raise ProfileError("cryptography is required for profile signatures") from exc
        try:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_text))
            key.verify(
                base64.b64decode(self.signature),
                (self.path / "manifest.yaml").read_bytes(),
            )
        except Exception:
            return False
        return True

    def check_factory_version(self) -> None:
        if not factory_version_allows(self.min_factory_version):
            raise ProfileError(
                f"profile requires Factory >= {self.min_factory_version}, "
                f"this is {__version__}"
            )

    def cleanup(self) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None


def _require(raw: dict[str, Any], key: str) -> Any:
    if key not in raw:
        raise ProfileError(f"manifest is missing {key!r}")
    return raw[key]


def _parse_released(raw: dict[str, Any]) -> tuple[str, str]:
    released = raw.get("released") or {}
    if isinstance(released, dict):
        by = str(released.get("by") or "")
        at = released.get("at") or ""
        return by, str(at)
    return "", ""


def _parse_images(profile_dir: Path, raw: dict[str, Any]) -> dict[str, ImageSpec]:
    images_raw = _require(raw, "images")
    if not isinstance(images_raw, dict):
        raise ProfileError("manifest.images must be a mapping")
    specs: dict[str, ImageSpec] = {}

    def parse_one(role: str, entry: object) -> ImageSpec:
        if not isinstance(entry, dict):
            raise ProfileError(f"manifest.images.{role} is required")
        file_name = str(entry.get("file") or "")
        digest = str(entry.get("sha256") or "").lower()
        if not file_name or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProfileError(f"manifest.images.{role} needs file and a SHA-256")
        path = (profile_dir / file_name).resolve()
        if profile_dir.resolve() not in path.parents and path != profile_dir.resolve():
            raise ProfileError(f"image path escapes the profile: {file_name}")
        if not path.is_file():
            raise ProfileError(f"profile image not found: {file_name}")
        actual = sha256_file(path)
        if actual != digest:
            raise ProfileError(f"image hash mismatch for {file_name}: {actual} != {digest}")
        try:
            image = parse_image(path)
        except ImageError as exc:
            raise ProfileError(str(exc)) from exc
        return ImageSpec(
            role=role,
            file=file_name,
            sha256=digest,
            slot=str(entry.get("slot") or "A"),
            address=int(entry["address"], 0) if entry.get("address") else None,
            image=image,
        )

    for role in ("test", "boot", "app"):
        entry = images_raw.get(role)
        if role == "app" and isinstance(entry, list):
            if not entry:
                raise ProfileError("manifest.images.app is empty")
            parsed = [parse_one(f"app:{index}", item) for index, item in enumerate(entry)]
            selected = next(
                (item for item in parsed if item.slot.upper() == "A"), parsed[0]
            )
            specs["app"] = ImageSpec(
                role="app",
                file=selected.file,
                sha256=selected.sha256,
                slot=selected.slot,
                address=selected.address,
                image=selected.image,
            )
            for index, item in enumerate(parsed[1:], 1):
                specs[f"app:{index}"] = item
            continue
        specs[role] = parse_one(role, entry)
    return specs


def _parse_tests(raw: dict[str, Any]) -> TestPlan:
    test_plan = raw.get("test_plan") or {}
    if not isinstance(test_plan, dict):
        raise ProfileError("manifest.test_plan must be a mapping")
    tests = test_plan.get("tests") or []
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        raise ProfileError("test_plan.tests must be a list of test IDs")
    limits_raw = test_plan.get("limits") or {}
    limits: dict[str, dict[str, float]] = {}
    if isinstance(limits_raw, dict):
        for key, value in limits_raw.items():
            if not isinstance(value, dict):
                continue
            limit: dict[str, float] = {}
            for bound in ("min", "max"):
                if value.get(bound) is not None:
                    limit[bound] = float(value[bound])
            limits[str(key)] = limit
    return TestPlan(
        tests=[str(t) for t in tests],
        allow_operator=bool(test_plan.get("allow_operator", True)),
        limits=limits,
        timeout_s=float(test_plan.get("timeout_s") or 120.0),
        destructive_allowed=bool(test_plan.get("destructive_allowed", False)),
    )


def _parse_calibration(raw: dict[str, Any]) -> list[CalibrationRule]:
    items = raw.get("calibration") or []
    if not isinstance(items, list):
        raise ProfileError("manifest.calibration must be a list")
    rules: list[CalibrationRule] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        field_raw = item.get("field")
        try:
            field_id = int(str(field_raw), 0)
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"invalid calibration field {field_raw!r}") from exc
        if not 0x20 <= field_id <= 0x2F:
            raise ProfileError(f"calibration field {field_id:#x} outside 0x20-0x2F")
        source = str(item.get("from") or "")
        if not source:
            raise ProfileError("calibration entry is missing 'from'")
        range_raw = item.get("range") or []
        minimum = float(range_raw[0]) if len(range_raw) >= 1 else None
        maximum = float(range_raw[1]) if len(range_raw) >= 2 else None
        rules.append(
            CalibrationRule(
                field_id=field_id,
                source=source,
                rule=str(item.get("rule") or "round(value)"),
                minimum=minimum,
                maximum=maximum,
                unit=str(item.get("unit") or ""),
            )
        )
    return rules


def _parse_identity(raw: dict[str, Any]) -> IdentitySpec:
    identity = raw.get("identity") or {}
    if not isinstance(identity, dict):
        raise ProfileError("manifest.identity must be a mapping")
    serial = identity.get("serial")
    if isinstance(serial, dict):
        serial_format = str(serial.get("format") or "SC767-{yyww}-{counter:06}-{luhn}")
    else:
        serial_format = str(serial or "SC767-{yyww}-{counter:06}-{luhn}")
    mac = identity.get("mac") or {}
    if not isinstance(mac, dict):
        raise ProfileError("identity.mac must be a mapping")
    return IdentitySpec(
        serial_format=serial_format,
        mac_enabled=bool(mac.get("enabled", False)),
        oui_block=str(mac.get("oui_block") or ""),
        allow_local=bool(mac.get("allow_local", True)),
        mac_start=int(str(mac.get("start") or "0"), 0) if mac.get("start") else 0,
    )


def _parse_protection(raw: dict[str, Any]) -> ProtectionSpec:
    protection = raw.get("protection") or {}
    if not isinstance(protection, dict):
        raise ProfileError("manifest.protection must be a mapping")
    wrp = protection.get("wrp_sectors") or {}
    bank1_raw = wrp.get("bank1") if isinstance(wrp, dict) else None
    bank2_raw = wrp.get("bank2") if isinstance(wrp, dict) else None
    spec = ProtectionSpec(
        ndbank=int(protection.get("nDBANK", 0)) & 1,
        ndboot=int(protection.get("nDBOOT", 0)) & 1,
        bor_lev=int(protection.get("BOR_LEV", 3)),
        wrp_bank1=[int(x) for x in (bank1_raw if bank1_raw is not None else [0, 1])],
        wrp_bank2=[int(x) for x in (bank2_raw if bank2_raw is not None else [1])],
        rdp=int(protection.get("rdp", 0)),
        optcr=int(protection["OPTCR"], 0) if protection.get("OPTCR") else None,
        optcr1=int(protection["OPTCR1"], 0) if protection.get("OPTCR1") else None,
    )
    spec.validate()
    return spec


def _parse_label(raw: dict[str, Any], profile_dir: Path) -> LabelSpec:
    label = raw.get("label") or {}
    if not isinstance(label, dict):
        raise ProfileError("manifest.label must be a mapping")
    template = str(label.get("template") or "")
    if template:
        path = profile_dir / template
        if not path.is_file():
            raise ProfileError(f"label template not found: {template}")
    fields = label.get("fields") or []
    if not isinstance(fields, list):
        raise ProfileError("label.fields must be a list")
    return LabelSpec(
        template=template,
        fields=[str(f) for f in fields],
        width_mm=int(label.get("width_mm") or 50),
        height_mm=int(label.get("height_mm") or 25),
    )


def _load_manifest(profile_dir: Path) -> dict[str, Any]:
    manifest_path = profile_dir / "manifest.yaml"
    if not manifest_path.is_file():
        manifest_path = profile_dir / "manifest.yml"
    if not manifest_path.is_file():
        raise ProfileError("profile has no manifest.yaml")
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid profile manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError("profile manifest must be a mapping")
    return raw


def load_profile(
    path: str | Path,
    *,
    require_signature: bool = False,
    public_key: str | None = None,
) -> ProductProfile:
    source = Path(path)
    tempdir: tempfile.TemporaryDirectory[str] | None = None
    if source.is_dir():
        profile_dir = source
    elif source.is_file() and zipfile.is_zipfile(source):
        tempdir = tempfile.TemporaryDirectory(prefix="hwval-profile-")
        with zipfile.ZipFile(source) as archive:
            root = Path(tempdir.name).resolve()
            for member in archive.infolist():
                target = (root / member.filename).resolve()
                if not target.is_relative_to(root):
                    raise ProfileError("profile archive contains an unsafe path")
            archive.extractall(tempdir.name)
        profile_dir = _find_profile_root(Path(tempdir.name))
    elif source.is_file() and tarfile.is_tarfile(source):
        tempdir = tempfile.TemporaryDirectory(prefix="hwval-profile-")
        with tarfile.open(source) as archive:
            archive.extractall(tempdir.name, filter="data")
        profile_dir = _find_profile_root(Path(tempdir.name))
    else:
        raise ProfileError(f"profile not found or unsupported archive: {source}")

    try:
        raw = _load_manifest(profile_dir)
        profile_id = str(_require(raw, "profile"))
        version = str(_require(raw, "version"))
        product = str(_require(raw, "product"))
        hw_revisions = [str(r) for r in (_require(raw, "hw_revisions") or [])]
        min_factory = str(raw.get("min_factory_version") or "0.0.0")
        released_by, released_at = _parse_released(raw)
        parts_raw = _require(raw, "parts")
        if not isinstance(parts_raw, dict):
            raise ProfileError("manifest.parts must be a mapping")
        rev_ids = parts_raw.get("rev_id") or []
        if not isinstance(rev_ids, list):
            rev_ids = [rev_ids]
        parts = PartSpec(
            dev_id=int(parts_raw.get("dev_id", 0x451)),
            rev_ids=[int(r) for r in rev_ids],
            flash_kb=int(parts_raw.get("flash_kb", 2048)),
        )
        images = _parse_images(profile_dir, raw)
        test_plan = _parse_tests(raw)
        calibration = _parse_calibration(raw)
        identity = _parse_identity(raw)
        protection = _parse_protection(raw)
        label = _parse_label(raw, profile_dir)
        signature = str(raw.get("signature") or "")
        signature_file = profile_dir / str(raw.get("signature_file") or "manifest.sig")
        if not signature and signature_file.is_file():
            signature = signature_file.read_text(encoding="ascii").strip()
        signature_key = str(raw.get("signature_public_key") or "")
        profile = ProductProfile(
            profile_id=profile_id,
            version=version,
            product=product,
            hw_revisions=hw_revisions,
            min_factory_version=min_factory,
            released_by=released_by,
            released_at=released_at,
            parts=parts,
            images=images,
            test_plan=test_plan,
            calibration=calibration,
            identity=identity,
            protection=protection,
            label=label,
            path=profile_dir.resolve(),
            manifest=raw,
            signature=signature,
            signature_public_key=signature_key,
            _tempdir=tempdir,
        )
        profile.check_factory_version()
        if require_signature and not profile.verify_signature(public_key):
            raise ProfileError("profile signature is required and could not be verified")
        return profile
    except Exception:
        if tempdir is not None:
            tempdir.cleanup()
        raise


def _find_profile_root(root: Path) -> Path:
    if (root / "manifest.yaml").is_file() or (root / "manifest.yml").is_file():
        return root
    entries = [p for p in root.iterdir() if p.is_dir()]
    if len(entries) == 1:
        return entries[0]
    raise ProfileError("archive does not contain a manifest.yaml at its root")


def import_profile(source: str | Path, profiles_dir: str | Path) -> Path:
    """Copy a released profile into the station's read-only profile store."""
    src = Path(source)
    if not src.exists():
        raise ProfileError(f"profile not found: {src}")
    destination = Path(profiles_dir)
    destination.mkdir(parents=True, exist_ok=True)
    name = src.stem if src.is_file() else src.name
    target = destination / name
    if target.exists():
        raise ProfileError(f"profile already imported: {target}")
    if src.is_dir():
        shutil.copytree(src, target)
        for path in target.rglob("*"):
            with contextlib.suppress(OSError):
                os.chmod(path, 0o555 if path.is_dir() else 0o444)
    else:
        shutil.copy2(src, destination / src.name)
        target = destination / src.name
        with contextlib.suppress(OSError):
            os.chmod(target, 0o444)
    return target


def list_profiles(
    profiles_dir: str | Path,
    *,
    require_signature: bool = False,
    public_key: str | None = None,
) -> list[ProductProfile]:
    root = Path(profiles_dir)
    if not root.is_dir():
        return []
    results: list[ProductProfile] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir() or entry.suffix.lower() in (".zip", ".tar", ".gz", ".tgz"):
            try:
                results.append(
                    load_profile(
                        entry,
                        require_signature=require_signature,
                        public_key=public_key,
                    )
                )
            except ProfileError:
                continue
    return results


def find_profile(
    profiles_dir: str | Path,
    identifier: str,
    *,
    require_signature: bool = False,
    public_key: str | None = None,
) -> ProductProfile:
    """Find a profile by ID, ID@version, directory name or manifest path."""
    candidate = Path(identifier)
    if candidate.exists():
        return load_profile(
            candidate,
            require_signature=require_signature,
            public_key=public_key,
        )
    root = Path(profiles_dir)
    for profile in list_profiles(
        root, require_signature=require_signature, public_key=public_key
    ):
        if profile.profile_id == identifier or profile.path.name == identifier:
            return profile
        if identifier == f"{profile.profile_id}@{profile.version}":
            return profile
    raise ProfileError(f"profile {identifier!r} not found in {root}")


def manifest_for_directory(
    directory: str | Path,
    *,
    profile_id: str,
    version: str,
    product: str,
    hw_revisions: list[str],
    min_factory_version: str = "0.1.0",
    author: str = "",
    **sections: Any,
) -> dict[str, Any]:
    """Create a manifest skeleton with SHA-256 pinned files."""
    root = Path(directory)
    images_raw = sections.pop("images", {})
    images: dict[str, Any] = {}
    for role, file_name in images_raw.items():
        path = root / str(file_name)
        images[role] = {"file": str(file_name), "sha256": sha256_file(path)}
    manifest: dict[str, Any] = {
        "profile": profile_id,
        "version": version,
        "product": product,
        "hw_revisions": hw_revisions,
        "min_factory_version": min_factory_version,
        "released": {
            "by": author,
            "at": date.today().isoformat(),
        },
        "parts": sections.pop("parts", {"dev_id": 0x451, "rev_id": [0x1001], "flash_kb": 2048}),
        "images": images,
        "test_plan": sections.pop("test_plan", {"tests": [], "allow_operator": True}),
        "calibration": sections.pop("calibration", []),
        "identity": sections.pop(
            "identity",
            {
                "serial": "SC767-{yyww}-{counter:06}-{luhn}",
                "mac": {"enabled": False, "oui_block": "", "allow_local": True},
            },
        ),
        "protection": sections.pop(
            "protection",
            {
                "nDBANK": 0,
                "BOR_LEV": 3,
                "wrp_sectors": {"bank1": [0, 1], "bank2": [1]},
                "rdp": 0,
            },
        ),
        "label": sections.pop(
            "label",
            {
                "template": "",
                "fields": ["serial", "product", "hw_revision", "date", "fw_version"],
            },
        ),
    }
    manifest.update(sections)
    return manifest


def sign_manifest(profile_dir: str | Path, private_key_path: str | Path) -> Path:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover
        raise ProfileError("cryptography is required to sign profiles") from exc
    root = Path(profile_dir)
    key_data = Path(private_key_path).read_bytes()
    try:
        key = serialization.load_pem_private_key(key_data, password=None)
    except (ValueError, TypeError):
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_data.decode().strip()))
    if not isinstance(key, Ed25519PrivateKey):
        raise ProfileError("signing key is not Ed25519")
    signature = key.sign((root / "manifest.yaml").read_bytes())
    target = root / "manifest.sig"
    target.write_text(base64.b64encode(signature).decode("ascii"), encoding="ascii")
    return target


def generate_signing_key(private_path: str | Path, public_path: str | Path) -> None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover
        raise ProfileError("cryptography is required to create keys") from exc
    key = Ed25519PrivateKey.generate()
    Path(private_path).write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    Path(public_path).write_text(
        base64.b64encode(
            key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
        encoding="ascii",
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def manifest_age_days(profile: ProductProfile) -> int:
    try:
        released = date.fromisoformat(profile.released_at[:10])
    except ValueError:
        return 0
    return (date.today() - released).days


# Re-export for callers that only need typing of option bytes.
def option_bytes_from_raw(optcr: int, optcr1: int) -> OptionBytesView:
    return OptionBytesView(optcr=optcr, optcr1=optcr1)


@dataclass(slots=True)
class OptionBytesView:
    optcr: int
    optcr1: int

    @property
    def rdp(self) -> int:
        return (self.optcr >> 8) & 0xFF

    @property
    def bor_level(self) -> int:
        return (self.optcr >> 2) & 0x3

    @property
    def ndbank(self) -> int:
        return (self.optcr >> 29) & 1

    @property
    def ndboot(self) -> int:
        return (self.optcr >> 28) & 1

    @property
    def bank1_wrp(self) -> int:
        return (self.optcr >> 16) & 0xFFF

    @property
    def bank2_wrp(self) -> int:
        return self.optcr1 & 0xFFF
