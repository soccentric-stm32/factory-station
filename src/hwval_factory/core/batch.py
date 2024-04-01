"""Bulk creation of unit records: serial numbers and MAC addresses in a range.

Pure functions with no Qt, so the GUI dialog and the tests share them.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from . import fileformat as ff
from .generators import CsvError, read_csv, valid_date

SYS_COLUMNS = ("id", "serial", "product", "hw_version", "fw_version",
               "batch", "mfg_date", "mac", "notes")


def _next_number(text: str) -> tuple[str, int, int]:
    """Split ``SC767-2611-00001`` into prefix, number, width of the last digit run."""
    match = re.search(r"(\d+)(\D*)$", text)
    if not match:
        raise CsvError(f"{text!r} has no number to count up")
    return text[:match.start(1)], int(match.group(1)), len(match.group(1))


def format_mac(value: int) -> str:
    return ":".join(f"{(value >> shift) & 0xFF:02X}" for shift in range(40, -8, -8))


def sys_batch(
    *, count: int, first_id: str, first_serial: str, product: str, hw_version: str,
    fw_version: str, batch: str, mfg_date: str, first_mac: str, notes: str = "",
) -> list[dict[str, str]]:
    """``count`` consecutive unit rows; ids, serials and MACs each count up by one."""
    if not 1 <= count <= 10_000:
        raise CsvError("count must be 1..10000")
    if not valid_date(mfg_date):
        raise CsvError(f"mfg_date {mfg_date!r} is not a calendar date (YYYY-MM-DD)")
    try:
        mac0 = int.from_bytes(ff.parse_mac(first_mac), "big")
    except ff.FileFormatError as exc:
        raise CsvError(str(exc)) from exc
    if mac0 + count - 1 >= 1 << 48:
        raise CsvError("the MAC range runs past FF:FF:FF:FF:FF:FF")
    id_prefix, id_n, id_w = _next_number(first_id)
    sn_prefix, sn_n, sn_w = _next_number(first_serial)
    rows = []
    for i in range(count):
        rows.append({
            "id": f"{id_prefix}{id_n + i:0{id_w}d}",
            "serial": f"{sn_prefix}{sn_n + i:0{sn_w}d}",
            "product": product, "hw_version": hw_version, "fw_version": fw_version,
            "batch": batch, "mfg_date": mfg_date, "mac": format_mac(mac0 + i),
            "notes": notes,
        })
    return rows


def append_rows(path: str | Path, rows: list[dict[str, str]]) -> int:
    """Append ``rows`` to a .sys CSV (created with a header when missing).

    Refuses ids that are already in the file. Returns the number written.
    """
    target = Path(path)
    existing: set[str] = set()
    columns = list(SYS_COLUMNS)
    needs_header = True
    needs_newline = False
    if target.exists() and target.stat().st_size:
        found, records = read_csv(target)
        if "id" not in found:
            raise CsvError(f"{target.name} has no id column")
        columns, needs_header = found, False
        existing = {r.get("id", "") for r in records}
        needs_newline = not target.read_bytes().endswith(b"\n")
    clash = sorted(r["id"] for r in rows if r["id"] in existing)
    if clash:
        shown = ", ".join(clash[:5]) + (" …" if len(clash) > 5 else "")
        raise CsvError(f"{len(clash)} id(s) already in {target.name}: {shown}")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    if needs_header:
        writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])
    with target.open("a", encoding="utf-8", newline="") as handle:
        if needs_newline:
            handle.write("\n")
        handle.write(out.getvalue())
    return len(rows)
