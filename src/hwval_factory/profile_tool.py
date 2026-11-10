"""Engineer CLI for building, signing and verifying product profiles (PRF-003).

Examples::

    hwval-profile keygen keys/eng-private.pem keys/eng-public.txt
    hwval-profile build examples/profile-sc767-demo \
        --id sc767-demo --version 1.2.0 --product "SoCcentric demo board" \
        --hw-revisions B C --test hwval.elf --boot boot.hex --app demo.img
    hwval-profile sign examples/profile-sc767-demo keys/eng-private.pem
    hwval-profile verify examples/profile-sc767-demo --public-key keys/eng-public.txt
    hwval-profile archive examples/profile-sc767-demo dist/sc767-demo.zip
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import yaml

from hostcore import layout
from hostcore.images import key_id, load_key, public_point, sha256_file
from hwval_factory.profile import (
    _version_tuple,
    generate_signing_key,
    load_profile,
    manifest_for_directory,
    sign_manifest,
)


def _workspace_sources() -> dict[str, Path]:
    repo = Path(__file__).resolve().parents[2]
    workspace = repo.parent
    return {
        "test": workspace / "validation-firmware" / "build" / "hwval.elf",
        "boot": workspace / "bootloader" / "build" / "boot.hex",
        "app": workspace / "app-c-freertos" / "build" / "demo.img",
    }


def _build(args: argparse.Namespace) -> int:
    root = Path(args.directory)
    manifest_path = root / "manifest.yaml"
    if args.from_workspace:
        root.mkdir(parents=True, exist_ok=True)
        names = {
            "test": "hwval-1.0.0.elf",
            "boot": "boot-1.0.0.hex",
            "app": "app-0.1.0.img",
        }
        for role, source in _workspace_sources().items():
            if not source.is_file():
                print(f"error: workspace artefact not found: {source}", file=sys.stderr)
                return 2
            shutil.copy2(source, root / names[role])
        args.test = names["test"]
        args.boot = names["boot"]
        args.app = names["app"]
    images = {
        "test": args.test,
        "boot": args.boot,
        "app": args.app,
    }
    images = {role: name for role, name in images.items() if name}
    if not manifest_path.is_file() and set(images) != {"test", "boot", "app"}:
        print(
            "error: a new profile needs --test, --boot and --app image files",
            file=sys.stderr,
        )
        return 2
    if manifest_path.is_file():
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    else:
        raw = manifest_for_directory(
            root,
            profile_id=args.id,
            version=args.version,
            product=args.product,
            hw_revisions=args.hw_revisions,
            min_factory_version=args.min_factory_version,
            author=args.author,
            images=images,
        )
    if args.id:
        raw["profile"] = args.id
    if args.version:
        raw["version"] = args.version
    if args.product:
        raw["product"] = args.product
    if args.hw_revisions:
        raw["hw_revisions"] = args.hw_revisions
    if args.min_factory_version:
        raw["min_factory_version"] = args.min_factory_version
    raw.setdefault("released", {})["by"] = args.author
    from datetime import date

    raw["released"].setdefault("at", date.today().isoformat())
    images_raw = raw.setdefault("images", {})
    from hostcore.images import sha256_file

    for role, name in images.items():
        images_raw[role] = {"file": name, "sha256": sha256_file(root / name)}
    # Re-hash every image already in the manifest so a stale pin is caught here.
    for entry in images_raw.values():
        if not isinstance(entry, dict) or not entry.get("file"):
            continue
        target = root / str(entry["file"])
        if not target.is_file():
            print(f"error: missing image {target}", file=sys.stderr)
            return 2
        entry["sha256"] = sha256_file(target)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(manifest_path)
    return 0


def _sign(args: argparse.Namespace) -> int:
    print(sign_manifest(args.directory, args.private_key))
    return 0


def _previous_counter(profile) -> int:
    """Highest signed counter of an earlier released profile of this product."""
    highest = 0
    parent = profile.path.parent
    if not parent.is_dir():
        return 0
    for candidate in sorted(parent.iterdir()):
        if not candidate.is_dir() or candidate.resolve() == profile.path:
            continue
        try:
            previous = load_profile(candidate)
        except Exception:
            continue
        if previous.profile_id != profile.profile_id:
            continue
        if _version_tuple(previous.version) >= _version_tuple(profile.version):
            continue
        image = previous.app_image.image
        if image is not None and image.is_signed:
            highest = max(highest, int(previous.signing.security_counter))
    return highest


def _verify(args: argparse.Namespace) -> int:
    try:
        profile = load_profile(
            args.directory,
            require_signature=bool(args.public_key),
            public_key=args.public_key,
        )
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    app = profile.app_image.image
    boot = profile.boot_image.image
    if app is None or boot is None:
        print("INVALID: missing application or BOOT image", file=sys.stderr)
        return 1
    problems: list[str] = []
    key_point: bytes | None = None
    key = profile.signing_public_key_path
    actual_key_id = 0
    if key is not None:
        try:
            key_point = public_point(load_key(key))
            actual_key_id = key_id(key_point)
        except Exception as exc:
            problems.append(f"cannot read signing.public_key: {exc}")
    boot_has_key = bool(key_point is not None and key_point in boot.data)
    development = actual_key_id == layout.DEVELOPMENT_KEY_ID
    if profile.production:
        if not app.is_signed:
            problems.append("production profile has an unsigned (v1) application")
        if not boot_has_key:
            problems.append("the BOOT image does not contain the profile's public key")
        if development:
            problems.append("production profile uses the published development key")
    previous = _previous_counter(profile)
    if app.is_signed and previous and app.security_counter < previous:
        problems.append(
            f"security counter {app.security_counter} is below the previous released "
            f"profile's counter {previous}"
        )
    print(
        f"{profile.profile_id} v{profile.version} ({profile.product})\n"
        f"  production      : {'yes' if profile.production else 'no'}\n"
        f"  test mode       : {profile.test_mode}\n"
        f"  app header      : v{app.header_format} "
        f"{'signed' if app.is_signed else 'UNSIGNED'}\n"
        f"  security counter: {app.security_counter} (previous released {previous})\n"
        f"  key id          : 0x{actual_key_id:08x} "
        f"{'(development key)' if development else ''}\n"
        f"  BOOT trusts key : {'yes' if boot_has_key else 'no'}\n"
        f"  factory image   : "
        f"{profile.factory_image.file if profile.factory_image else 'not built'}"
    )
    if problems:
        for problem in problems:
            print(f"INVALID: {problem}", file=sys.stderr)
        return 1
    print(f"OK: {len(profile.images)} images verified")
    return 0


def _mkimage_path() -> Path:
    vendored = Path(__file__).resolve().parents[1] / "hostcore" / "vendor" / "mkimage.py"
    if vendored.is_file():
        return vendored
    return Path(__file__).resolve().parents[3] / "bootloader" / "tools" / "mkimage.py"


def _build_factory(args: argparse.Namespace) -> int:
    """Build one factory HEX with the bootloader's own tool (P1-04)."""
    root = Path(args.directory)
    mkimage = _mkimage_path()
    if not mkimage.is_file():
        print(f"error: mkimage.py not found ({mkimage})", file=sys.stderr)
        return 2
    output = Path(args.output) if args.output else root / "factory.hex"
    command = [
        sys.executable,
        str(mkimage),
        "--factory-hex",
        str(output),
        "--boot",
        str(args.boot),
        "--slot-a",
        str(args.app),
        "--seq-a",
        str(args.sequence_a),
        "--seq-b",
        str(args.sequence_b),
    ]
    if args.slot_b:
        command += ["--slot-b", str(args.slot_b)]
    if args.slot_b_unconfirmed:
        if not args.slot_b:
            print("error: --slot-b-unconfirmed needs --slot-b", file=sys.stderr)
            return 2
        command.append("--slot-b-unconfirmed")
    subprocess.run(command, check=True)
    manifest_path = root / "manifest.yaml"
    if manifest_path.is_file() and output.parent.resolve() == root.resolve():
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        images = raw.setdefault("images", {})
        images["factory"] = {
            "file": output.name if output.parent.resolve() == root.resolve() else str(output),
            "sha256": sha256_file(output),
        }
        manifest_path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        print(manifest_path)
    print(output)
    return 0


def _archive(args: argparse.Namespace) -> int:
    source = Path(args.directory)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    profile = load_profile(source)
    if target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(source))
    else:
        with tarfile.open(target, "w:gz") as archive:
            archive.add(source, arcname=profile.path.name)
    print(target)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    profile = load_profile(args.directory, require_signature=args.require_signature)
    print(yaml.safe_dump(profile.manifest, sort_keys=False))
    return 0


def _keygen(args: argparse.Namespace) -> int:
    generate_signing_key(args.private_key, args.public_key)
    print(f"private key: {args.private_key}")
    print(f"public key:  {args.public_key}")
    return 0


def _copy(args: argparse.Namespace) -> int:
    shutil.copy2(args.source, args.destination)
    print(args.destination)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hwval-profile", description="Product profile tool")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="pin image hashes and write manifest.yaml")
    build.add_argument("directory", type=Path)
    build.add_argument("--id", default="")
    build.add_argument("--version", default="")
    build.add_argument("--product", default="")
    build.add_argument("--hw-revisions", nargs="+", default=[])
    build.add_argument("--min-factory-version", default="0.1.0")
    build.add_argument("--author", default="")
    build.add_argument("--test", default="")
    build.add_argument("--boot", default="")
    build.add_argument("--app", default="")
    build.add_argument(
        "--from-workspace",
        action="store_true",
        help="copy the built BOOT, HWVAL and application images from sibling repos",
    )
    build.set_defaults(func=_build)

    sign = sub.add_parser("sign")
    sign.add_argument("directory", type=Path)
    sign.add_argument("private_key", type=Path)
    sign.set_defaults(func=_sign)

    verify = sub.add_parser("verify")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--public-key", default="")
    verify.set_defaults(func=_verify)

    factory = sub.add_parser(
        "build-factory", help="build the signed factory HEX with mkimage --factory-hex"
    )
    factory.add_argument("directory", type=Path)
    factory.add_argument("--boot", type=Path, required=True)
    factory.add_argument("--app", type=Path, required=True)
    factory.add_argument("--slot-b", type=Path, default=None)
    factory.add_argument("--slot-b-unconfirmed", action="store_true")
    factory.add_argument("--output", type=Path, default=None)
    factory.add_argument("--sequence-a", type=int, default=1)
    factory.add_argument("--sequence-b", type=int, default=2)
    factory.set_defaults(func=_build_factory)

    archive = sub.add_parser("archive")
    archive.add_argument("directory", type=Path)
    archive.add_argument("output", type=Path)
    archive.set_defaults(func=_archive)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("directory", type=Path)
    inspect.add_argument("--require-signature", action="store_true")
    inspect.set_defaults(func=_inspect)

    keygen = sub.add_parser("keygen")
    keygen.add_argument("private_key", type=Path)
    keygen.add_argument("public_key", type=Path)
    keygen.set_defaults(func=_keygen)

    copy = sub.add_parser("copy")
    copy.add_argument("source", type=Path)
    copy.add_argument("destination", type=Path)
    copy.set_defaults(func=_copy)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
