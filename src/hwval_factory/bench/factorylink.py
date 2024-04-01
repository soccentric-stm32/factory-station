"""Compatibility shim for the retired raw factory client.

The station now speaks FlatBuffers through :mod:`hwval_factory.bench.factoryclient`.
Keep this module so older scripts and tests that imported ``FactoryLink`` keep
working; it is removed with the legacy ``proto=0x46`` path after the hardware
checklist passes.
"""

from __future__ import annotations

from hwval_factory.bench.factoryclient import (
    FactoryClient as FactoryLink,
)
from hwval_factory.bench.factoryclient import (
    FactoryInfo,
    FactoryLinkError,
    FactoryStatusError,
    SelfTestItem,
    SerialFactoryTransport,
)

__all__ = [
    "FactoryInfo",
    "FactoryLink",
    "FactoryLinkError",
    "FactoryStatusError",
    "SelfTestItem",
    "SerialFactoryTransport",
]
