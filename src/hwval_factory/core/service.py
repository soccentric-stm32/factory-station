"""The operations the GUI and the command line share, with no Qt.

Author: Sandesh Ghimire <sandesh@soccentric.com>

``check`` validates a CSV the way Generate builds it, then adds the product
rules and the repository-wide unit registry.  ``plan`` says which files
Generate All would create, change or leave alone.  ``write`` writes files,
reads each back and updates the manifest.  ``register`` records the units of
a generated .sys batch in ``units.csv``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import fileformat as ff
from .generators import GENERATORS, CsvError, Dataset, Generator, Issue
from .manifest import update_manifest
from .rules import Rules
from .units import Registry

NEW, CHANGED, SAME = "new", "changed", "unchanged"


def check(
    gen: Generator,
    data: Dataset,
    *,
    revision: int = 1,
    uid: bytes | None = None,
    rules: Rules | None = None,
    registry: Registry | None = None,
) -> list[Issue]:
    """Every error and warning: format, product rules and unit uniqueness."""
    issues = gen.validate(data, revision=revision, uid=uid)
    if rules:
        issues.extend(rules.check(gen, data))
    if registry is not None and gen.kind == "sys":
        records = [data.groups[ident][0] for ident in data.ids]
        issues.extend(registry.conflicts(records, data.line_of))
    issues.sort(key=lambda issue: (issue.line, issue.severity != "error"))
    return issues


@dataclass
class Plan:
    """What Generate All would do with a folder."""

    files: dict[str, bytes] = field(default_factory=dict)   # every buildable file
    errors: dict[str, str] = field(default_factory=dict)    # id -> why not
    state: dict[str, str] = field(default_factory=dict)     # name -> new/changed/unchanged
    owner: dict[str, str] = field(default_factory=dict)     # name -> id

    def names(self, *states: str) -> list[str]:
        return [name for name in self.files if self.state[name] in states]

    def to_write(self) -> dict[str, bytes]:
        """The files that are new or differ from what is on disk."""
        return {name: self.files[name] for name in self.names(NEW, CHANGED)}

    def summary(self) -> str:
        counts = {s: len(self.names(s)) for s in (NEW, CHANGED, SAME)}
        text = f"{counts[NEW]} new, {counts[CHANGED]} changed, {counts[SAME]} unchanged"
        if self.errors:
            text += f", {len(self.errors)} with errors"
        return text


def plan(
    gen: Generator,
    data: Dataset,
    folder: str | Path,
    *,
    revision: int = 1,
    uid: bytes | None = None,
    blocked: set[str] | None = None,
) -> Plan:
    """Build every record and compare it with the files already in ``folder``.

    ``blocked`` are record ids that must be skipped (rule or registry errors).
    """
    root = Path(folder)
    built, errors = gen.build_all(data, revision=revision, uid=uid)
    result = Plan(errors=dict(errors))
    for ident, blob in built.items():
        if blocked and ident in blocked:
            result.errors[ident] = f"record {ident!r}: blocked by a rule or the unit registry"
            continue
        name = gen.filename(ident)
        if name in result.files:
            result.errors[ident] = f"file name {name} is also used by {result.owner[name]!r}"
            continue
        result.files[name] = blob
        result.owner[name] = ident
        target = root / name
        if not target.is_file():
            result.state[name] = NEW
        else:
            try:
                same = target.read_bytes() == blob
            except OSError:
                same = False
            result.state[name] = SAME if same else CHANGED
    return result


def write(folder: str | Path, files: dict[str, bytes], kind: str, *, manifest: bool = True) -> tuple[list[str], list[str]]:
    """Write ``files``, read each back and compare.

    Returns ``(written, problems)``: the names that were written and verified,
    and a message for each that was not.  The manifest lists only the files
    that verified.
    """
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    good: dict[str, bytes] = {}
    problems: list[str] = []
    for name, blob in files.items():
        target = root / name
        try:
            target.write_bytes(blob)
            same = target.read_bytes() == blob
            ff.parse_file(blob, kind)
        except (OSError, ff.FileFormatError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        if not same:
            problems.append(f"{name}: read back differently from what was written")
            continue
        good[name] = blob
    if good and manifest:
        update_manifest(root, good)
    return list(good), problems


def register(
    registry: Registry,
    sys_data: Dataset,
    built: dict[str, bytes],
    *,
    product: str = "",
    batch: str = "",
) -> list[str]:
    """Record the generated units of a .sys CSV; return the serials changed.

    ``built`` maps record id to file bytes, so the chip UID of a bound file
    ends up in the registry too.
    """
    uids: dict[str, str] = {}
    for ident, blob in built.items():
        parsed = ff.parse_file(blob, "sys")
        if parsed.bound:
            uids[ident] = parsed.uid.hex().upper()
    records = [sys_data.groups[ident][0] for ident in built if ident in sys_data.groups]
    return registry.record_generated(records, product=product, batch=batch, uids=uids)


def load(kind: str, csv_path: str | Path) -> tuple[Generator, Dataset]:
    """The generator for ``kind`` and the loaded CSV."""
    if kind not in GENERATORS:
        raise CsvError(f"unknown kind {kind!r}; use sys, cal or diag")
    gen = GENERATORS[kind]
    return gen, gen.load(csv_path)
