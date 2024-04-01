"""Compatibility view of the factory .sys CSV for the old bench pages.

The CSV parsing and file generation now live in :mod:`hwval_factory.core`;
this class only keeps the small API the legacy pages used.  New pages should
load a CSV through ``core.generators`` and a ``core.workspace.Workspace``.
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from hwval_factory.bench import files
from hwval_factory.core import generators


class FactoryDbError(RuntimeError):
    """The CSV is missing, malformed or the record cannot be used."""


class FactoryCsv:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.rows: list[dict[str, str]] = []
        self.columns: list[str] = []
        self.reload()

    def reload(self) -> None:
        try:
            columns, rows, _lines = generators.read_csv_numbered(self.path)
        except generators.CsvError as exc:
            raise FactoryDbError(str(exc)) from exc
        self.columns = list(columns)
        self.rows = rows
        missing = [name for name in ("id", "serial") if name not in self.columns]
        if missing:
            raise FactoryDbError(f"{self.path.name} is missing columns: {', '.join(missing)}")
        seen: set[str] = set()
        for row in self.rows:
            ident = row.get("id", "")
            if not ident:
                raise FactoryDbError("a row has an empty id")
            if ident in seen:
                raise FactoryDbError(f"duplicate id {ident!r}")
            seen.add(ident)

    def get(self, ident: str) -> dict[str, str] | None:
        wanted = ident.strip().lower()
        for row in self.rows:
            if row["id"].lower() == wanted:
                return row
        return None

    def search(self, text: str) -> list[dict[str, str]]:
        """Rows whose id starts with, or whose serial contains, the text."""
        needle = text.strip().lower()
        if not needle:
            return list(self.rows)
        return [
            row
            for row in self.rows
            if row["id"].lower().startswith(needle) or needle in row.get("serial", "").lower()
        ]

    def sys_file(self, ident: str, uid: bytes | None = None) -> bytes:
        row = self.get(ident)
        if row is None:
            raise FactoryDbError(f"no record with id {ident!r}")
        try:
            return files.build_sys(row, uid=uid)
        except (files.FileFormatError, UnicodeEncodeError) as exc:
            raise FactoryDbError(f"record {ident!r} cannot become a .sys file: {exc}") from exc

    def mark_programmed(self, ident: str) -> None:
        row = self.get(ident)
        if row is None:
            raise FactoryDbError(f"no record with id {ident!r}")
        if "status" not in self.columns:
            self.columns.append("status")
        row["status"] = "programmed"
        self._save()

    def _save(self) -> None:
        handle, temp_name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", newline="", encoding="utf-8") as out:
                writer = csv.DictWriter(out, fieldnames=self.columns, extrasaction="ignore")
                writer.writeheader()
                for row in self.rows:
                    writer.writerow({name: row.get(name, "") for name in self.columns})
            os.replace(temp_name, self.path)
        except OSError as exc:
            Path(temp_name).unlink(missing_ok=True)
            raise FactoryDbError(f"cannot save {self.path}: {exc}") from exc


def write_sample(path: str | Path, count: int = 8) -> Path:
    """Create a sample database so a new station has something to search."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = generators.GENERATORS["sys"].sample().splitlines()
    if count > 0 and len(lines) > count + 1:
        lines = lines[: count + 1]
    header, *rows = lines
    if "status" not in header.split(","):
        header += ",status"
        rows = [row + ",new" for row in rows]
    target.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return target
