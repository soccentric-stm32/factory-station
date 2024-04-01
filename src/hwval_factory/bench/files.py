"""Adapter over :mod:`hwval_factory.core.fileformat`.

The data tool and the station use one file-format implementation.  The few
helpers that the bench pages liked (a dict-shaped ``describe`` and file
lookup) live here; the bytes on disk are built and validated by ``core``.
"""

from __future__ import annotations

from pathlib import Path

from hwval_factory.core import fileformat as _ff
from hwval_factory.core.fileformat import (
    FactoryFile,
    FileFormatError,
    build_cal,
    build_diag,
    build_file,
    build_sys,
    crc32,
    parse_file,
    parse_tlv,
    tlv,
)

kind_for_path = lambda path: next(  # noqa: E731
    (kind for kind, suffix in (("sys", ".sys"), ("cal", ".cal"), ("diag", ".diag"))
     if Path(path).suffix.lower() == suffix),
    None,
)


def describe(parsed: FactoryFile) -> dict[str, str]:
    return dict(_ff.describe(parsed))


def load_file(path: str | Path, expected_kind: str | None = None) -> tuple[bytes, FactoryFile]:
    raw = Path(path).read_bytes()
    return raw, parse_file(raw, expected_kind)


__all__ = [
    "FactoryFile",
    "FileFormatError",
    "build_cal",
    "build_diag",
    "build_file",
    "build_sys",
    "crc32",
    "describe",
    "kind_for_path",
    "load_file",
    "parse_file",
    "parse_tlv",
    "tlv",
]
