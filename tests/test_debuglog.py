"""Debug-UART capture (D-6 / P2-01)."""

from __future__ import annotations

from hwval_factory.debuglog import DebugLog, create_debug_log


class _FakeSerial:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines
        self.closed = False

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def close(self) -> None:
        self.closed = True


def test_debug_log_parses_tags_and_levels() -> None:
    fake = _FakeSerial(
        [
            b"[12] BOOT I starting\r\n",
            b"[34] C-FRT W app version 0.1.0\r\n",
            b"plain bootloader text\r\n",
        ]
    )
    log = DebugLog(port="/dev/null", opener=lambda _port, _baud: fake)
    log.start()
    assert log._thread is not None
    log._thread.join(timeout=2)
    lines = log.stop()
    assert fake.closed
    assert lines[0].endswith("BOOT I starting")
    assert lines[1].endswith("C-FRT W app version 0.1.0")
    assert lines[2].endswith("plain bootloader text")
    assert log.tags() == {"BOOT": 1, "C-FRT": 1}


def test_debug_log_tail_and_null() -> None:
    log = DebugLog(port="x")
    for index in range(30):
        log._append(f"[{index}] TAG I line {index}")
    assert len(log.tail(5)) == 5
    assert "line 29" in log.tail(1)[0]
    assert create_debug_log("").formatted() == []


def test_debug_log_survives_an_open_failure() -> None:
    def _fail(_port: str, _baud: int):
        raise OSError("no such port")

    log = DebugLog(port="missing", opener=_fail)
    log.start()
    log._thread.join(timeout=2)
    assert "no such port" in log.error
