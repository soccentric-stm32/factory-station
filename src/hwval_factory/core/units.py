"""The unit registry: one row per unit ever generated, kept in ``units.csv``.

Author: Sandesh Ghimire <sandesh@soccentric.com>

The registry is the repo-wide record of what exists and how far each unit
has got.  It answers "where is unit X" without opening a batch, and it is
what makes serial and MAC numbers unique across every product and batch, not
just inside the CSV that is open.

``units.csv`` is plain text, sorted by serial, so its diffs read well.  A
unit's status only moves forward (see :func:`can_move`); the factory station
and the CLI set ``programmed`` and ``verified``.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .generators import Issue

REGISTRY_NAME = "units.csv"
COLUMNS = (
    "serial", "id", "product", "hw_version", "batch", "mac", "uid", "status",
    "updated", "note",
)
STATUSES = ("planned", "generated", "programmed", "verified", "shipped", "rma", "scrapped")


class RegistryError(ValueError):
    """The registry cannot be read, or a status change is not allowed."""


def can_move(old: str, new: str) -> bool:
    """Whether a unit may go from status ``old`` to ``new``.

    Status moves forward along :data:`STATUSES`.  ``rma`` and ``scrapped`` can
    be entered from any state; a unit in ``rma`` can be reworked (back to
    ``generated`` or ``programmed``); ``scrapped`` is final.
    """
    if old not in STATUSES or new not in STATUSES:
        return False
    if old == new:
        return True
    if old == "scrapped":
        return False
    if new in ("rma", "scrapped"):
        return True
    if old == "rma":
        return new in ("generated", "programmed", "verified")
    return STATUSES.index(new) > STATUSES.index(old)


def normalise_mac(text: str) -> str:
    """``AA:BB:...`` upper-case, or the text unchanged when it is not a MAC."""
    cleaned = text.replace(":", "").replace("-", "").strip().upper()
    if len(cleaned) == 12 and all(c in "0123456789ABCDEF" for c in cleaned):
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    return text.strip()


def now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M")


@dataclass
class Unit:
    serial: str
    id: str = ""
    product: str = ""
    hw_version: str = ""
    batch: str = ""
    mac: str = ""
    uid: str = ""
    status: str = "generated"
    updated: str = ""
    note: str = ""

    def row(self) -> list[str]:
        return [str(getattr(self, name)) for name in COLUMNS]


class Registry:
    """The units of the repository, keyed by serial."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.units: dict[str, Unit] = {}

    # ---- storage ------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> Registry:
        registry = cls(path)
        if not registry.path.is_file():
            return registry
        try:
            with registry.path.open(newline="", encoding="utf-8-sig") as handle:
                for number, row in enumerate(csv.DictReader(handle), start=2):
                    serial = (row.get("serial") or "").strip()
                    if not serial:
                        raise RegistryError(f"{registry.path.name} line {number}: no serial")
                    if serial in registry.units:
                        raise RegistryError(
                            f"{registry.path.name} line {number}: duplicate serial {serial!r}"
                        )
                    values = {n: (row.get(n) or "").strip() for n in COLUMNS}
                    if values["status"] not in STATUSES:
                        raise RegistryError(
                            f"{registry.path.name} line {number}: "
                            f"unknown status {values['status']!r}"
                        )
                    registry.units[serial] = Unit(**values)
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            raise RegistryError(f"cannot read {registry.path}: {exc}") from exc
        return registry

    def text(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(COLUMNS)
        for serial in sorted(self.units):
            writer.writerow(self.units[serial].row())
        return out.getvalue()

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.text(), encoding="utf-8", newline="\n")
        return self.path

    # ---- lookup -------------------------------------------------------

    def find(self, text: str) -> list[Unit]:
        """Units whose serial, id, MAC, UID or batch contains ``text`` (any case)."""
        needle = text.strip().lower()
        if not needle:
            return list(self.units.values())
        compact = needle.replace(":", "").replace("-", "")
        return [
            unit
            for unit in self.units.values()
            if needle in unit.serial.lower()
            or needle in unit.id.lower()
            or needle in unit.batch.lower()
            or needle in unit.product.lower()
            or compact in unit.mac.replace(":", "").lower()
            or (compact and compact in unit.uid.lower())
        ]

    def by_mac(self, mac: str) -> Unit | None:
        target = normalise_mac(mac)
        for unit in self.units.values():
            if unit.mac and unit.mac == target:
                return unit
        return None

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(STATUSES, 0)
        for unit in self.units.values():
            out[unit.status] += 1
        return out

    # ---- changes ------------------------------------------------------

    def set_status(self, serial: str, status: str, note: str = "", *, now: str | None = None) -> Unit:
        unit = self.units.get(serial)
        if unit is None:
            raise RegistryError(f"no unit with serial {serial!r}")
        if status not in STATUSES:
            raise RegistryError(f"unknown status {status!r}; use one of {', '.join(STATUSES)}")
        if not can_move(unit.status, status):
            raise RegistryError(f"{serial}: cannot go from {unit.status} to {status}")
        unit.status = status
        unit.updated = now or now_text()
        if note:
            unit.note = note
        return unit

    def record_generated(
        self,
        records: list[dict[str, str]],
        *,
        product: str = "",
        batch: str = "",
        uids: dict[str, str] | None = None,
        now: str | None = None,
    ) -> list[str]:
        """Add or refresh the units of a .sys CSV that was just generated.

        ``records`` are the rows of the .sys CSV.  A new unit starts at
        ``generated``; a unit that already went further keeps its status.  A
        record whose serial belongs to a different unit id is refused, not
        overwritten.  Returns the serials that were added or changed.
        """
        changed: list[str] = []
        stamp = now or now_text()
        for row in records:
            serial = (row.get("serial") or "").strip()
            if not serial:
                continue
            ident = (row.get("id") or "").strip()
            existing = self.units.get(serial)
            if existing is not None and existing.id and existing.id != ident:
                raise RegistryError(
                    f"serial {serial} is already unit {existing.id!r} "
                    f"({existing.product or 'no product'}, batch {existing.batch or '-'})"
                )
            fresh = Unit(
                serial=serial,
                id=ident,
                product=(row.get("product") or product).strip(),
                hw_version=(row.get("hw_version") or "").strip(),
                batch=(row.get("batch") or batch).strip(),
                mac=normalise_mac(row.get("mac") or ""),
                uid=((uids or {}).get(ident) or (row.get("uid") or "")).strip().upper(),
                status="generated",
                updated=stamp,
            )
            if existing is None:
                self.units[serial] = fresh
                changed.append(serial)
                continue
            fresh.status = existing.status if existing.status != "planned" else "generated"
            fresh.note = existing.note
            if existing.row()[:7] != fresh.row()[:7] or existing.status != fresh.status:
                self.units[serial] = fresh
                changed.append(serial)
        return changed

    # ---- uniqueness ---------------------------------------------------

    def conflicts(self, records: list[dict[str, str]], line_of=lambda _ident: 0) -> list[Issue]:
        """Errors for records that reuse a serial or MAC of another unit.

        A serial may only appear once in the repository, under one unit id.
        A MAC may only belong to one serial.  Re-generating the same unit is
        fine.
        """
        issues: list[Issue] = []
        for row in records:
            serial = (row.get("serial") or "").strip()
            ident = (row.get("id") or "").strip()
            mac = normalise_mac(row.get("mac") or "")
            line = line_of(ident)
            known = self.units.get(serial)
            if known is not None and known.id and known.id != ident:
                issues.append(
                    Issue(
                        "error", ident, line,
                        f"serial {serial} is already unit {known.id!r} "
                        f"in batch {known.batch or '-'} ({known.status}); serials are never reused",
                    )
                )
            if mac:
                owner = self.by_mac(mac)
                if owner is not None and owner.serial != serial:
                    issues.append(
                        Issue(
                            "error", ident, line,
                            f"MAC {mac} already belongs to serial {owner.serial} "
                            f"(batch {owner.batch or '-'})",
                        )
                    )
        return issues

    def next_free(self, prefix: str, digits: int = 4) -> str:
        """The next unused serial ``<prefix><number>`` after the highest used."""
        highest = 0
        for serial in self.units:
            if serial.startswith(prefix):
                tail = serial[len(prefix) :]
                if tail.isdigit():
                    highest = max(highest, int(tail))
        return f"{prefix}{highest + 1:0{digits}d}"
