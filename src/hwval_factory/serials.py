"""Serial-number and MAC allocation (SN-*, MAC-*).

Allocation is atomic in SQLite (``BEGIN IMMEDIATE``) and shared by every
fixture worker, so the same serial cannot be handed out twice across
fixtures, threads or restarts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hwval_factory.database import Database
from hwval_factory.models import WorkOrder
from hwval_factory.profile import ProductProfile, ProfileError

_PLACEHOLDER = re.compile(r"\{(\w+)(?::([^}]+))?\}")


def luhn_digit(payload: str) -> str:
    digits = [int(ch) for ch in payload if ch.isdigit()]
    total = 0
    parity = (len(digits) + 1) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def luhn_valid(serial: str) -> bool:
    digits = [int(ch) for ch in serial if ch.isdigit()]
    if len(digits) < 2:
        return False
    payload, check = digits[:-1], digits[-1]
    return luhn_digit("".join(str(d) for d in payload)) == str(check)


def iso_week(now: datetime | None = None) -> tuple[str, str]:
    moment = now or datetime.now(UTC)
    year, week, _ = moment.isocalendar()
    return str(year)[-2:], f"{week:02d}"


def render_serial(
    template: str,
    *,
    counter: int,
    product_code: str = "",
    prefix: str = "",
    site: str = "",
    line: str = "",
    rev: str = "",
    now: datetime | None = None,
) -> str:
    yy, ww = iso_week(now)
    values: dict[str, Any] = {
        "counter": counter,
        "serial": counter,
        "n": counter,
        "yy": yy,
        "yyyy": (now or datetime.now(UTC)).year,
        "ww": ww,
        "yyww": f"{yy}{ww}",
        "product": product_code,
        "prefix": prefix,
        "product_code": product_code,
        "site": site,
        "line": line,
        "rev": rev,
    }
    marker = "\x00LUHN\x00"
    text = template.replace("{luhn}", marker)
    result = _PLACEHOLDER.sub(lambda m: _format_field(m, values), text)
    if marker in result:
        result = result.replace(marker, luhn_digit(result))
    return result


def _format_field(match: re.Match[str], values: dict[str, Any]) -> str:
    name, spec = match.group(1), match.group(2)
    if name not in values:
        raise ProfileError(f"unknown serial placeholder {{{name}}}")
    value = values[name]
    if spec:
        try:
            return format(value, spec)
        except (ValueError, TypeError) as exc:
            raise ProfileError(f"invalid serial format specifier {spec!r}") from exc
    return str(value)


def serial_range(
    wo: WorkOrder,
    profile: ProductProfile,
    *,
    now: datetime | None = None,
) -> list[str]:
    template = profile.identity.serial_format
    start = max(0, wo.serial_start)
    end = wo.serial_end if wo.serial_end >= start else start + max(0, wo.quantity) - 1
    if end < start:
        return []
    product_code = profile.profile_id
    identity_raw = profile.manifest.get("identity") or {}
    identity = identity_raw if isinstance(identity_raw, dict) else {}
    site = str(identity.get("site") or "")
    line = str(identity.get("line") or "")
    rev = str(identity.get("hw_revision") or "")
    values: list[str] = []
    for counter in range(start, end + 1):
        serial = render_serial(
            template,
            counter=counter,
            product_code=product_code,
            prefix=wo.serial_prefix,
            site=site,
            line=line,
            rev=rev,
            now=now,
        )
        values.append(serial)
    if len(values) != len(set(values)):
        raise ProfileError("serial format produces duplicate numbers for this range")
    return values


def parse_mac(text: str) -> int:
    clean = text.replace("-", ":").replace(".", "")
    if ":" in clean:
        parts = clean.split(":")
        if len(parts) != 6:
            raise ProfileError(f"invalid MAC address {text!r}")
        return int("".join(f"{int(p, 16):02x}" for p in parts), 16)
    return int(clean, 16)


def format_mac(value: int) -> str:
    return ":".join(f"{(value >> shift) & 0xFF:02X}" for shift in (40, 32, 24, 16, 8, 0))


def parse_oui_block(block: str) -> tuple[int, int, int]:
    """Return ``(prefix, mask, capacity)`` for e.g. ``02:00:00:00:00:00/24``."""
    if not block.strip():
        raise ProfileError("MAC OUI block is empty")
    base, _, length = block.partition("/")
    prefix_len = int(length) if length else 48
    if not 0 <= prefix_len <= 48:
        raise ProfileError("MAC prefix length must be 0-48")
    value = parse_mac(base.strip())
    mask = ((1 << prefix_len) - 1) << (48 - prefix_len) if prefix_len else 0
    prefix = value & mask
    capacity = 1 << (48 - prefix_len)
    return prefix, mask, capacity


def mac_range(profile: ProductProfile, wo: WorkOrder) -> list[str]:
    spec = profile.identity
    if not spec.mac_enabled:
        return []
    if spec.mac_source in ("import", "local"):
        # `import`: the pool is loaded from a CSV with `--import-macs`.
        # `local`: the MAC is derived from the chip UID at allocation time.
        return []
    prefix, mask, capacity = parse_oui_block(spec.oui_block)
    if wo.mac_start:
        first = parse_mac(wo.mac_start)
        prefix = first & mask
    else:
        first = prefix
    count = wo.mac_count or min(capacity, 10000)
    macs: list[str] = []
    value = first
    while len(macs) < count and value < prefix + capacity:
        if (spec.allow_local or not (value & 0x20000000000)) and not (
            value & 0x10000000000
        ):  # never multicast
            macs.append(format_mac(value))
        value += 1
    if not macs:
        raise ProfileError(
            "MAC pool is empty: OUI block does not allow any unicast address "
            "under the profile rules"
        )
    return macs


@dataclass(slots=True)
class Allocation:
    serial_number: str
    mac: str = ""


def locally_administered_mac(uid_hex: str, salt: int = 0) -> str:
    """Derive a stable, unicast, locally administered MAC from the chip UID."""
    raw_uid = bytes.fromhex(uid_hex)
    digest = hashlib.sha256(raw_uid + salt.to_bytes(4, "little")).digest()
    value = bytearray(digest[:6])
    value[0] = (value[0] & 0xFC) | 0x02  # unicast + locally administered
    return format_mac(int.from_bytes(value, "big"))


class SerialService:
    def __init__(self, db: Database, central: Any = None) -> None:
        self.db = db
        self.central = central
        self.station_id = str(getattr(central, "station_id", "") or "")

    def prepare_work_order(self, wo: WorkOrder, profile: ProductProfile) -> dict[str, int]:
        self.db.save_work_order(
            wo.name,
            profile.profile_id,
            quantity=wo.quantity,
            serial_prefix=wo.serial_prefix,
            serial_start=wo.serial_start,
            serial_end=wo.serial_end,
            mac_start=wo.mac_start,
            mac_count=wo.mac_count,
        )
        serials = serial_range(wo, profile)
        if serials:
            existing = self.db.count_serials(wo.name)
            self.db.add_serials(wo.name, serials)
            loaded = self.db.count_serials(wo.name)
            if loaded < len(serials) and loaded <= existing:
                raise ProfileError(
                    f"serial range for {wo.name} overlaps serial numbers already "
                    "allocated to another work order"
                )
        if self.central is not None:
            self.central.load_serials(wo.name, serials)
            self.central.load_macs(wo.name, mac_range(profile, wo))
        macs = mac_range(profile, wo)
        if macs:
            existing_macs = self.db.scalar(
                "SELECT COUNT(*) FROM mac_pool WHERE work_order = ?", (wo.name,)
            )
            self.db.add_macs(wo.name, macs)
            loaded_macs = int(
                self.db.scalar("SELECT COUNT(*) FROM mac_pool WHERE work_order = ?", (wo.name,))
                or 0
            )
            if loaded_macs < len(macs) and loaded_macs <= int(existing_macs or 0):
                raise ProfileError(
                    f"MAC range for {wo.name} overlaps addresses already allocated "
                    "to another work order"
                )
        return self.usage(wo.name)

    def usage(self, work_order: str) -> dict[str, Any]:
        if self.central is not None:
            counts: dict[str, Any] = dict(self.central.usage(work_order))
        else:
            counts = self.db.serial_counts(work_order)
        if counts["TOTAL"]:
            counts["free_percent"] = round(counts["FREE"] * 100 / counts["TOTAL"], 1)
        else:
            counts["free_percent"] = 0.0
        return counts

    def warning(self, work_order: str) -> str:
        counts = self.usage(work_order)
        if counts["TOTAL"] == 0:
            return "no serial numbers loaded"
        if counts["FREE"] == 0:
            return "serial range exhausted"
        if counts["free_percent"] <= 10.0:
            return f"serial range at {counts['free_percent']}% remaining"
        return ""

    def allocate(
        self,
        work_order: str,
        *,
        uid: str = "",
        run_id: int | None = None,
        rework: bool = False,
        need_mac: bool = False,
        local_mac: bool = False,
    ) -> Allocation | None:
        serial = ""
        if self.central is not None:
            serial = (
                self.central.reserve_serial(
                    work_order,
                    station=self.station_id,
                    uid=uid,
                    run_id=run_id,
                )
                or ""
            )
        else:
            if rework and uid:
                serial = self.db.serial_by_uid(uid) or ""
            if not serial:
                serial = self.db.allocate_serial(work_order, uid=uid, run_id=run_id) or ""
        if not serial:
            return None
        mac = ""
        if need_mac:
            if self.central is not None:
                mac = (
                    self.central.reserve_mac(
                        work_order,
                        station=self.station_id,
                        uid=uid,
                        run_id=run_id,
                    )
                    or ""
                )
            elif rework and uid:
                mac = self.db.mac_by_uid(uid) or ""
            if not mac and local_mac and uid:
                salt = 0
                while salt < 1024:
                    candidate = locally_administered_mac(uid, salt)
                    owner = self.db.scalar(
                        "SELECT uid FROM units WHERE mac = ? LIMIT 1", (candidate,)
                    )
                    if owner is None or str(owner).upper() == uid.upper():
                        mac = candidate
                        break
                    salt += 1
            if not mac:
                mac = self.db.allocate_mac(work_order, uid=uid, run_id=run_id) or ""
            if not mac:
                self.db.release_serial(serial)
                return None
        return Allocation(serial_number=serial, mac=mac)

    def consume(self, allocation: Allocation, uid: str, run_id: int) -> None:
        self.db.consume_serial(allocation.serial_number, uid, run_id)
        if allocation.mac:
            self.db.consume_mac(allocation.mac, uid, run_id)

    def release(self, allocation: Allocation) -> None:
        if self.central is not None:
            self.central.release(serial=allocation.serial_number, mac=allocation.mac)
            return
        self.db.release_serial(allocation.serial_number)
        if allocation.mac:
            self.db.release_mac(allocation.mac)
