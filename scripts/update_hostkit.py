#!/usr/bin/env python3
"""Vendor the canonical HWLINK contract into the station (P0-02).

The BOOT contract is published by the bootloader repository with
``make package-hwlink``.  HWVAL-only tables live in the validation-firmware
repository.  The station needs both, so this script:

1. builds (or takes) the bootloader bundle;
2. copies its schema, generated Python package, ``mkimage.py``,
   ``boot_layout.h`` and flatbuffers runtime into ``src/hostcore/vendor/``;
3. composes the station schema from the BOOT schema (authoritative) and the
   HWVAL section of ``validation-firmware/protocol/hwlink.fbs``;
4. regenerates the Python bindings with ``flatc`` when it is installed, or
   copies the bundle's BOOT bindings and rewrites the union enum when it is
   not;
5. records the bundle hash in ``src/hostcore/BUNDLE``.

Nothing under ``src/hostcore/vendor`` or ``src/hostcore/protocol/generated``
is edited by hand; run this script instead.

    scripts/update_hostkit.py                 # build bootloader/bootloader first
    scripts/update_hostkit.py --bundle PATH   # use an existing bundle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOTLOADER = ROOT.parents[1] / "bootloader" / "bootloader"
DEFAULT_HWVAL = ROOT.parents[1] / "validation" / "validation-firmware"
VENDOR = ROOT / "src" / "hostcore" / "vendor"
SCHEMA = ROOT / "src" / "hostcore" / "protocol" / "hwlink.fbs"
GENERATED = ROOT / "src" / "hostcore" / "protocol" / "generated" / "hwlink"
BUNDLE_RECORD = ROOT / "src" / "hostcore" / "BUNDLE"

UNION_MARKER = "// The union order is part of the wire format. Append new members only."
HWVAL_MARKER = "// ------------------------------------------------------------------- HWVAL"
REQUIRED_BUNDLE_FILES = (
    "protocol/hwlink.fbs",
    "include/boot_layout.h",
    "mkimage.py",
    "SHA256SUMS",
)


class UpdateError(RuntimeError):
    """The bundle or the firmware repositories are not usable."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(bootloader: Path) -> Path:
    if not (bootloader / "Makefile").is_file():
        raise UpdateError(f"bootloader repository not found: {bootloader}")
    print(f"update_hostkit: building bundle in {bootloader}")
    subprocess.run(
        ["make", "--no-print-directory", "-C", str(bootloader), "package-hwlink"],
        check=True,
    )
    return bootloader / "build" / "soccentric-hwlink"


def check_bundle(bundle: Path) -> None:
    missing = [name for name in REQUIRED_BUNDLE_FILES if not (bundle / name).is_file()]
    if missing:
        raise UpdateError(f"{bundle} is not a complete bundle (missing {', '.join(missing)})")


def copy_vendor(bundle: Path) -> None:
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)
    for relative in ("protocol/hwlink.fbs", "include/boot_layout.h", "mkimage.py", "SHA256SUMS"):
        target = VENDOR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / relative, target)
    for name in ("hwlink", "flatbuffers"):
        shutil.copytree(
            bundle / name,
            VENDOR / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


def _union_members(text: str) -> list[str]:
    match = re.search(r"union\s+Body\s*\{(.*?)\}", text, re.S)
    if not match:
        raise UpdateError("schema has no union Body")
    return [line.strip().rstrip(",") for line in match.group(1).splitlines() if line.strip() and not line.strip().startswith("//")]


def _hwval_section(vf_schema: Path) -> str:
    text = vf_schema.read_text(encoding="utf-8")
    start = text.find(HWVAL_MARKER)
    if start < 0:
        raise UpdateError(f"{vf_schema} has no HWVAL section")
    return text[start:]


def compose_schema(bundle: Path, hwval: Path) -> str:
    boot_text = (bundle / "protocol" / "hwlink.fbs").read_text(encoding="utf-8")
    vf_schema = hwval / "protocol" / "hwlink.fbs"
    if not vf_schema.is_file():
        raise UpdateError(f"HWVAL schema not found: {vf_schema}")
    hwval_text = _hwval_section(vf_schema)

    boot_union = _union_members(boot_text)
    full_union = _union_members(hwval_text)
    if full_union[: len(boot_union)] != boot_union:
        raise UpdateError(
            "the HWVAL union Body is not an append-only extension of the BOOT union; "
            "fix the firmware schemas before vendoring"
        )

    split = boot_text.find(UNION_MARKER)
    if split < 0:
        raise UpdateError("the BOOT schema has no union marker")
    composed = boot_text[:split].rstrip() + "\n\n" + hwval_text.lstrip()
    _ = hwval_text  # kept for clarity: the union lives at the end of that text
    return composed


def regenerate_generated(composed: str, bundle: Path) -> str:
    flatc = shutil.which("flatc")
    if flatc:
        GENERATED.parent.mkdir(parents=True, exist_ok=True)
        if GENERATED.exists():
            shutil.rmtree(GENERATED)
        subprocess.run(
            [flatc, "--python", "-o", str(GENERATED.parent), str(SCHEMA)],
            check=True,
        )
        return f"flatc ({flatc})"

    # No flatc on this machine: copy the bundle's BOOT bindings (the canonical
    # append-only prefix) and rewrite only the union enum, which contains the
    # HWVAL members the bootloader does not use.
    if not GENERATED.is_dir():
        GENERATED.mkdir(parents=True)
    for path in sorted((bundle / "hwlink").glob("*.py")):
        if path.name in ("Body.py", "__init__.py"):
            continue
        # The bundle is generated for a top-level ``hwlink`` package; the
        # station keeps it below ``hostcore.protocol.generated``.
        text = path.read_text(encoding="utf-8")
        text = text.replace("from hwlink.", "from hostcore.protocol.generated.hwlink.")
        text = text.replace("import hwlink.", "import hostcore.protocol.generated.hwlink.")
        (GENERATED / path.name).write_text(text, encoding="utf-8")
    members = _union_members(composed)
    lines = [
        "# automatically generated by the FlatBuffers compiler, do not modify",
        "",
        "# namespace: hwlink",
        "",
        "class Body(object):",
        "    NONE = 0",
    ]
    lines.extend(f"    {name} = {index}" for index, name in enumerate(members, 1))
    lines.append("")
    (GENERATED / "Body.py").write_text("\n".join(lines), encoding="utf-8")
    return (
        "bundle bindings + composed union enum (flatc not installed; "
        "HWVAL table bindings were kept as-is)"
    )


def record_bundle(bundle: Path, composed_hash: str) -> None:
    sums = bundle / "SHA256SUMS"
    payload = {
        "bundle_sha256": sha256(sums),
        "schema_sha256": composed_hash,
        "source": str(bundle),
    }
    BUNDLE_RECORD.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="existing package-hwlink directory")
    parser.add_argument("--bootloader", type=Path, default=DEFAULT_BOOTLOADER)
    parser.add_argument("--hwval", type=Path, default=DEFAULT_HWVAL)
    parser.add_argument(
        "--no-build", action="store_true", help="do not run make package-hwlink"
    )
    args = parser.parse_args(argv)

    try:
        if args.bundle is not None:
            bundle = args.bundle.resolve()
        else:
            bundle = args.bootloader.resolve() / "build" / "soccentric-hwlink"
            if not args.no_build:
                bundle = build_bundle(args.bootloader.resolve())
        check_bundle(bundle)
        composed = compose_schema(bundle, args.hwval.resolve())
        copy_vendor(bundle)
        SCHEMA.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA.write_text(composed, encoding="utf-8")
        mode = regenerate_generated(composed, bundle)
        record_bundle(bundle, sha256(SCHEMA))
    except (OSError, subprocess.CalledProcessError, UpdateError) as exc:
        print(f"update_hostkit: {exc}", file=sys.stderr)
        return 1

    print(f"update_hostkit: vendored {bundle}")
    print(f"update_hostkit: composed schema -> {SCHEMA}")
    print(f"update_hostkit: bindings: {mode}")
    print(f"update_hostkit: bundle record -> {BUNDLE_RECORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
