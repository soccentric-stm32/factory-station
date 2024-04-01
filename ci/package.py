#!/usr/bin/env python3
#
# package.py - build the versioned release bundle for Jenkins
#
# Author:  Sandesh Ghimire <sandesh@soccentric.com>
#
# Shared by every soccentric-stm32 repository (keep the copies identical).
# Collects the build outputs into dist/<name>-<version>.tar.gz with a
# manifest.json and SHA256SUMS inside, then writes dist/SHA256SUMS over every
# file in dist/.  The Release stage uploads dist/*.
#
# Usage:
#   ci/package.py --name bootloader --version 0.1.0 build/boot.elf build/boot.hex ...
#   ci/package.py --sums-only            only (re)write dist/SHA256SUMS
#

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DIST = Path("dist")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def walk(paths):
    """Yield (source file, path inside the bundle) for files and directories."""
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(p for p in path.rglob("*") if p.is_file()):
                yield child, Path(path.name) / child.relative_to(path)
        elif path.is_file():
            yield path, Path(path.name)
        else:
            sys.exit(f"package: {raw} does not exist")


def add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mtime: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = mtime
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def write_sums() -> None:
    files = sorted(p for p in DIST.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    lines = [f"{sha256(p)}  {p.name}\n" for p in files]
    (DIST / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    sys.stdout.write("".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the release bundle in dist/")
    parser.add_argument("--name", help="bundle name, normally the repository name")
    parser.add_argument("--version", help="version without the leading v")
    parser.add_argument("--sums-only", action="store_true", help="only write dist/SHA256SUMS")
    parser.add_argument("files", nargs="*", help="files or directories to bundle")
    args = parser.parse_args()

    DIST.mkdir(exist_ok=True)
    if args.sums_only:
        write_sums()
        return 0
    if not args.name or not args.version or not args.files:
        parser.error("--name, --version and at least one file are required")

    stem = f"{args.name}-{args.version}"
    mtime = int(os.environ.get("SOURCE_DATE_EPOCH") or time.time())
    entries = list(walk(args.files))
    manifest = {
        "name": args.name,
        "version": args.version,
        "commit": git("rev-parse", "HEAD"),
        "tag": os.environ.get("TAG_NAME", ""),
        "build": os.environ.get("BUILD_TAG", "local"),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)),
        "files": {str(inner): sha256(src) for src, inner in entries},
    }
    sums = "".join(f"{digest}  {inner}\n" for inner, digest in manifest["files"].items())

    bundle = DIST / f"{stem}.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        for src, inner in entries:
            info = tar.gettarinfo(str(src), arcname=f"{stem}/{inner}")
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with src.open("rb") as handle:
                tar.addfile(info, handle)
        add_bytes(tar, f"{stem}/manifest.json",
                  (json.dumps(manifest, indent=2) + "\n").encode(), mtime)
        add_bytes(tar, f"{stem}/SHA256SUMS", sums.encode(), mtime)

    print(f"package: {bundle} ({len(entries)} files, version {args.version})")
    write_sums()
    return 0


if __name__ == "__main__":
    sys.exit(main())
