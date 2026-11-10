"""Shared serial/MAC pool for several stations (P4).

The run database stays local (SQLite) per station.  This module is only about
the allocation pool: when ``central_pool.url`` is configured, reservations
are made with PostgreSQL row locks (``FOR UPDATE SKIP LOCKED``) so two
stations can never hand the same serial or MAC to two boards.  Without a URL
the existing local SQLite pool is used unchanged.

Install the optional driver with ``pip install -e ".[central]"``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS serial_pool (
  serial_number text PRIMARY KEY,
  work_order    text NOT NULL,
  state         text NOT NULL,
  uid           text,
  run_id        bigint,
  station_id    text,
  changed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS mac_pool (
  mac           text PRIMARY KEY,
  work_order    text NOT NULL,
  state         text NOT NULL,
  uid           text,
  run_id        bigint,
  station_id    text,
  changed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS serial_pool_free
  ON serial_pool (work_order, state);
CREATE INDEX IF NOT EXISTS mac_pool_free
  ON mac_pool (work_order, state);
"""


class CentralPoolError(RuntimeError):
    """The shared allocation pool is unavailable or inconsistent."""


class CentralPool(Protocol):
    def load_serials(self, work_order: str, serials: Iterable[str]) -> None: ...
    def load_macs(self, work_order: str, macs: Iterable[str]) -> None: ...
    def reserve_serial(
        self, work_order: str, *, station: str, uid: str = "", run_id: int | None = None
    ) -> str | None: ...
    def reserve_mac(
        self, work_order: str, *, station: str, uid: str = "", run_id: int | None = None
    ) -> str | None: ...
    def release(self, *, serial: str = "", mac: str = "") -> None: ...
    def usage(self, work_order: str) -> dict[str, int]: ...


class PostgresPool:
    """PostgreSQL implementation used when a central pool URL is configured."""

    def __init__(self, url: str, *, station_id: str = "") -> None:
        if not url.startswith(("postgresql://", "postgres://")):
            raise CentralPoolError("central_pool.url must be a PostgreSQL URL")
        self.url = url
        self.station_id = station_id

    @contextlib.contextmanager
    def _connection(self) -> Iterator[Any]:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise CentralPoolError(
                "psycopg is required for a central pool; install .[central]"
            ) from exc
        try:
            conn = psycopg.connect(self.url)
        except Exception as exc:
            raise CentralPoolError(f"cannot connect to the central pool: {exc}") from exc
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_schema(self) -> None:
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA)

    def load_serials(self, work_order: str, serials: Iterable[str]) -> None:
        rows = [(serial, work_order, "FREE") for serial in serials]
        if not rows:
            return
        with self._connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO serial_pool (serial_number, work_order, state) "
                "VALUES (%s, %s, %s) ON CONFLICT (serial_number) DO NOTHING",
                rows,
            )

    def load_macs(self, work_order: str, macs: Iterable[str]) -> None:
        rows = [(mac, work_order, "FREE") for mac in macs]
        if not rows:
            return
        with self._connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO mac_pool (mac, work_order, state) "
                "VALUES (%s, %s, %s) ON CONFLICT (mac) DO NOTHING",
                rows,
            )

    def _reserve(
        self, table: str, column: str, work_order: str, station: str, uid: str, run_id: int | None
    ) -> str | None:
        with self._connection() as conn, conn.cursor() as cur:
            if uid:
                cur.execute(
                    f"SELECT {column} FROM {table} WHERE work_order = %s AND uid = %s "
                    "AND state IN ('RESERVED','CONSUMED') LIMIT 1",
                    (work_order, uid.upper()),
                )
                row = cur.fetchone()
                if row is not None:
                    return str(row[0])
            cur.execute(
                f"SELECT {column} FROM {table} WHERE work_order = %s AND state = 'FREE' "
                f"ORDER BY {column} FOR UPDATE SKIP LOCKED LIMIT 1",
                (work_order,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            value = str(row[0])
            cur.execute(
                f"UPDATE {table} SET state = 'RESERVED', uid = %s, run_id = %s, "
                "station_id = %s, changed_at = now() WHERE "
                f"{column} = %s AND state = 'FREE'",
                (uid.upper() or None, run_id, station, value),
            )
            return value if cur.rowcount == 1 else None

    def reserve_serial(
        self, work_order: str, *, station: str, uid: str = "", run_id: int | None = None
    ) -> str | None:
        return self._reserve("serial_pool", "serial_number", work_order, station, uid, run_id)

    def reserve_mac(
        self, work_order: str, *, station: str, uid: str = "", run_id: int | None = None
    ) -> str | None:
        return self._reserve("mac_pool", "mac", work_order, station, uid, run_id)

    def release(self, *, serial: str = "", mac: str = "") -> None:
        with self._connection() as conn, conn.cursor() as cur:
            if serial:
                cur.execute(
                    "UPDATE serial_pool SET state = 'FREE', uid = NULL, run_id = NULL, "
                    "changed_at = now() WHERE serial_number = %s AND state = 'RESERVED'",
                    (serial,),
                )
            if mac:
                cur.execute(
                    "UPDATE mac_pool SET state = 'FREE', uid = NULL, run_id = NULL, "
                    "changed_at = now() WHERE mac = %s AND state = 'RESERVED'",
                    (mac,),
                )

    def usage(self, work_order: str) -> dict[str, int]:
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT state, COUNT(*) FROM serial_pool WHERE work_order = %s "
                "GROUP BY state",
                (work_order,),
            )
            serial = {str(state): int(count) for state, count in cur.fetchall()}
            cur.execute(
                "SELECT state, COUNT(*) FROM mac_pool WHERE work_order = %s GROUP BY state",
                (work_order,),
            )
            mac = {str(state): int(count) for state, count in cur.fetchall()}
        total = sum(serial.values())
        return {
            "TOTAL": total,
            "FREE": serial.get("FREE", 0),
            "RESERVED": serial.get("RESERVED", 0),
            "CONSUMED": serial.get("CONSUMED", 0),
            "MAC_TOTAL": sum(mac.values()),
            "MAC_FREE": mac.get("FREE", 0),
            "MAC_RESERVED": mac.get("RESERVED", 0),
            "MAC_CONSUMED": mac.get("CONSUMED", 0),
        }


@dataclass(slots=True)
class CentralPoolConfig:
    url: str = ""
    station_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.url)


__all__ = [
    "SCHEMA",
    "CentralPool",
    "CentralPoolConfig",
    "CentralPoolError",
    "PostgresPool",
]
