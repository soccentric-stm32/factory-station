from __future__ import annotations

import re
from datetime import UTC, datetime

from hwval_factory.serials import (
    format_mac,
    luhn_digit,
    luhn_valid,
    parse_mac,
    parse_oui_block,
    render_serial,
)


def test_serial_template_and_luhn() -> None:
    moment = datetime(2026, 11, 4, tzinfo=UTC)
    serial = render_serial(
        "SC767-{yyww}-{counter:06}-{luhn}",
        counter=42,
        product_code="sc767",
        now=moment,
    )
    assert re.fullmatch(r"SC767-2645-000042-\d", serial)
    assert luhn_valid(serial)
    assert luhn_digit("000042".replace("-", "")) in "0123456789"


def test_mac_format_and_oui() -> None:
    value = parse_mac("02:00:00:00:00:01")
    assert format_mac(value) == "02:00:00:00:00:01"
    prefix, mask, capacity = parse_oui_block("02:00:00:00:00:00/24")
    assert prefix == value - 1
    assert mask == 0xFFFFFF000000
    assert capacity == 1 << 24


def test_locally_administered_mac_is_stable_and_unicast() -> None:
    from hwval_factory.serials import locally_administered_mac

    uid = "002A004B3337500D37323336"
    first = locally_administered_mac(uid)
    assert first == locally_administered_mac(uid)
    value = parse_mac(first)
    assert not value & 0x010000000000  # unicast
    assert value & 0x020000000000      # locally administered
    assert first != locally_administered_mac(uid, 1)
