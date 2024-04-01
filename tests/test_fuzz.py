"""Randomised checks of the file format: corruption is always caught, garbage
never crashes the parser, and random records survive a round trip."""

from __future__ import annotations

import random
import struct

import pytest

from hwval_factory.core import fileformat as ff
from hwval_factory.core import spec
from hwval_factory.core.generators import GENERATORS
from tests.test_core import load


def sample_files(tmp_path):
    files = []
    for kind, gen in GENERATORS.items():
        data = load(tmp_path, gen)
        for ident in data.ids[:3]:
            files.append((kind, gen.build(data, ident, revision=3, uid=bytes(range(1, 13)))))
    return files


def test_every_single_bit_flip_is_detected(tmp_path):
    for kind, blob in sample_files(tmp_path):
        for index in range(len(blob)):
            for bit in (0, 7):
                bad = bytearray(blob)
                bad[index] ^= 1 << bit
                with pytest.raises(ff.FileFormatError):
                    ff.parse_file(bytes(bad), kind)


def test_every_truncation_and_extension_is_detected(tmp_path):
    for kind, blob in sample_files(tmp_path):
        for cut in range(len(blob)):
            with pytest.raises(ff.FileFormatError):
                ff.parse_file(blob[:cut], kind)
        with pytest.raises(ff.FileFormatError):
            ff.parse_file(blob + b"\x00", kind)


def test_wrong_kind_is_rejected(tmp_path):
    for kind, blob in sample_files(tmp_path):
        other = next(k for k in GENERATORS if k != kind)
        with pytest.raises(ff.FileFormatError):
            ff.parse_file(blob, other)


def test_random_garbage_only_raises_file_format_error():
    rng = random.Random(1)
    for _ in range(2000):
        blob = rng.randbytes(rng.randint(0, 200))
        try:
            ff.parse_file(blob)
        except ff.FileFormatError:
            pass


def test_garbage_payload_with_valid_crcs_never_crashes():
    """A well-formed header around a random payload: the TLV parser must cope."""
    rng = random.Random(2)
    for _ in range(2000):
        payload = rng.randbytes(rng.randint(0, 300))
        blob = ff.build_file(rng.choice(list(spec.KIND_MAGIC)), payload)
        try:
            parsed = ff.parse_file(blob)
            ff.describe(parsed)
            ff.layout(blob)
        except ff.FileFormatError:
            pass


def test_random_records_round_trip():
    rng = random.Random(3)
    text = "abcdefghijklmnopqrstuvwxyz0123456789 -_./"
    for _ in range(300):
        word = lambda n: "".join(rng.choice(text) for _ in range(rng.randint(1, n))).strip() or "x"  # noqa: E731
        mac = bytes([rng.randrange(256) & 0xFE | 2, *rng.randbytes(5)])
        record = {
            "serial": word(30), "product": word(12), "hw_version": word(4),
            "fw_version": word(10), "batch": word(8), "mfg_date": "2026-01-02",
            "mac": ":".join(f"{b:02X}" for b in mac), "notes": word(60),
        }
        revision = rng.randint(0, 0xFFFF)
        uid = rng.randbytes(12) if rng.random() < 0.5 else None
        parsed = ff.parse_file(ff.build_sys(record, revision=revision, uid=uid), "sys")
        fields = dict(ff.describe(parsed))
        assert fields["serial"] == record["serial"] and fields["mac"] == record["mac"]
        assert parsed.revision == revision
        assert parsed.uid == (uid or bytes(12))

    for _ in range(300):
        channels = [(i, rng.uniform(0.5, 1.5), rng.uniform(-10, 10)) for i in range(rng.randint(1, 40))]
        parsed = ff.parse_file(ff.build_cal(channels, temp_ref=25.0, date="2026-01-02", operator="x"), "cal")
        got = [struct.unpack("<Bff", v) for t, v in parsed.items if t == spec.CAL_CHANNEL]
        assert [g[0] for g in got] == [c[0] for c in channels]
        for (_, g1, o1), (_, g2, o2) in zip(got, channels):
            assert g1 == pytest.approx(g2, rel=1e-6) and o1 == pytest.approx(o2, rel=1e-6, abs=1e-6)
