"""Debug-UART capture (D-6 / P2-01).

BOOT and every application print ``[<ms>] <TAG> <E|W|I|D> <text>`` on USART6
TX (PG14).  A fixture may name a USB-UART; the station then reads it in a
thread for the whole run, timestamps every line and keeps it with the run.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

LINE_RE = re.compile(r"^\[(?P<ms>\d+)\]\s+(?P<tag>\S+)\s+(?P<level>[EWID])\s+(?P<text>.*)$")


@dataclass(slots=True)
class DebugLine:
    at: float
    text: str
    tag: str = ""
    level: str = ""
    device_ms: int = 0

    def formatted(self) -> str:
        prefix = f"{self.tag} {self.level} " if self.tag else ""
        return f"{self.at:.3f} {prefix}{self.text}"


@dataclass
class DebugLog:
    port: str = ""
    baudrate: int = 115200
    opener: Callable[[str, int], Any] | None = None
    lines: list[DebugLine] = field(default_factory=list)
    error: str = ""
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _serial: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        if not self.port or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._read_loop, name=f"debug-uart-{self.port}", daemon=True
        )
        self._thread.start()

    def _open(self) -> Any:
        if self.opener is not None:
            return self.opener(self.port, self.baudrate)
        import serial

        return serial.Serial(self.port, self.baudrate, timeout=0.2)

    def _read_loop(self) -> None:
        try:
            self._serial = self._open()
        except Exception as exc:
            self.error = str(exc)
            return
        while not self._stop.is_set():
            try:
                raw = self._serial.readline()
            except Exception as exc:
                self.error = str(exc)
                break
            if not raw:
                continue
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            if text:
                self._append(text)

    def _append(self, text: str, at: float | None = None) -> None:
        match = LINE_RE.match(text)
        entry = DebugLine(
            at=time.monotonic() if at is None else at,
            text=text,
            tag=match.group("tag") if match else "",
            level=match.group("level") if match else "",
            device_ms=int(match.group("ms")) if match else 0,
        )
        with self._lock:
            self.lines.append(entry)
            if len(self.lines) > 20000:
                del self.lines[:5000]

    def stop(self) -> list[str]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._serial is not None:
            with contextlib.suppress(Exception):
                self._serial.close()
            self._serial = None
        return self.formatted()

    def formatted(self) -> list[str]:
        with self._lock:
            return [line.formatted() for line in self.lines]

    def tail(self, count: int = 20) -> list[str]:
        return self.formatted()[-count:]

    def tags(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for line in self.lines:
                if line.tag:
                    counts[line.tag] = counts.get(line.tag, 0) + 1
        return counts

    def lines_for(self, tag: str) -> list[str]:
        with self._lock:
            return [line.formatted() for line in self.lines if line.tag == tag]


class NullDebugLog(DebugLog):
    def start(self) -> None:
        return

    def stop(self) -> list[str]:
        return []


def create_debug_log(port: str) -> DebugLog:
    return DebugLog(port=port) if port else NullDebugLog()


__all__ = ["DebugLine", "DebugLog", "NullDebugLog", "create_debug_log"]
