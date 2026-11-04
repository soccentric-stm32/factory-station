"""CSV/JSON exports and per-unit PDF certificates of test (TRC-005)."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hwval_factory.database import Database, row_dict


def _rows_as_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [row_dict(row) for row in rows]


def export_csv(
    db: Database,
    path: str | Path,
    *,
    work_order: str = "",
    date_from: str = "",
    date_to: str = "",
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows_as_dicts(
        db.export_rows(work_order=work_order, date_from=date_from, date_to=date_to)
    )
    headers = list(rows[0].keys()) if rows else ["id"]
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return target


def export_json(
    db: Database,
    path: str | Path,
    *,
    work_order: str = "",
    date_from: str = "",
    date_to: str = "",
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows_as_dicts(
        db.export_rows(work_order=work_order, date_from=date_from, date_to=date_to)
    )
    payload = {
        "schema": "hwval.factory.export/1",
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "work_order": work_order,
        "date_from": date_from,
        "date_to": date_to,
        "runs": rows,
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def certificate_pdf(db: Database, run_id: int, path: str | Path) -> Path:
    """Render a compact certificate of test with Pillow (no Qt needed)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required for PDF certificates") from exc
    run = db.run(run_id)
    if run is None:
        raise KeyError(f"run {run_id} not found")
    unit = db.get_unit(str(run["uid"])) if run["uid"] else None
    tests = db.tests(run_id)
    steps = db.steps(run_id)
    width, height = 1240, 1754  # A4 at 150 dpi
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title: Any
    heading: Any
    body: Any
    small: Any
    try:
        title = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
        heading = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
        body = ImageFont.truetype("DejaVuSans.ttf", 19)
        small = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        title = heading = body = small = ImageFont.load_default()

    def line(y: int, text: str, font: Any = body, *, gap: int = 30) -> int:
        draw.text((60, y), text, fill="black", font=font)
        return y + gap

    y = 60
    y = line(y, "HWVAL Factory — Certificate of Test", title, gap=55)
    y = line(y, f"Run #{run_id}   Result: {run['result'] or 'INCOMPLETE'}", heading, gap=36)
    y = line(y, f"Station: {run['station_id']}   Fixture: {run['fixture']}", gap=28)
    y = line(y, f"Operator: {run['operator']}   Work order: {run['work_order'] or '—'}", gap=28)
    y = line(
        y,
        f"Profile: {run['profile_id']} v{run['profile_version']}   Kind: {run['kind']}",
        gap=28,
    )
    y = line(y, f"Started: {run['started_at']}   Finished: {run['finished_at'] or '—'}", gap=28)
    if unit is not None:
        y = line(y, f"PCB serial: {unit['pcb_serial'] or '—'}   UID: {unit['uid']}", gap=28)
        y = line(y, f"Serial number: {unit['serial_number'] or '—'}", gap=28)
        y = line(y, f"MAC: {unit['mac'] or '—'}   Product: {unit['product_id'] or '—'}", gap=28)
        y = line(y, f"Hardware revision: {unit['hw_revision'] or '—'}", gap=36)
    if run["failed_step"]:
        y = line(y, f"Failed step: {run['failed_step']} — {run['reason'] or ''}", heading, gap=36)
    draw.line([(60, y), (width - 60, y)], fill="black", width=2)
    y += 24
    y = line(y, "Steps", heading, gap=32)
    for step in steps:
        duration = "" if step["duration_ms"] is None else f" {int(step['duration_ms']) / 1000:.2f}s"
        y = line(
            y,
            f"{int(step['seq']):02d}  {step['name']:<28} {step['result']}{duration}",
            small,
            gap=24,
        )
        if y > height - 420:
            break
    y += 18
    y = line(y, "Test results", heading, gap=32)
    for test in tests:
        value = ""
        if test["measured_value"] is not None:
            value = f" {test['measured_value']} {test['measured_unit'] or ''}"
        limits = ""
        if test["limit_min"] is not None or test["limit_max"] is not None:
            limits = f" [{test['limit_min']}..{test['limit_max']}]"
        y = line(
            y,
            f"{test['test_id']:<22} {test['result']:<6}{value}{limits}",
            small,
            gap=24,
        )
        if y > height - 120:
            break
    y = max(y + 20, height - 100)
    y = line(
        y,
        "Generated by HWVAL Factory. The station database is the source of record.",
        small,
        gap=22,
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "PDF", resolution=150.0)
    return target
