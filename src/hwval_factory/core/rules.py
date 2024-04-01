"""Per-product rules, kept in ``rules.csv`` next to the product's batches.

Author: Sandesh Ghimire <sandesh@soccentric.com>

The validation in :mod:`core.generators` only knows what is legal in the file
format.  The rules say what is right for one product: which hardware and
firmware versions exist, how serial numbers look, how many calibration
channels a unit has and what a believable gain is.  A record that breaks a
rule is an error, so it never becomes a file.

``rules.csv`` is ``key,value`` rows (``#`` lines and blank lines skipped):

  product          the only product name accepted in .sys
  hw_versions      allowed hw_version values, separated by |
  fw_versions      allowed fw_version values, separated by |
  serial_pattern   regular expression the whole serial must match
  mac_prefix       the MAC must start with this (e.g. 02:00:5E)
  channels         exact number of calibration channels per unit
  gain_min         lowest accepted calibration gain
  gain_max         highest accepted calibration gain
  offset_abs_max   largest accepted offset, in either direction
  temp_ref_min     lowest accepted temperature reference (degrees C)
  temp_ref_max     highest accepted temperature reference
  log_depth_max    largest accepted diagnostics log depth
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .generators import CsvError, Dataset, Generator, Issue, first_value

RULES_NAME = "rules.csv"
KEYS = (
    "product", "hw_versions", "fw_versions", "serial_pattern", "mac_prefix",
    "channels", "gain_min", "gain_max", "offset_abs_max", "temp_ref_min",
    "temp_ref_max", "log_depth_max",
)

SAMPLE = """\
# Rules of one product. Every key is optional; a missing key is not checked.
key,value
product,SC767
hw_versions,A|B|C
fw_versions,0.1.0|0.1.1|1.0.0
serial_pattern,SC767-[0-9]{4}-[0-9]{5}
mac_prefix,02:00
channels,8
gain_min,0.9
gain_max,1.1
offset_abs_max,50
temp_ref_min,-40
temp_ref_max,125
log_depth_max,512
"""


class RulesError(ValueError):
    """``rules.csv`` cannot be read or has an invalid value."""


@dataclass
class Rules:
    values: dict[str, str] = field(default_factory=dict)
    path: Path | None = None

    def __bool__(self) -> bool:
        return bool(self.values)

    @classmethod
    def load(cls, path: str | Path) -> Rules:
        target = Path(path)
        if not target.is_file():
            return cls({}, target)
        values: dict[str, str] = {}
        try:
            with target.open(newline="", encoding="utf-8-sig") as handle:
                lines = [ln for ln in handle if ln.strip() and not ln.lstrip().startswith("#")]
            for number, row in enumerate(csv.reader(lines)):
                if number == 0:
                    continue  # header
                if len(row) < 2 or not row[0].strip():
                    raise RulesError(f"{target.name}: row {number + 1} needs key,value")
                key = row[0].strip()
                if key not in KEYS:
                    raise RulesError(f"{target.name}: unknown rule {key!r}")
                values[key] = row[1].strip()
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            raise RulesError(f"cannot read {target}: {exc}") from exc
        rules = cls(values, target)
        rules._compile()   # fail now, not in the middle of a validation
        return rules

    def _compile(self) -> None:
        pattern = self.values.get("serial_pattern")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RulesError(f"serial_pattern {pattern!r}: {exc}") from exc
        for key in ("channels", "gain_min", "gain_max", "offset_abs_max",
                    "temp_ref_min", "temp_ref_max", "log_depth_max"):
            if key in self.values:
                try:
                    float(self.values[key])
                except ValueError:
                    raise RulesError(f"{key} {self.values[key]!r} is not a number") from None

    def number(self, key: str) -> float | None:
        return float(self.values[key]) if key in self.values else None

    def choices(self, key: str) -> list[str]:
        return [c.strip() for c in self.values.get(key, "").split("|") if c.strip()]

    # ---- checks ---------------------------------------------------------

    def check(self, gen: Generator, data: Dataset) -> list[Issue]:
        """Errors of every record of ``data`` against these rules."""
        if not self.values:
            return []
        check = {"sys": self._sys, "cal": self._cal, "diag": self._diag}[gen.kind]
        issues: list[Issue] = []
        for ident in data.ids:
            line = data.line_of(ident)
            try:
                rows = gen.rows_for(data, ident)
            except CsvError:
                continue
            for text in check(rows):
                issues.append(Issue("error", ident, line, f"rule: {text}"))
        issues.sort(key=lambda issue: (issue.line, issue.ident))
        return issues

    def _sys(self, rows: list[dict[str, str]]) -> list[str]:
        out: list[str] = []
        row = rows[0]
        product = self.values.get("product")
        if product and (row.get("product") or "").strip() != product:
            out.append(f"product must be {product!r}, not {(row.get('product') or '').strip()!r}")
        for key, column in (("hw_versions", "hw_version"), ("fw_versions", "fw_version")):
            allowed = self.choices(key)
            value = (row.get(column) or "").strip()
            if allowed and value not in allowed:
                out.append(f"{column} {value!r} is not one of {', '.join(allowed)}")
        pattern = self.values.get("serial_pattern")
        serial = (row.get("serial") or "").strip()
        if pattern and not re.fullmatch(pattern, serial):
            out.append(f"serial {serial!r} does not match {pattern}")
        prefix = self.values.get("mac_prefix")
        mac = (row.get("mac") or "").replace("-", ":").upper().strip()
        if prefix and mac and not mac.startswith(prefix.replace("-", ":").upper()):
            out.append(f"MAC {mac} does not start with {prefix}")
        return out

    def _cal(self, rows: list[dict[str, str]]) -> list[str]:
        out: list[str] = []
        channels = {r.get("channel", "") for r in rows if (r.get("channel") or "").strip()}
        want = self.number("channels")
        if want is not None and len(channels) != int(want):
            out.append(f"{len(channels)} calibration channels, the product has {int(want)}")
        low, high = self.number("gain_min"), self.number("gain_max")
        limit = self.number("offset_abs_max")
        for row in rows:
            name = (row.get("channel") or "").strip()
            if not name:
                continue
            for column, check in (
                ("gain", lambda v: (low is not None and v < low) or (high is not None and v > high)),
                ("offset", lambda v: limit is not None and abs(v) > limit),
            ):
                try:
                    value = float(row.get(column) or "")
                except ValueError:
                    continue   # the generator already reports a bad number
                if check(value):
                    span = f"{low:g}..{high:g}" if column == "gain" and low is not None and high is not None else (
                        f"+-{limit:g}" if column == "offset" and limit is not None else "the allowed range"
                    )
                    out.append(f"channel {name}: {column} {value:g} is outside {span}")
        try:
            ref = float(first_value(rows, "temp_ref") or "")
        except ValueError:
            return out
        lo, hi = self.number("temp_ref_min"), self.number("temp_ref_max")
        if (lo is not None and ref < lo) or (hi is not None and ref > hi):
            out.append(f"temperature reference {ref:g} is outside {lo:g}..{hi:g}")
        return out

    def _diag(self, rows: list[dict[str, str]]) -> list[str]:
        out: list[str] = []
        top = self.number("log_depth_max")
        if top is None:
            return out
        for row in rows:
            if (row.get("type") or "").strip() != "log":
                continue
            try:
                depth = int(row.get("depth") or "0", 0)
            except ValueError:
                continue
            if depth > top:
                out.append(f"log depth {depth} is above the maximum {int(top)}")
        return out
