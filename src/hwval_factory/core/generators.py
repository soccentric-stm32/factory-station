"""CSV loaders and file generators for .sys, .cal and .diag.

One generator per file kind.  ``load`` reads a CSV into a :class:`Dataset`
(rows grouped by ``id``); ``build`` turns one id into the bytes of a file.

CSV layouts (UTF-8, header row required, ``#`` lines and blank lines skipped):

  sys    id,serial,product,hw_version,fw_version,batch,mfg_date,mac,notes[,uid]
         one row per unit.
  cal    id,channel,gain,offset[,temp_ref,date,operator,uid]
         one row per channel; the rows of an id make one .cal file.
  diag   id,type,code,severity,flags,text,counter,initial,depth,log_flags[,uid]
         type is fault, counter or log; the rows of an id make one .diag file.

For cal and diag an id of ``*`` marks rows shared by every unit.  An optional
``uid`` column binds the file to one chip (24 hex digits).
"""

from __future__ import annotations

import csv
import datetime
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import fileformat as ff
from . import spec

SHARED_ID = "*"


class CsvError(ValueError):
    """The CSV is missing, malformed, or a record cannot become a file."""


@dataclass(frozen=True)
class Issue:
    """One finding of :meth:`Generator.validate`.

    ``line`` is the line in the CSV file (0 for the file as a whole), and
    ``severity`` is ``error`` (the record cannot become a file) or ``warning``
    (the file is written, but check it).
    """

    severity: str
    ident: str
    line: int
    message: str


@dataclass
class Dataset:
    path: Path
    columns: list[str]
    rows: list[dict[str, str]]
    groups: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    lines: list[int] = field(default_factory=list)  # CSV line of each row

    @property
    def ids(self) -> list[str]:
        return [ident for ident in self.groups if ident != SHARED_ID]

    @property
    def shared(self) -> list[dict[str, str]]:
        return self.groups.get(SHARED_ID, [])

    def line_of(self, ident: str) -> int:
        """Line of the first row of ``ident`` (0 when unknown)."""
        for row, line in zip(self.rows, self.lines):
            if row.get("id") == ident:
                return line
        return 0


def read_csv_numbered(
    path: str | Path,
) -> tuple[list[str], list[dict[str, str]], list[int]]:
    """Columns, rows and the file line each row starts on.

    Blank lines and ``#`` lines are skipped but still counted, so the line
    numbers match what an editor shows.
    """
    target = Path(path)
    if not target.is_file():
        raise CsvError(f"{target} does not exist")
    try:
        with target.open(newline="", encoding="utf-8-sig") as handle:
            kept = [
                (number, line)
                for number, line in enumerate(handle, start=1)
                if line.strip() and not line.lstrip().startswith("#")
            ]
        reader = csv.reader(line for _, line in kept)
        columns: list[str] = []
        rows: list[dict[str, str]] = []
        numbers: list[int] = []
        consumed = 0
        for record in reader:
            first = kept[consumed][0]
            consumed = reader.line_num
            if not columns:
                columns = [name.strip() for name in record]
                continue
            values = [value.strip() for value in record]
            values += [""] * (len(columns) - len(values))
            rows.append(dict(zip(columns, values)))
            numbers.append(first)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        raise CsvError(f"cannot read {target}: {exc}") from exc
    return columns, rows, numbers


def read_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    columns, rows, _ = read_csv_numbered(path)
    return columns, rows


def to_int(text: str, what: str, low: int, high: int) -> int:
    try:
        value = int(text.strip(), 0)
    except ValueError as exc:
        raise CsvError(f"{what} {text!r} is not an integer") from exc
    if not low <= value <= high:
        raise CsvError(f"{what} {value} is out of range {low}..{high}")
    return value


def to_float(text: str, what: str) -> float:
    try:
        return float(text.strip())
    except ValueError as exc:
        raise CsvError(f"{what} {text!r} is not a number") from exc


def valid_date(text: str) -> bool:
    """True for a real ``YYYY-MM-DD`` calendar date."""
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def first_value(rows: list[dict[str, str]], name: str) -> str:
    for row in rows:
        if row.get(name):
            return row[name]
    return ""


class Generator:
    """Base: subclasses set ``kind`` and the column lists and implement ``make``."""

    kind = ""
    title = ""
    required: tuple[str, ...] = ("id",)
    columns: tuple[str, ...] = ()
    grouped = False  # several rows per id, and "*" rows are shared

    @property
    def suffix(self) -> str:
        return f".{self.kind}"

    @property
    def address(self) -> int:
        return spec.KIND_ADDRESS[self.kind]

    def load(self, path: str | Path) -> Dataset:
        columns, rows, lines = read_csv_numbered(path)
        missing = [name for name in self.required if name not in columns]
        if missing:
            raise CsvError(f"{Path(path).name} is missing columns: {', '.join(missing)}")
        groups: dict[str, list[dict[str, str]]] = {}
        first_line: dict[str, int] = {}
        for row, number in zip(rows, lines):
            ident = row.get("id", "")
            if not ident:
                raise CsvError(f"line {number}: the id is empty")
            if ident == SHARED_ID and not self.grouped:
                raise CsvError(
                    f"line {number}: shared rows (id *) only exist in .cal and .diag CSVs"
                )
            if ident in groups and not self.grouped:
                raise CsvError(
                    f"line {number}: duplicate id {ident!r} (first on line {first_line[ident]})"
                )
            first_line.setdefault(ident, number)
            groups.setdefault(ident, []).append(row)
        if not groups:
            raise CsvError(f"{Path(path).name} has no records")
        return Dataset(Path(path), columns, rows, groups, lines)

    # -- validation ---------------------------------------------------------

    def check_record(self, ident: str, rows: list[dict[str, str]]) -> list[str]:
        """Warnings for one record (subclasses add their own)."""
        return []

    def check_dataset(self, data: Dataset) -> list[Issue]:
        """Warnings that span records (subclasses add their own)."""
        return []

    def validate(
        self, data: Dataset, *, revision: int = 1, uid: bytes | None = None
    ) -> list[Issue]:
        """Every error and warning of the CSV, with the line it comes from.

        Each record is built the way Generate does it, so a record without an
        error here always becomes a valid file.
        """
        issues: list[Issue] = []
        known = {*self.columns, *self.required, "uid"}
        unknown = [name for name in data.columns if name and name not in known]
        if unknown:
            issues.append(
                Issue("warning", "", 1, f"unknown columns are ignored: {', '.join(unknown)}")
            )
        if not data.ids:
            issues.append(Issue("error", "", 0, "only shared rows (id *): there is no unit id"))
        names: dict[str, str] = {}
        for ident in data.ids:
            line = data.line_of(ident)
            name = self.filename(ident)
            if name in names:
                issues.append(
                    Issue(
                        "error", ident, line,
                        f"file name {name} is also used by {names[name]!r}",
                    )
                )
            names.setdefault(name, ident)
            try:
                blob = self.build(data, ident, revision=revision, uid=uid)
            except CsvError as exc:
                prefix = f"record {ident!r}: "
                text = str(exc)
                issues.append(
                    Issue("error", ident, line, text[len(prefix):] if text.startswith(prefix) else text)
                )
                continue
            if len(blob) > spec.MAX_FILE_SIZE * 9 // 10:
                issues.append(
                    Issue(
                        "warning", ident, line,
                        f"{len(blob)} of {spec.MAX_FILE_SIZE} bytes: close to the limit",
                    )
                )
            for text in self.check_record(ident, self.rows_for(data, ident)):
                issues.append(Issue("warning", ident, line, text))
        issues.extend(self.check_dataset(data))
        issues.sort(key=lambda issue: (issue.line, issue.severity != "error"))
        return issues

    def build_all(
        self, data: Dataset, *, revision: int = 1, uid: bytes | None = None
    ) -> tuple[dict[str, bytes], dict[str, str]]:
        """Build every record: the files by id, and the error text of the rest."""
        files: dict[str, bytes] = {}
        errors: dict[str, str] = {}
        for ident in data.ids:
            try:
                files[ident] = self.build(data, ident, revision=revision, uid=uid)
            except CsvError as exc:
                errors[ident] = str(exc)
        return files, errors

    def rows_for(self, data: Dataset, ident: str) -> list[dict[str, str]]:
        if ident not in data.groups or ident == SHARED_ID:
            raise CsvError(f"no record with id {ident!r}")
        shared = data.shared if self.grouped else []
        return [*shared, *data.groups[ident]]

    def build(
        self,
        data: Dataset,
        ident: str,
        *,
        revision: int = 1,
        uid: bytes | None = None,
    ) -> bytes:
        rows = self.rows_for(data, ident)
        if uid is None or not any(uid):
            text = first_value(rows, "uid")
            try:
                uid = ff.parse_uid(text) if text else None
            except ff.FileFormatError as exc:
                raise CsvError(f"record {ident!r}: {exc}") from exc
        try:
            return self.make(rows, revision, uid)
        except (ff.FileFormatError, CsvError) as exc:
            raise CsvError(f"record {ident!r}: {exc}") from exc

    def make(self, rows: list[dict[str, str]], revision: int, uid: bytes | None) -> bytes:
        raise NotImplementedError

    def sample(self) -> str:
        raise NotImplementedError

    def filename(self, ident: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ident).strip("._") or "file"
        return stem + self.suffix


class SysGenerator(Generator):
    kind = "sys"
    title = "System info"
    required = ("id", "serial")
    columns = (
        "id", "serial", "product", "hw_version", "fw_version",
        "batch", "mfg_date", "mac", "notes",
    )

    def make(self, rows: list[dict[str, str]], revision: int, uid: bytes | None) -> bytes:
        return ff.build_sys(rows[0], revision=revision, uid=uid)

    def check_record(self, ident: str, rows: list[dict[str, str]]) -> list[str]:
        row = rows[0]
        out: list[str] = []
        mac = row.get("mac", "")
        if mac and ff.parse_mac(mac)[0] & 1:
            out.append(f"MAC {mac} has the multicast bit set")
        if mac and not any(ff.parse_mac(mac)):
            out.append("MAC is all zeros")
        date = row.get("mfg_date", "")
        if date and not valid_date(date):
            out.append(f"mfg_date {date!r} is not a calendar date (YYYY-MM-DD)")
        elif date and datetime.date.fromisoformat(date) > datetime.date.today():
            out.append(f"mfg_date {date} is in the future")
        for column in ("product", "hw_version", "fw_version", "batch", "mac"):
            if not row.get(column):
                out.append(f"{column} is empty")
        return out

    def check_dataset(self, data: Dataset) -> list[Issue]:
        issues: list[Issue] = []
        for column, label in (("serial", "serial number"), ("mac", "MAC address")):
            seen: dict[str, str] = {}
            for row, line in zip(data.rows, data.lines):
                value = row.get(column, "").lower().replace("-", ":")
                if not value:
                    continue
                if value in seen:
                    issues.append(
                        Issue(
                            "warning", row.get("id", ""), line,
                            f"{label} {row[column]} is also used by {seen[value]!r}",
                        )
                    )
                seen.setdefault(value, row.get("id", ""))
        return issues

    def sample(self) -> str:
        # Two production batches of the SC767 controller, hardware rev B then C.
        batches = (
            ("2611A", "B", "0.1.0", "2026-10-26", 6, ""),
            ("2611B", "C", "0.1.1", "2026-11-02", 6, ""),
        )
        notes = {
            3: "Rework: U12 replaced",
            9: "Engineering sample",
            12: "Customer pilot unit",
        }
        lines = ["id,serial,product,hw_version,fw_version,batch,mfg_date,mac,notes"]
        n = 0
        for batch, hw, fw, date, count, _ in batches:
            for _ in range(count):
                n += 1
                lines.append(
                    f"SC{n:04d},SC767-2611-{n:05d},SC767,{hw},{fw},{batch},{date},"
                    f"02:00:00:00:00:{n:02X},{notes.get(n, '')}"
                )
        return "\n".join(lines) + "\n"


class CalGenerator(Generator):
    kind = "cal"
    title = "Calibration"
    required = ("id", "channel", "gain", "offset")
    columns = ("id", "channel", "gain", "offset", "temp_ref", "date", "operator")
    grouped = True

    def make(self, rows: list[dict[str, str]], revision: int, uid: bytes | None) -> bytes:
        channels: list[tuple[int, float, float]] = []
        seen: set[int] = set()
        for row in rows:
            if not row.get("channel"):
                continue
            channel = to_int(row["channel"], "channel", 0, 255)
            if channel in seen:
                raise CsvError(f"channel {channel} appears twice")
            seen.add(channel)
            gain = to_float(row.get("gain", ""), "gain")
            offset = to_float(row.get("offset", ""), "offset")
            if not (math.isfinite(gain) and math.isfinite(offset)):
                raise CsvError(f"channel {channel}: gain and offset must be finite numbers")
            channels.append((channel, gain, offset))
        if not channels:
            raise CsvError("no calibration channels")
        temp_ref = first_value(rows, "temp_ref")
        return ff.build_cal(
            channels,
            temp_ref=to_float(temp_ref, "temp_ref") if temp_ref else None,
            date=first_value(rows, "date"),
            operator=first_value(rows, "operator"),
            revision=revision,
            uid=uid,
        )

    GAIN_RANGE = (0.9, 1.1)
    OFFSET_LIMIT = 50.0
    TEMP_RANGE = (-40.0, 125.0)

    def check_record(self, ident: str, rows: list[dict[str, str]]) -> list[str]:
        out: list[str] = []
        channels: list[int] = []
        for row in rows:
            if not row.get("channel"):
                continue
            try:
                channel = int(row["channel"], 0)
                gain = float(row.get("gain", ""))
                offset = float(row.get("offset", ""))
            except ValueError:
                continue
            channels.append(channel)
            if gain == 0.0:
                out.append(f"channel {channel} has a gain of 0")
            elif not self.GAIN_RANGE[0] <= gain <= self.GAIN_RANGE[1]:
                out.append(f"channel {channel} gain {gain:g} is outside {self.GAIN_RANGE[0]}..{self.GAIN_RANGE[1]}")
            if abs(offset) > self.OFFSET_LIMIT:
                out.append(f"channel {channel} offset {offset:g} is larger than {self.OFFSET_LIMIT:g}")
        if channels and sorted(channels) != list(range(min(channels), max(channels) + 1)):
            out.append("channel numbers have gaps")
        temp = first_value(rows, "temp_ref")
        if not temp:
            out.append("temp_ref is empty")
        else:
            try:
                value = float(temp)
                if not self.TEMP_RANGE[0] <= value <= self.TEMP_RANGE[1]:
                    out.append(f"temp_ref {value:g} is outside {self.TEMP_RANGE[0]:g}..{self.TEMP_RANGE[1]:g} C")
            except ValueError:
                pass
        date = first_value(rows, "date")
        if not date:
            out.append("calibration date is empty")
        elif not valid_date(date):
            out.append(f"date {date!r} is not a calendar date (YYYY-MM-DD)")
        if not first_value(rows, "operator"):
            out.append("operator is empty")
        return out

    def check_dataset(self, data: Dataset) -> list[Issue]:
        counts = {
            ident: sum(1 for row in rows if row.get("channel"))
            for ident, rows in data.groups.items() if ident != SHARED_ID
        }
        if len(set(counts.values())) <= 1:
            return []
        common = max(set(counts.values()), key=list(counts.values()).count)
        return [
            Issue("warning", ident, data.line_of(ident),
                  f"{n} channels, most units have {common}")
            for ident, n in counts.items() if n != common
        ]

    def sample(self) -> str:
        # Eight analog input channels per unit, calibrated at 25 C.
        # Gain and offset are repeatable pseudo-random values, so the sample
        # is identical on every run.
        rng = random.Random(767)
        dates = ("2026-10-28", "2026-11-04")
        lines = ["id,channel,gain,offset,temp_ref,date,operator"]
        for n in range(1, 13):
            unit = f"SC{n:04d}"
            when = dates[0] if n <= 6 else dates[1]
            operator = "sandesh" if n % 3 else "line1"
            for channel in range(8):
                gain = 1.0 + rng.uniform(-0.004, 0.004)
                offset = rng.uniform(-2.0, 2.0)
                first = channel == 0
                lines.append(
                    f"{unit},{channel},{gain:.5f},{offset:.3f},"
                    f"{'25.0' if first else ''},"
                    f"{when if first else ''},"
                    f"{operator if first else ''}"
                )
        return "\n".join(lines) + "\n"


class DiagGenerator(Generator):
    kind = "diag"
    title = "Diagnostics"
    required = ("id", "type")
    columns = (
        "id", "type", "code", "severity", "flags", "text",
        "counter", "initial", "depth", "log_flags",
    )
    grouped = True

    def make(self, rows: list[dict[str, str]], revision: int, uid: bytes | None) -> bytes:
        faults: list[tuple[int, int, int, str]] = []
        counters: list[tuple[int, int]] = []
        log_depth, log_flags = 64, 0
        for row in rows:
            kind = row.get("type", "").lower()
            if kind == "fault":
                faults.append((
                    to_int(row.get("code", ""), "code", 0, 0xFFFF),
                    to_int(row.get("severity") or "0", "severity", 0, 0xFF),
                    to_int(row.get("flags") or "0", "flags", 0, 0xFF),
                    row.get("text", ""),
                ))
            elif kind == "counter":
                counters.append((
                    to_int(row.get("counter", ""), "counter", 0, 0xFFFF),
                    to_int(row.get("initial") or "0", "initial", 0, 0xFFFF_FFFF),
                ))
            elif kind == "log":
                log_depth = to_int(row.get("depth", ""), "depth", 0, 0xFFFF)
                log_flags = to_int(row.get("log_flags") or "0", "log_flags", 0, 0xFFFF)
            else:
                raise CsvError(f"type {row.get('type', '')!r} is not fault, counter or log")
        return ff.build_diag(
            faults, counters,
            log_depth=log_depth, log_flags=log_flags, revision=revision, uid=uid,
        )

    def check_record(self, ident: str, rows: list[dict[str, str]]) -> list[str]:
        out: list[str] = []
        seen_codes: set[int] = set()
        seen_counters: set[int] = set()
        for row in rows:
            kind = row.get("type", "").lower()
            try:
                if kind == "fault":
                    code = int(row.get("code", ""), 0)
                    if code in seen_codes:
                        out.append(f"fault {code:#06x} appears twice")
                    seen_codes.add(code)
                    if int(row.get("severity") or "0", 0) > 3:
                        out.append(f"fault {code:#06x} severity is above 3")
                    if int(row.get("flags") or "0", 0) & ~0x07:
                        out.append(f"fault {code:#06x} sets flag bits above 0x07")
                    if not row.get("text"):
                        out.append(f"fault {code:#06x} has no text")
                elif kind == "counter":
                    counter = int(row.get("counter", ""), 0)
                    if counter in seen_counters:
                        out.append(f"counter {counter} appears twice")
                    seen_counters.add(counter)
                elif kind == "log":
                    depth = int(row.get("depth", ""), 0)
                    if depth == 0 or depth & (depth - 1):
                        out.append(f"log depth {depth} is not a power of two")
            except ValueError:
                pass
        return out

    def sample(self) -> str:
        # Severity: 0 info, 1 warning, 2 error, 3 critical.
        # Flags: 0x01 latched, 0x02 written to the event log, 0x04 enters safe state.
        faults = (
            (0x0001, 3, 0x07, "Brown-out reset"),
            (0x0002, 2, 0x03, "Independent watchdog reset"),
            (0x0003, 2, 0x03, "Window watchdog reset"),
            (0x0004, 1, 0x02, "Software reset"),
            (0x0010, 3, 0x07, "HSE oscillator failure"),
            (0x0011, 2, 0x03, "PLL lock timeout"),
            (0x0020, 3, 0x07, "Flash ECC error"),
            (0x0021, 2, 0x03, "Identity block CRC mismatch"),
            (0x0022, 2, 0x03, "Calibration block CRC mismatch"),
            (0x0023, 1, 0x02, "SDRAM self-test failed"),
            (0x0030, 1, 0x02, "Ethernet link down"),
            (0x0031, 1, 0x02, "DHCP lease lost"),
            (0x0032, 2, 0x03, "UART framing error burst"),
            (0x0033, 2, 0x03, "CAN bus off"),
            (0x0040, 2, 0x03, "ADC reference out of range"),
            (0x0041, 1, 0x02, "ADC channel saturated"),
            (0x0042, 1, 0x02, "Sensor disconnected"),
            (0x0050, 2, 0x03, "Over-temperature warning"),
            (0x0051, 3, 0x07, "Over-temperature shutdown"),
            (0x0060, 1, 0x02, "Supply voltage low"),
            (0x0061, 2, 0x03, "Supply voltage high"),
        )
        counters = (
            (1, 0),   # boot count
            (2, 0),   # watchdog resets
            (3, 0),   # brown-outs
            (4, 0),   # communication errors
            (5, 0),   # calibration runs
        )
        lines = ["id,type,code,severity,flags,text,counter,initial,depth,log_flags"]
        for code, severity, flags, text in faults:
            lines.append(f"*,fault,0x{code:04X},{severity},0x{flags:02X},{text},,,,")
        for ident, initial in counters:
            lines.append(f"*,counter,,,,,{ident},{initial},,")
        lines.append("*,log,,,,,,,64,0")
        # Every unit has a burn-in record: counter 6 (power-on hours) starts at
        # the hours it ran on the burn-in rack, so every unit is its own record.
        for n in range(1, 13):
            lines.append(f"SC{n:04d},counter,,,,,6,{24 if n % 4 else 48},,")
        # Per-unit exceptions.
        lines += [
            "SC0003,counter,,,,,7,1,,",           # reworked unit: rework count
            "SC0009,log,,,,,,,128,0x0001",         # engineering sample logs deeper
            "SC0012,fault,0x0070,0,0x02,Pilot unit: field trial build,,,,",
        ]
        return "\n".join(lines) + "\n"


GENERATORS: dict[str, Generator] = {
    gen.kind: gen for gen in (SysGenerator(), CalGenerator(), DiagGenerator())
}


def detect_kind(columns: list[str]) -> str | None:
    """The file kind a CSV header belongs to, or None."""
    names = set(columns)
    for kind in ("diag", "cal", "sys"):
        if set(GENERATORS[kind].required) <= names:
            return kind
    return None
