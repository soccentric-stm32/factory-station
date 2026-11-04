from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

from hwval_factory.config import MesConfig
from hwval_factory.database import Database
from hwval_factory.mes import MesService


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, str]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        type(self).seen.append(dict(self.headers))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


def test_mes_send_sets_idempotency_key(tmp_path: Path) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    db = Database(tmp_path / "mes.sqlite")
    db.initialize()
    config = MesConfig(
        enabled=True,
        endpoint=f"http://127.0.0.1:{server.server_port}/runs",
        timeout_s=5,
    )
    service = MesService(db, config)
    service._send({"schema": "test", "run": {"id": 42}})
    thread.join(timeout=5)
    server.server_close()
    db.close()
    assert _Handler.seen
    assert _Handler.seen[0]["Idempotency-Key"] == "hwval-run-42"
