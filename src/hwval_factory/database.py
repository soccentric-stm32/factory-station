"""SQLite persistence: runs, steps, tests, serial pool and audit (TRC-*).

Appendix D schema with a few additive columns/tables for the station's
operational needs (users, work orders, MAC pool, fixture counters and
self-tests).  The database is WAL mode, fsync is requested when a run closes,
and writes are never deleted — corrections are new rows.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA user_version = 1;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS units (
  uid            TEXT PRIMARY KEY,
  pcb_serial     TEXT,
  serial_number  TEXT UNIQUE,
  mac            TEXT UNIQUE,
  product_id     TEXT,
  hw_revision    TEXT,
  first_seen     TEXT NOT NULL,
  last_result    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY,
  uid            TEXT REFERENCES units(uid),
  pcb_serial     TEXT,
  station_id     TEXT NOT NULL,
  fixture        TEXT NOT NULL,
  operator       TEXT NOT NULL,
  work_order     TEXT,
  profile_id     TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  kind           TEXT NOT NULL,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  result         TEXT,
  failed_step    TEXT,
  reason         TEXT,
  supersedes     INTEGER REFERENCES runs(id),
  label_pending  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS steps (
  run_id   INTEGER NOT NULL REFERENCES runs(id),
  seq      INTEGER NOT NULL,
  name     TEXT NOT NULL,
  result   TEXT NOT NULL,
  started_at TEXT NOT NULL,
  duration_ms INTEGER,
  detail   TEXT,
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS test_results (
  run_id   INTEGER NOT NULL REFERENCES runs(id),
  test_id  TEXT NOT NULL,
  result   TEXT NOT NULL,
  measured_value REAL, measured_unit TEXT,
  limit_min REAL, limit_max REAL,
  reason   TEXT,
  operator_answer TEXT
);

CREATE TABLE IF NOT EXISTS serial_pool (
  serial_number TEXT PRIMARY KEY,
  work_order    TEXT NOT NULL,
  state         TEXT NOT NULL,
  uid           TEXT,
  run_id        INTEGER,
  changed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mac_pool (
  mac           TEXT PRIMARY KEY,
  work_order    TEXT NOT NULL,
  state         TEXT NOT NULL,
  uid           TEXT,
  run_id        INTEGER,
  changed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
  uid TEXT NOT NULL, revision INTEGER NOT NULL, run_id INTEGER NOT NULL,
  blob BLOB NOT NULL, written_at TEXT NOT NULL,
  PRIMARY KEY (uid, revision)
);

CREATE TABLE IF NOT EXISTS labels (
  run_id INTEGER NOT NULL, printed_at TEXT NOT NULL, reprint INTEGER NOT NULL DEFAULT 0,
  user TEXT NOT NULL, data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mes_queue (
  run_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, sent_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT
);

CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  pin_hash TEXT NOT NULL DEFAULT '',
  badge TEXT UNIQUE,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_orders (
  name TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 0,
  serial_prefix TEXT NOT NULL DEFAULT '',
  serial_start INTEGER NOT NULL DEFAULT 1,
  serial_end INTEGER NOT NULL DEFAULT 0,
  mac_start TEXT NOT NULL DEFAULT '',
  mac_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  closed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fixture_stats (
  fixture TEXT PRIMARY KEY,
  insertions INTEGER NOT NULL DEFAULT 0,
  runs INTEGER NOT NULL DEFAULT 0,
  passes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  last_maintenance_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS self_tests (
  id INTEGER PRIMARY KEY,
  fixture TEXT NOT NULL,
  at TEXT NOT NULL,
  result TEXT NOT NULL,
  operator TEXT NOT NULL,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS station_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_extras (
  run_id INTEGER PRIMARY KEY REFERENCES runs(id),
  option_bytes TEXT,
  calibration TEXT,
  image_versions TEXT,
  metadata TEXT,
  provisioning_blob BLOB,
  record_revision INTEGER,
  label_pending INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_runs_uid ON runs(uid);
CREATE INDEX IF NOT EXISTS idx_runs_pcb ON runs(pcb_serial);
CREATE INDEX IF NOT EXISTS idx_runs_serial ON units(serial_number);
CREATE INDEX IF NOT EXISTS idx_runs_work_order ON runs(work_order);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_tests_test ON test_results(test_id);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def row_dict(row: Any) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict (``dict(row)`` loses keys)."""
    return {key: row[key] for key in row.keys()}  # noqa: SIM118


class Database:
    """Thread-safe facade; each thread opens its own SQLite connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.RLock()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        with self._lock:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def initialize(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.mark_interrupted_runs()

    def mark_interrupted_runs(self) -> int:
        """EOL-010: any run without a final state is INCOMPLETE after restart."""
        now = utcnow()
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE runs SET result = 'INCOMPLETE', finished_at = ?, "
                "reason = COALESCE(reason, 'station interrupted') "
                "WHERE finished_at IS NULL AND result IS NULL",
                (now,),
            )
            count = cursor.rowcount
        return count

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.rowcount)

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._conn().execute(sql, params).fetchall())

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.fetchone(sql, params)
        return None if row is None else row[0]

    def checkpoint(self) -> None:
        with self._lock:
            self._conn().execute("PRAGMA wal_checkpoint(FULL)")

    # -- runs ----------------------------------------------------------
    def create_run(
        self,
        *,
        uid: str,
        pcb_serial: str,
        station_id: str,
        fixture: str,
        operator: str,
        work_order: str | None,
        profile_id: str,
        profile_version: str,
        kind: str,
        started_at: str,
        supersedes: int | None = None,
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (uid, pcb_serial, station_id, fixture, operator,
                    work_order, profile_id, profile_version, kind, started_at, supersedes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid or None,
                    pcb_serial,
                    station_id,
                    fixture,
                    operator,
                    work_order,
                    profile_id,
                    profile_version,
                    kind,
                    started_at,
                    supersedes,
                ),
            )
            return int(cursor.lastrowid or 0)

    def close_run(
        self,
        run_id: int,
        result: str,
        *,
        failed_step: str | None = None,
        reason: str | None = None,
        finished_at: str | None = None,
        label_pending: bool | None = None,
    ) -> None:
        fields = ["result = ?", "finished_at = ?", "failed_step = ?", "reason = ?"]
        params: list[Any] = [result, finished_at or utcnow(), failed_step, reason]
        if label_pending is not None:
            fields.append("label_pending = ?")
            params.append(1 if label_pending else 0)
        params.append(run_id)
        with self.transaction() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", params)
        self.checkpoint()

    def update_run_identity(
        self,
        run_id: int,
        *,
        uid: str | None = None,
        pcb_serial: str | None = None,
    ) -> None:
        fields: list[str] = []
        params: list[Any] = []
        if uid is not None:
            fields.append("uid = ?")
            params.append(uid)
        if pcb_serial is not None:
            fields.append("pcb_serial = ?")
            params.append(pcb_serial)
        if not fields:
            return
        params.append(run_id)
        with self.transaction() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", params)

    def add_step(
        self,
        run_id: int,
        seq: int,
        name: str,
        result: str,
        started_at: str,
        duration_ms: int | None,
        detail: str = "",
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO steps (run_id, seq, name, result, started_at, "
                "duration_ms, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, seq, name, result, started_at, duration_ms, detail),
            )

    def add_test_result(
        self,
        run_id: int,
        *,
        test_id: str,
        result: str,
        measured_value: float | None = None,
        measured_unit: str = "",
        limit_min: float | None = None,
        limit_max: float | None = None,
        reason: str = "",
        operator_answer: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO test_results (run_id, test_id, result, measured_value, "
                "measured_unit, limit_min, limit_max, reason, operator_answer) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    test_id,
                    result,
                    measured_value,
                    measured_unit,
                    limit_min,
                    limit_max,
                    reason,
                    operator_answer,
                ),
            )

    def upsert_unit(
        self,
        uid: str,
        *,
        pcb_serial: str = "",
        serial_number: str = "",
        mac: str = "",
        product_id: str = "",
        hw_revision: str = "",
        last_result: str = "",
    ) -> None:
        now = utcnow()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO units (uid, pcb_serial, serial_number, mac, product_id,
                    hw_revision, first_seen, last_result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    pcb_serial = COALESCE(NULLIF(excluded.pcb_serial, ''), units.pcb_serial),
                    serial_number = COALESCE(NULLIF(excluded.serial_number, ''), units.serial_number),
                    mac = COALESCE(NULLIF(excluded.mac, ''), units.mac),
                    product_id = COALESCE(NULLIF(excluded.product_id, ''), units.product_id),
                    hw_revision = COALESCE(NULLIF(excluded.hw_revision, ''), units.hw_revision),
                    last_result = COALESCE(NULLIF(excluded.last_result, ''), units.last_result)
                """,
                (
                    uid.upper(),
                    pcb_serial,
                    serial_number or None,
                    mac or None,
                    product_id,
                    hw_revision,
                    now,
                    last_result,
                ),
            )

    def get_unit(self, uid: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM units WHERE uid = ?", (uid.upper(),))

    def get_unit_by_pcb(self, pcb_serial: str) -> sqlite3.Row | None:
        return self.fetchone(
            "SELECT * FROM units WHERE pcb_serial = ? ORDER BY first_seen DESC LIMIT 1",
            (pcb_serial,),
        )

    def get_unit_by_serial(self, serial_number: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM units WHERE serial_number = ?", (serial_number,))

    def runs_for_unit(self, uid: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM runs WHERE uid = ? ORDER BY started_at DESC", (uid.upper(),)
        )

    def runs_for_pcb(self, pcb_serial: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM runs WHERE pcb_serial = ? ORDER BY started_at DESC", (pcb_serial,)
        )

    def run(self, run_id: int) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))

    def steps(self, run_id: int) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM steps WHERE run_id = ? ORDER BY seq", (run_id,))

    def tests(self, run_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM test_results WHERE run_id = ? ORDER BY rowid", (run_id,)
        )

    def search_runs(
        self,
        query: str = "",
        *,
        work_order: str = "",
        profile_id: str = "",
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            clauses.append(
                "(r.pcb_serial LIKE ? OR r.uid LIKE ? OR u.serial_number LIKE ? "
                "OR u.mac LIKE ? OR r.id LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if work_order:
            clauses.append("r.work_order = ?")
            params.append(work_order)
        if profile_id:
            clauses.append("r.profile_id = ?")
            params.append(profile_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return self.fetchall(
            f"""
            SELECT r.*, u.serial_number, u.mac, u.pcb_serial AS unit_pcb
            FROM runs r LEFT JOIN units u ON u.uid = r.uid
            {where}
            ORDER BY r.started_at DESC LIMIT ?
            """,
            params,
        )

    # -- serials and MACs ---------------------------------------------
    def reserve_auto_counter(self, key: str, amount: int) -> int:
        """Reserve a unique counter range for an auto-generated work order."""
        amount = max(1, amount)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT value FROM station_settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                used = conn.execute("SELECT COUNT(*) FROM serial_pool").fetchone()
                start = int(used[0] if used is not None else 0) + 1
            else:
                start = int(row[0])
            conn.execute(
                "INSERT INTO station_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(start + amount)),
            )
            return start

    def count_serials(self, work_order: str) -> int:
        value = self.scalar(
            "SELECT COUNT(*) FROM serial_pool WHERE work_order = ?", (work_order,)
        )
        return int(value or 0)

    def add_serials(self, work_order: str, serials: list[str]) -> int:
        now = utcnow()
        inserted = 0
        with self.transaction() as conn:
            for serial in serials:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO serial_pool (serial_number, work_order, state, changed_at) "
                    "VALUES (?, ?, 'FREE', ?)",
                    (serial, work_order, now),
                )
                inserted += int(cursor.rowcount)
        return inserted

    def serial_counts(self, work_order: str) -> dict[str, int]:
        rows = self.fetchall(
            "SELECT state, COUNT(*) FROM serial_pool WHERE work_order = ? GROUP BY state",
            (work_order,),
        )
        counts = {"FREE": 0, "RESERVED": 0, "CONSUMED": 0}
        for state, count in rows:
            counts[str(state)] = int(count)
        counts["TOTAL"] = sum(counts.values())
        return counts

    def allocate_serial(self, work_order: str) -> str | None:
        now = utcnow()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT serial_number FROM serial_pool WHERE work_order = ? AND state = 'FREE' "
                "ORDER BY rowid LIMIT 1",
                (work_order,),
            ).fetchone()
            if row is None:
                return None
            serial = str(row[0])
            conn.execute(
                "UPDATE serial_pool SET state = 'RESERVED', changed_at = ? "
                "WHERE serial_number = ? AND state = 'FREE'",
                (now, serial),
            )
            return serial

    def consume_serial(self, serial: str, uid: str, run_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE serial_pool SET state = 'CONSUMED', uid = ?, run_id = ?, changed_at = ? "
                "WHERE serial_number = ?",
                (uid.upper(), run_id, utcnow(), serial),
            )

    def release_serial(self, serial: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE serial_pool SET state = 'FREE', uid = NULL, run_id = NULL, changed_at = ? "
                "WHERE serial_number = ? AND state = 'RESERVED'",
                (utcnow(), serial),
            )

    def serial_by_uid(self, uid: str) -> str | None:
        row = self.fetchone("SELECT serial_number FROM units WHERE uid = ?", (uid.upper(),))
        if row is not None and row[0]:
            return str(row[0])
        row = self.fetchone(
            "SELECT serial_number FROM serial_pool WHERE uid = ? AND state = 'CONSUMED' "
            "ORDER BY changed_at DESC LIMIT 1",
            (uid.upper(),),
        )
        return None if row is None else str(row[0])

    def add_macs(self, work_order: str, macs: list[str]) -> int:
        now = utcnow()
        inserted = 0
        with self.transaction() as conn:
            for mac in macs:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO mac_pool (mac, work_order, state, changed_at) "
                    "VALUES (?, ?, 'FREE', ?)",
                    (mac, work_order, now),
                )
                inserted += int(cursor.rowcount)
        return inserted

    def allocate_mac(self, work_order: str) -> str | None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT mac FROM mac_pool WHERE work_order = ? AND state = 'FREE' "
                "ORDER BY rowid LIMIT 1",
                (work_order,),
            ).fetchone()
            if row is None:
                return None
            mac = str(row[0])
            conn.execute(
                "UPDATE mac_pool SET state = 'RESERVED', changed_at = ? "
                "WHERE mac = ? AND state = 'FREE'",
                (utcnow(), mac),
            )
            return mac

    def consume_mac(self, mac: str, uid: str, run_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE mac_pool SET state = 'CONSUMED', uid = ?, run_id = ?, changed_at = ? "
                "WHERE mac = ?",
                (uid.upper(), run_id, utcnow(), mac),
            )

    def release_mac(self, mac: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE mac_pool SET state = 'FREE', uid = NULL, run_id = NULL, changed_at = ? "
                "WHERE mac = ? AND state = 'RESERVED'",
                (utcnow(), mac),
            )

    def mac_by_uid(self, uid: str) -> str | None:
        row = self.fetchone("SELECT mac FROM units WHERE uid = ?", (uid.upper(),))
        if row is not None and row[0]:
            return str(row[0])
        row = self.fetchone(
            "SELECT mac FROM mac_pool WHERE uid = ? AND state = 'CONSUMED' "
            "ORDER BY changed_at DESC LIMIT 1",
            (uid.upper(),),
        )
        return None if row is None else str(row[0])

    # -- work orders ---------------------------------------------------
    def save_work_order(
        self,
        name: str,
        profile_id: str,
        *,
        quantity: int = 0,
        serial_prefix: str = "",
        serial_start: int = 1,
        serial_end: int = 0,
        mac_start: str = "",
        mac_count: int = 0,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO work_orders (name, profile_id, quantity, serial_prefix,
                    serial_start, serial_end, mac_start, mac_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    quantity = excluded.quantity,
                    serial_prefix = excluded.serial_prefix,
                    serial_start = excluded.serial_start,
                    serial_end = excluded.serial_end,
                    mac_start = excluded.mac_start,
                    mac_count = excluded.mac_count
                """,
                (
                    name,
                    profile_id,
                    quantity,
                    serial_prefix,
                    serial_start,
                    serial_end,
                    mac_start,
                    mac_count,
                    utcnow(),
                ),
            )

    def work_order(self, name: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM work_orders WHERE name = ?", (name,))

    def work_orders(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM work_orders ORDER BY created_at DESC")

    def close_work_order(self, name: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE work_orders SET closed = 1 WHERE name = ?", (name,))

    # -- records, labels, MES, audit ---------------------------------
    def add_record(self, uid: str, revision: int, run_id: int, blob: bytes) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO records (uid, revision, run_id, blob, written_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uid.upper(), revision, run_id, sqlite3.Binary(blob), utcnow()),
            )

    def records_for_uid(self, uid: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM records WHERE uid = ? ORDER BY revision DESC", (uid.upper(),)
        )

    def latest_record(self, uid: str) -> sqlite3.Row | None:
        return self.fetchone(
            "SELECT * FROM records WHERE uid = ? ORDER BY revision DESC LIMIT 1",
            (uid.upper(),),
        )

    def add_label(
        self,
        run_id: int,
        data: dict[str, Any],
        *,
        user: str,
        reprint: int = 0,
        printed_at: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO labels (run_id, printed_at, reprint, user, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, printed_at or utcnow(), reprint, user, json.dumps(data, sort_keys=True)),
            )

    def labels_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM labels WHERE run_id = ? ORDER BY printed_at, reprint", (run_id,)
        )

    def queue_mes(self, run_id: int, payload: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mes_queue (run_id, payload, attempts, last_error, sent_at) "
                "VALUES (?, ?, 0, NULL, NULL)",
                (run_id, json.dumps(payload, sort_keys=True)),
            )

    def mes_pending(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM mes_queue WHERE sent_at IS NULL ORDER BY rowid LIMIT ?", (limit,)
        )

    def mes_mark_sent(self, run_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE mes_queue SET sent_at = ? WHERE run_id = ?", (utcnow(), run_id))

    def mes_mark_attempt(self, run_id: int, error: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE mes_queue SET attempts = attempts + 1, last_error = ? WHERE run_id = ?",
                (error[:500], run_id),
            )

    def mes_queue_length(self) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM mes_queue WHERE sent_at IS NULL") or 0)

    def audit(self, user: str, action: str, detail: str = "") -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit (at, user, action, detail) VALUES (?, ?, ?, ?)",
                (utcnow(), user, action, detail),
            )

    def audit_entries(self, limit: int = 1000) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM audit ORDER BY at DESC LIMIT ?", (limit,))

    # -- users ---------------------------------------------------------
    def add_user(
        self,
        username: str,
        role: str,
        pin_hash: str = "",
        badge: str = "",
        active: bool = True,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO users (username, role, pin_hash, badge, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET role = excluded.role, "
                "pin_hash = excluded.pin_hash, badge = excluded.badge, active = excluded.active",
                (username, role, pin_hash, badge or None, int(active), utcnow()),
            )

    def user(self, username: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM users WHERE username = ?", (username,))

    def user_by_badge(self, badge: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM users WHERE badge = ?", (badge,))

    def users(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM users ORDER BY username")

    # -- fixture stats and self-tests ---------------------------------
    def register_insertion(self, fixture: str) -> int:
        now = utcnow()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO fixture_stats (fixture, insertions, updated_at) VALUES (?, 1, ?) "
                "ON CONFLICT(fixture) DO UPDATE SET insertions = insertions + 1, updated_at = ?",
                (fixture, now, now),
            )
            row = conn.execute(
                "SELECT insertions FROM fixture_stats WHERE fixture = ?", (fixture,)
            ).fetchone()
            return 0 if row is None else int(row[0])

    def register_run(self, fixture: str, result: str) -> None:
        now = utcnow()
        passed = 1 if result == "PASS" else 0
        failed = 0 if result == "PASS" else 1
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO fixture_stats (fixture, runs, passes, failures, updated_at) "
                "VALUES (?, 1, ?, ?, ?) ON CONFLICT(fixture) DO UPDATE SET "
                "runs = runs + 1, passes = passes + ?, failures = failures + ?, updated_at = ?",
                (fixture, passed, failed, now, passed, failed, now),
            )

    def fixture_stats(self, fixture: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM fixture_stats WHERE fixture = ?", (fixture,))

    def add_self_test(self, fixture: str, result: str, operator: str, detail: str = "") -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO self_tests (fixture, at, result, operator, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (fixture, utcnow(), result, operator, detail),
            )

    def last_self_test(self, fixture: str, passing_only: bool = False) -> sqlite3.Row | None:
        clause = "AND result = 'PASS'" if passing_only else ""
        return self.fetchone(
            f"SELECT * FROM self_tests WHERE fixture = ? {clause} ORDER BY at DESC LIMIT 1",
            (fixture,),
        )

    def self_test_age_hours(self, fixture: str) -> float | None:
        row = self.last_self_test(fixture, passing_only=True)
        if row is None:
            return None
        try:
            then = datetime.fromisoformat(str(row["at"]))
        except ValueError:
            return None
        if then.tzinfo is None:
            then = then.replace(tzinfo=UTC)
        return (datetime.now(UTC) - then).total_seconds() / 3600.0

    def save_run_extras(
        self,
        run_id: int,
        *,
        option_bytes: dict[str, Any] | None = None,
        calibration: dict[str, Any] | None = None,
        image_versions: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provisioning_blob: bytes = b"",
        record_revision: int = 0,
        label_pending: bool = False,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO run_extras (run_id, option_bytes, calibration, image_versions,
                    metadata, provisioning_blob, record_revision, label_pending)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    option_bytes = excluded.option_bytes,
                    calibration = excluded.calibration,
                    image_versions = excluded.image_versions,
                    metadata = excluded.metadata,
                    provisioning_blob = excluded.provisioning_blob,
                    record_revision = excluded.record_revision,
                    label_pending = excluded.label_pending
                """,
                (
                    run_id,
                    json.dumps(option_bytes or {}, sort_keys=True, default=str),
                    json.dumps(calibration or {}, sort_keys=True, default=str),
                    json.dumps(image_versions or {}, sort_keys=True, default=str),
                    json.dumps(metadata or {}, sort_keys=True, default=str),
                    sqlite3.Binary(provisioning_blob),
                    record_revision,
                    int(label_pending),
                ),
            )

    def run_extras(self, run_id: int) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM run_extras WHERE run_id = ?", (run_id,))

    # -- statistics ----------------------------------------------------
    def yield_stats(self, *, work_order: str = "", since: str = "") -> dict[str, Any]:
        clauses: list[str] = ["result IS NOT NULL"]
        params: list[Any] = []
        if work_order:
            clauses.append("work_order = ?")
            params.append(work_order)
        if since:
            clauses.append("started_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        rows = self.fetchall(
            f"SELECT result, kind, started_at, finished_at FROM runs WHERE {where}", params
        )
        total = len(rows)
        passed = sum(1 for r in rows if r["result"] == "PASS")
        first = [r for r in rows if r["kind"] == "FIRST"]
        first_pass = sum(1 for r in first if r["result"] == "PASS")
        fpy = (first_pass / len(first) * 100.0) if first else 0.0
        duration = 0.0
        for row in rows:
            try:
                start = datetime.fromisoformat(str(row["started_at"]))
                end = datetime.fromisoformat(str(row["finished_at"]))
                duration += max(0.0, (end - start).total_seconds())
            except (ValueError, TypeError):
                continue
        span_hours = 0.0
        if rows:
            starts = []
            for row in rows:
                try:
                    starts.append(datetime.fromisoformat(str(row["started_at"])))
                except ValueError:
                    continue
            if starts:
                start = min(starts)
                end = datetime.now(UTC)
                span_hours = max(1.0 / 3600.0, (end - start).total_seconds() / 3600.0)
        step_rows = self.fetchall(
            f"""
            SELECT s.name, COUNT(*) AS n FROM steps s JOIN runs r ON r.id = s.run_id
            WHERE s.result = 'FAIL' AND {where.replace('result', 'r.result')}
            GROUP BY s.name ORDER BY n DESC
            """,
            params,
        )
        test_rows = self.fetchall(
            f"""
            SELECT t.test_id, COUNT(*) AS n FROM test_results t JOIN runs r ON r.id = t.run_id
            WHERE t.result = 'FAIL' AND {where.replace('result', 'r.result')}
            GROUP BY t.test_id ORDER BY n DESC
            """,
            params,
        )
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "fpy": fpy,
            "units_per_hour": (total / span_hours) if span_hours else 0.0,
            "average_cycle_s": (duration / total) if total else 0.0,
            "step_pareto": [(str(r[0]), int(r[1])) for r in step_rows],
            "test_pareto": [(str(r[0]), int(r[1])) for r in test_rows],
        }

    def cycle_stats(self) -> dict[str, list[dict[str, Any]]]:
        rows = self.fetchall(
            "SELECT fixture, profile_id, profile_version, started_at, finished_at "
            "FROM runs WHERE finished_at IS NOT NULL AND result IS NOT NULL"
        )
        fixture: dict[str, list[float]] = {}
        profile: dict[str, list[float]] = {}
        for row in rows:
            try:
                start = datetime.fromisoformat(str(row["started_at"]))
                end = datetime.fromisoformat(str(row["finished_at"]))
            except (ValueError, TypeError):
                continue
            seconds = max(0.0, (end - start).total_seconds())
            fixture.setdefault(str(row["fixture"]), []).append(seconds)
            key = f"{row['profile_id']} v{row['profile_version']}"
            profile.setdefault(key, []).append(seconds)

        def summarise(groups: dict[str, list[float]]) -> list[dict[str, Any]]:
            result = []
            for name, values in sorted(groups.items()):
                result.append(
                    {
                        "name": name,
                        "runs": len(values),
                        "average_s": sum(values) / len(values),
                        "min_s": min(values),
                        "max_s": max(values),
                    }
                )
            return result

        return {"fixture": summarise(fixture), "profile": summarise(profile)}

    def run_count_for_pcb(self, pcb_serial: str) -> int:
        value = self.scalar(
            "SELECT COUNT(*) FROM runs WHERE pcb_serial = ?", (pcb_serial,)
        )
        return int(value or 0)

    def failure_count_for_pcb(self, pcb_serial: str) -> int:
        value = self.scalar(
            "SELECT COUNT(*) FROM runs WHERE pcb_serial = ? AND result IN ('FAIL','ABORTED','ERROR')",
            (pcb_serial,),
        )
        return int(value or 0)

    def export_rows(
        self, *, work_order: str = "", date_from: str = "", date_to: str = ""
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if work_order:
            clauses.append("r.work_order = ?")
            params.append(work_order)
        if date_from:
            clauses.append("r.started_at >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("r.started_at <= ?")
            params.append(date_to)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self.fetchall(
            f"""
            SELECT r.*, u.serial_number, u.mac, u.product_id, u.hw_revision,
                   e.record_revision, e.option_bytes, e.calibration, e.image_versions,
                   (SELECT COUNT(*) FROM test_results t WHERE t.run_id = r.id AND t.result='FAIL')
                       AS failed_tests
            FROM runs r LEFT JOIN units u ON u.uid = r.uid
            LEFT JOIN run_extras e ON e.run_id = r.id
            {where} ORDER BY r.started_at
            """,
            params,
        )
