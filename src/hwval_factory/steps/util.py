"""Small helpers shared by the step modules."""

from __future__ import annotations


def parse_identity_detail(text: str) -> dict[str, str]:
    """Parse the PRV1 key=value detail added to HelloReply by the app (A-05)."""
    if not text.startswith("PRV1 "):
        return {}
    result: dict[str, str] = {}
    for part in text[5:].split(";"):
        key, sep, value = part.partition("=")
        if sep and key:
            result[key.strip()] = value.strip()
    return result


def uid_word_hex(uid_hex: str) -> str:
    """Render the UID the way the firmware's DeviceInfo does: word0..word2."""
    raw = bytes.fromhex(uid_hex)
    if len(raw) < 12:
        return uid_hex.upper()
    return "".join(f"{int.from_bytes(raw[i : i + 4], 'little'):08X}" for i in (0, 4, 8))


def uid64(uid_hex: str) -> int:
    """Match the firmware's UID64: UID word0 << 32 | UID word1."""
    raw = bytes.fromhex(uid_hex)
    if len(raw) < 8:
        return 0
    return (int.from_bytes(raw[0:4], "little") << 32) | int.from_bytes(raw[4:8], "little")


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
