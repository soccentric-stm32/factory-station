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
import sys
import tarfile
import zipfile
from pathlib import Path

import yaml

from hwval_factory.profile import (
    generate_signing_key,
    load_profile,
    manifest_for_directory,
    sign_manifest,
)


def _build(args: argparse.Namespace) -> int:
    root = Path(args.directory)
    manifest_path = root / "manifest.yaml"
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
    print(
        f"OK: {profile.profile_id} v{profile.version} ({profile.product}), "
        f"{len(profile.images)} images, signature={'yes' if profile.signature else 'no'}"
    )
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
    build.set_defaults(func=_build)

    sign = sub.add_parser("sign")
    sign.add_argument("directory", type=Path)
    sign.add_argument("private_key", type=Path)
    sign.set_defaults(func=_sign)

    verify = sub.add_parser("verify")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--public-key", default="")
    verify.set_defaults(func=_verify)

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
