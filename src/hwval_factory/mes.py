"""Optional MES uplink: JSON over HTTPS, queued offline (TRC-004)."""

from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from hwval_factory.config import MesConfig
from hwval_factory.database import Database, row_dict


def build_run_payload(db: Database, run_id: int) -> dict[str, Any]:
    run = db.run(run_id)
    if run is None:
        raise KeyError(f"run {run_id} not found")
    steps = [
        {
            "seq": int(row["seq"]),
            "name": str(row["name"]),
            "result": str(row["result"]),
            "started_at": str(row["started_at"]),
            "duration_ms": row["duration_ms"],
            "detail": row["detail"],
        }
        for row in db.steps(run_id)
    ]
    tests = [
        {
            "test_id": str(row["test_id"]),
            "result": str(row["result"]),
            "measured_value": row["measured_value"],
            "measured_unit": row["measured_unit"],
            "limit_min": row["limit_min"],
            "limit_max": row["limit_max"],
            "reason": row["reason"],
            "operator_answer": row["operator_answer"],
        }
        for row in db.tests(run_id)
    ]
    labels = [
        {
            "printed_at": str(row["printed_at"]),
            "reprint": int(row["reprint"]),
            "user": str(row["user"]),
            "data": json.loads(str(row["data"])),
        }
        for row in db.labels_for_run(run_id)
    ]
    unit = db.get_unit(str(run["uid"])) if run["uid"] else None
    extras_row = db.run_extras(run_id)
    extras = None
    if extras_row is not None:
        extras = {
            "option_bytes": json.loads(str(extras_row["option_bytes"] or "{}")),
            "calibration": json.loads(str(extras_row["calibration"] or "{}")),
            "image_versions": json.loads(str(extras_row["image_versions"] or "{}")),
            "metadata": json.loads(str(extras_row["metadata"] or "{}")),
            "provisioning_blob_hex": bytes(extras_row["provisioning_blob"] or b"").hex(),
            "record_revision": extras_row["record_revision"],
            "label_pending": bool(extras_row["label_pending"]),
        }
    return {
        "schema": "hwval.factory.run/1",
        "run": row_dict(run),
        "unit": None if unit is None else row_dict(unit),
        "steps": steps,
        "tests": tests,
        "labels": labels,
        "extras": extras,
    }


class MesService:
    def __init__(
        self,
        db: Database,
        config: MesConfig,
        *,
        on_queue_change: Callable[[int], None] | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.on_queue_change = on_queue_change
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled and bool(self.config.endpoint)

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="mes-uplink", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def queue(self, run_id: int) -> int:
        payload = build_run_payload(self.db, run_id)
        self.db.queue_mes(run_id, payload)
        length = self.db.mes_queue_length()
        if self.on_queue_change:
            self.on_queue_change(length)
        self._wake.set()
        return length

    def trigger(self) -> None:
        self._wake.set()

    def queue_length(self) -> int:
        return self.db.mes_queue_length()

    def _loop(self) -> None:
        backoff = max(1.0, self.config.backoff_s)
        while not self._stop.is_set():
            pending = self.db.mes_pending()
            if not pending:
                self._wake.wait(30.0)
                self._wake.clear()
                continue
            for row in pending:
                if self._stop.is_set():
                    return
                run_id = int(row["run_id"])
                try:
                    self._send(json.loads(str(row["payload"])))
                except Exception as exc:
                    attempts = int(row["attempts"]) + 1
                    self.db.mes_mark_attempt(run_id, str(exc))
                    if self.on_queue_change:
                        self.on_queue_change(self.db.mes_queue_length())
                    if attempts >= self.config.max_attempts:
                        # Keep it queued; the local DB remains the source of
                        # record and a supervisor can retry after fixing config.
                        continue
                    self._stop.wait(min(300.0, backoff * attempts))
                else:
                    self.db.mes_mark_sent(run_id)
                    if self.on_queue_change:
                        self.on_queue_change(self.db.mes_queue_length())
            if self._stop.is_set():
                return
            self._wake.wait(30.0)
            self._wake.clear()

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            self.config.endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.config.token:
            request.add_header("Authorization", f"Bearer {self.config.token}")
        context = (
            ssl.create_default_context()
            if self.config.endpoint.lower().startswith("https://")
            else None
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_s, context=context
            ) as response:
                if response.status >= 300:
                    raise RuntimeError(f"MES HTTP {response.status}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"MES unreachable: {exc}") from exc
