"""Manifest of a folder of generated files, and the check against it.

``manifest.csv`` lists every file Generate All wrote with its size and
hashes.  :func:`verify_folder` re-reads the folder, checks every file's header
and CRCs, and, when a manifest is present, that nothing was changed, removed
or left over.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from . import fileformat as ff

MANIFEST_NAME = "manifest.csv"
SUFFIXES = (".sys", ".cal", ".diag")
COLUMNS = ("file", "kind", "revision", "uid", "size", "crc32", "sha256")


@dataclass(frozen=True)
class Verdict:
    name: str
    ok: bool
    detail: str


def _row(name: str, blob: bytes) -> list[str]:
    parsed = ff.parse_file(blob)
    return [
        name,
        parsed.kind,
        str(parsed.revision),
        parsed.uid.hex().upper() if parsed.bound else "",
        str(len(blob)),
        f"{ff.crc32(blob):08x}",
        hashlib.sha256(blob).hexdigest(),
    ]


def manifest_csv(files: dict[str, bytes]) -> str:
    """``manifest.csv`` text for ``{file name: bytes}``, sorted by name."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for name in sorted(files):
        writer.writerow(_row(name, files[name]))
    return out.getvalue()


def update_manifest(folder: str | Path, files: dict[str, bytes]) -> Path:
    """Add ``files`` to the folder's manifest, keeping the other entries as they are.

    Entries of files that no longer exist are dropped; entries of untouched
    files stay verbatim, so a file changed behind the tool's back still fails
    :func:`verify_folder`.
    """
    root = Path(folder)
    path = root / MANIFEST_NAME
    rows: dict[str, list[str]] = {}
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                name = row.get("file", "")
                if name and name not in files and (root / name).is_file():
                    rows[name] = [row.get(column, "") for column in COLUMNS]
    for name, blob in files.items():
        rows[name] = _row(name, blob)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for name in sorted(rows):
        writer.writerow(rows[name])
    path.write_text(out.getvalue(), encoding="utf-8")
    return path


def verify_folder(folder: str | Path) -> list[Verdict]:
    """Check every .sys, .cal and .diag file in ``folder``."""
    root = Path(folder)
    verdicts: list[Verdict] = []
    found = sorted(p for p in root.iterdir() if p.suffix in SUFFIXES and p.is_file())
    manifest: dict[str, dict[str, str]] = {}
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_file():
        with manifest_path.open(newline="", encoding="utf-8") as handle:
            manifest = {row["file"]: row for row in csv.DictReader(handle) if row.get("file")}
    for path in found:
        try:
            blob = path.read_bytes()
            parsed = ff.parse_file(blob, path.suffix[1:])
        except (OSError, ff.FileFormatError) as exc:
            verdicts.append(Verdict(path.name, False, str(exc)))
            continue
        entry = manifest.get(path.name)
        if manifest and entry is None:
            verdicts.append(Verdict(path.name, False, "not in the manifest"))
        elif entry and entry.get("sha256") != hashlib.sha256(blob).hexdigest():
            verdicts.append(Verdict(path.name, False, "changed since it was generated"))
        else:
            bound = "bound" if parsed.bound else "unbound"
            verdicts.append(
                Verdict(path.name, True, f"rev {parsed.revision}, {len(blob)} bytes, {bound}")
            )
    present = {p.name for p in found}
    for name in manifest:
        if name not in present:
            verdicts.append(Verdict(name, False, "listed in the manifest but missing"))
    return verdicts
