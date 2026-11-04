"""Shared host core for the HWVAL host tools.

This package is the in-tree implementation of the planned
``soccentric-stm32-hostcore`` package (SRS-FACTORY-F767 ARCH-001).  It contains
the HWLINK codec/session, OpenOCD process control, firmware-image helpers, the
light theme and reusable widgets.  HWVAL Studio and HWVAL Factory are both
expected to consume this package unchanged.

The package deliberately has no dependency on the Factory application.
"""

from hostcore.hwlink import (
    HwLinkClient,
    HwLinkError,
    HwLinkSession,
    SerialTransport,
    SimulatedTransport,
)
from hostcore.images import FirmwareImage, ImageError, parse_image, sha256_file
from hostcore.openocd import OpenOcdError, OpenOcdProcess
from hostcore.protocol.link import FrameParser
from hostcore.theme import COLORS, apply_theme, icon

__all__ = [
    "COLORS",
    "FirmwareImage",
    "FrameParser",
    "HwLinkClient",
    "HwLinkError",
    "HwLinkSession",
    "ImageError",
    "OpenOcdError",
    "OpenOcdProcess",
    "SerialTransport",
    "SimulatedTransport",
    "apply_theme",
    "icon",
    "parse_image",
    "sha256_file",
]
