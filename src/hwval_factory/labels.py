"""ZPL labels, PDF/PNG fallback, preview and reprints (LBL-*)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hwval_factory.config import PrinterConfig
from hwval_factory.database import Database
from hwval_factory.profile import LabelSpec, ProductProfile

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}|\{([\w.]+)\}")


class LabelError(RuntimeError):
    """Label rendering, printing or reprint failed."""


@dataclass(slots=True)
class LabelResult:
    status: str
    zpl: str
    data: dict[str, Any]
    printed: bool = False
    pending: bool = False
    output_paths: list[Path] = field(default_factory=list)
    error: str = ""


def label_data(
    *,
    serial_number: str,
    mac: str,
    product: str,
    hw_revision: str,
    firmware_version: str,
    pcb_serial: str = "",
    uid: str = "",
    work_order: str = "",
    profile: str = "",
    station: str = "",
    operator: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    return {
        "serial": serial_number,
        "serial_number": serial_number,
        "mac": mac,
        "product": product,
        "product_id": product,
        "hw_revision": hw_revision,
        "date": moment.date().isoformat(),
        "datetime": moment.isoformat(timespec="seconds"),
        "fw_version": firmware_version,
        "firmware_version": firmware_version,
        "pcb_serial": pcb_serial,
        "uid": uid,
        "work_order": work_order,
        "profile": profile,
        "station": station,
        "operator": operator,
        "label_revision": "1",
    }


def _zpl_escape(value: str) -> str:
    return value.replace("^", "^^").replace("~", "~~").replace("\n", " ")


def render_zpl(template: str, data: dict[str, Any]) -> str:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        if key not in data:
            missing.add(key)
            return match.group(0)
        return _zpl_escape(str(data[key]))

    rendered = _PLACEHOLDER.sub(replace, template)
    if missing:
        raise LabelError(f"label template references unknown fields: {sorted(missing)}")
    return rendered


def default_template(spec: LabelSpec) -> str:
    fields = spec.fields or ["serial", "mac", "product", "hw_revision", "date", "fw_version"]
    lines: list[str] = []
    y = 12
    for name in fields[:5]:
        if name in ("serial", "serial_number", "mac", "uid", "pcb_serial"):
            continue
        lines.append(
            f"^FO14,{y}^A0N,20,20^FD{name.replace('_', ' ').title()}: "
            + "{{" + name + "}}^FS"
        )
        y += 22
    serial = "{{serial}}" if "serial" in fields or not fields else "{{" + fields[0] + "}}"
    mac_line = (
        "^FO14,112^A0N,16,16^FDMAC: {{mac}}^FS" if "mac" in fields else ""
    )
    return (
        "^XA\n"
        f"^PW{int(spec.width_mm * 8)}\n"
        f"^LL{int(spec.height_mm * 8)}\n"
        "^LH0,0\n"
        + "\n".join(lines)
        + "\n"
        f"^FO14,{max(y, 70)}^A0N,26,26^FD{serial}^FS\n"
        f"{mac_line}\n"
        f"^FO14,145^BY2,2,54^BCN,54,N,N,N^FD{serial}^FS\n"
        f"^FO{int(spec.width_mm * 8) - 76},12^BQN,2,4^FDLA,{serial}^FS\n"
        "^XZ\n"
    )


def _load_template(profile: ProductProfile, printer: PrinterConfig) -> str:
    candidates: list[Path] = []
    if printer.template:
        candidates.append(Path(printer.template))
    if profile.label.template:
        candidates.append(profile.path / profile.label.template)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return default_template(profile.label)


class LabelService:
    def __init__(
        self,
        db: Database,
        printer: PrinterConfig,
        *,
        labels_dir: str | Path,
    ) -> None:
        self.db = db
        self.printer = printer
        self.labels_dir = Path(labels_dir)
        self.labels_dir.mkdir(parents=True, exist_ok=True)

    # -- rendering ----------------------------------------------------
    def render(
        self, profile: ProductProfile, data: dict[str, Any], template: str | None = None
    ) -> str:
        if template is None:
            template = _load_template(profile, self.printer)
        return render_zpl(template, data)

    def preview_png(
        self,
        profile: ProductProfile,
        data: dict[str, Any],
        target: str | Path | None = None,
    ) -> Path:
        path = Path(target) if target else self.labels_dir / f"preview-{data.get('serial', 'label')}.png"
        _draw_label_png(profile.label, data, path)
        return path

    # -- printing -----------------------------------------------------
    def print_for_run(
        self,
        *,
        run_id: int,
        profile: ProductProfile,
        data: dict[str, Any],
        user: str,
    ) -> LabelResult:
        zpl = self.render(profile, data)
        if self.printer.enabled and (self.printer.host or self.printer.queue):
            try:
                if self.printer.host:
                    self._send_tcp(zpl)
                else:
                    self._send_queue(zpl)
            except (OSError, LabelError) as exc:
                result = self._save_fallback(profile, data, zpl, error=str(exc))
                self._record(run_id, data, user, 0, result)
                return result
            result = LabelResult(status="PRINTED", zpl=zpl, data=data, printed=True)
            self._record(run_id, data, user, 0, result)
            return result
        error = ""
        if self.printer.enabled and not (self.printer.host or self.printer.queue):
            error = "printer is enabled but neither host nor print queue is configured"
        result = self._save_fallback(profile, data, zpl, error=error)
        self._record(run_id, data, user, 0, result)
        return result

    def reprint(self, *, run_id: int, user: str) -> LabelResult:
        rows = self.db.labels_for_run(run_id)
        if not rows:
            raise LabelError(f"run {run_id} has no label to reprint")
        latest = rows[-1]
        data = dict(json.loads(str(latest["data"])))
        zpl = str(data.pop("_zpl", "")) or self._reprint_zpl(run_id, data)
        reprint = max(int(row["reprint"]) for row in rows) + 1
        if self.printer.enabled and (self.printer.host or self.printer.queue):
            try:
                if self.printer.host:
                    self._send_tcp(zpl)
                else:
                    self._send_queue(zpl)
            except (OSError, LabelError) as exc:
                raise LabelError(f"reprint failed: {exc}") from exc
            result = LabelResult(status="REPRINTED", zpl=zpl, data=data, printed=True)
        else:
            stem = str(data.get("serial") or run_id).replace("/", "_")
            zpl_path = self.labels_dir / f"{stem}-reprint-{reprint}.zpl"
            png_path = self.labels_dir / f"{stem}-reprint-{reprint}.png"
            pdf_path = self.labels_dir / f"{stem}-reprint-{reprint}.pdf"
            zpl_path.write_text(zpl, encoding="utf-8")
            spec = LabelSpec(
                fields=[
                    key
                    for key in (
                        "serial",
                        "mac",
                        "product",
                        "hw_revision",
                        "date",
                        "fw_version",
                    )
                    if key in data
                ],
                width_mm=50,
                height_mm=25,
            )
            _draw_label_png(spec, data, png_path)
            _write_pdf(spec, data, pdf_path)
            result = LabelResult(
                status="PENDING",
                zpl=zpl,
                data=data,
                pending=True,
                output_paths=[zpl_path, png_path, pdf_path],
            )
        self._record(run_id, data, user, reprint, result)
        return result

    def test_print(self, profile: ProductProfile, user: str) -> LabelResult:
        data = label_data(
            serial_number="TEST-000001",
            mac="02:00:00:00:00:01",
            product=profile.product,
            hw_revision=profile.hw_revisions[-1] if profile.hw_revisions else "A",
            firmware_version=(
                profile.app_image.image.version_string()
                if profile.app_image.image is not None
                else "0.0.0"
            ),
            pcb_serial="TESTPCB",
            uid="00112233445566778899AABB",
            profile=profile.display,
            station="TEST",
            operator=user,
        )
        zpl = self.render(profile, data)
        if self.printer.enabled and (self.printer.host or self.printer.queue):
            if self.printer.host:
                self._send_tcp(zpl)
            else:
                self._send_queue(zpl)
            return LabelResult(status="TEST PRINTED", zpl=zpl, data=data, printed=True)
        result = self._save_fallback(profile, data, zpl)
        return result

    def _reprint_zpl(self, run_id: int, data: dict[str, Any]) -> str:
        path = self.labels_dir / f"{run_id}.zpl"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        # Regenerate a safe minimal label with the stored data.
        spec = LabelSpec(fields=list(data.keys()))
        return render_zpl(default_template(spec), data)

    def _send_tcp(self, zpl: str) -> None:
        if not self.printer.host:
            raise LabelError("no printer host configured")
        try:
            with socket.create_connection(
                (self.printer.host, self.printer.port), timeout=5.0
            ) as sock:
                sock.sendall(zpl.encode("utf-8"))
        except OSError as exc:
            raise LabelError(f"printer {self.printer.host}:{self.printer.port}: {exc}") from exc

    def _send_queue(self, zpl: str) -> None:
        if not self.printer.queue:
            raise LabelError("no print queue configured")
        lp = shutil.which("lp")
        if lp is None:
            raise LabelError("lp is not available for queue printing")
        proc = subprocess.run(
            [lp, "-d", self.printer.queue, "-o", "raw"],
            input=zpl.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise LabelError(
                f"lp failed: {proc.stderr.decode('utf-8', 'replace').strip()}"
            )

    def _save_fallback(
        self,
        profile: ProductProfile,
        data: dict[str, Any],
        zpl: str,
        *,
        error: str = "",
    ) -> LabelResult:
        serial = str(data.get("serial") or "label").replace("/", "_")
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        zpl_path = self.labels_dir / f"{serial}.zpl"
        png_path = self.labels_dir / f"{serial}.png"
        pdf_path = self.labels_dir / f"{serial}.pdf"
        zpl_path.write_text(zpl, encoding="utf-8")
        _draw_label_png(profile.label, data, png_path)
        _write_pdf(profile.label, data, pdf_path)
        return LabelResult(
            status="PENDING" if error else "SAVED",
            zpl=zpl,
            data=data,
            pending=True,
            output_paths=[zpl_path, png_path, pdf_path],
            error=error,
        )

    def _record(
        self, run_id: int, data: dict[str, Any], user: str, reprint: int, result: LabelResult
    ) -> None:
        payload = dict(data)
        payload["_status"] = result.status
        payload["_paths"] = [str(p) for p in result.output_paths]
        payload["_error"] = result.error
        payload["_zpl"] = result.zpl
        self.db.add_label(run_id, payload, user=user, reprint=reprint)


def _draw_label_png(spec: LabelSpec, data: dict[str, Any], path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise LabelError("Pillow is required to render label previews") from exc
    dpi = 203
    width = max(1, int(spec.width_mm / 25.4 * dpi))
    height = max(1, int(spec.height_mm / 25.4 * dpi))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(10, height // 10))
        small = ImageFont.truetype("DejaVuSans.ttf", max(8, height // 14))
    except OSError:
        font = ImageFont.load_default()
        small = font
    y = 4
    fields = spec.fields or ["product", "hw_revision", "date", "fw_version"]
    for name in fields:
        if name in ("serial", "serial_number", "mac", "uid", "pcb_serial"):
            continue
        text = f"{name.replace('_', ' ').title()}: {data.get(name, '')}"
        draw.text((4, y), text, fill="black", font=small)
        y += max(11, height // 14)
        if y > height // 2:
            break
    serial = str(data.get("serial") or data.get("serial_number") or "")
    draw.text((4, max(y + 2, height // 2 - 14)), serial, fill="black", font=font)
    if data.get("mac"):
        draw.text((4, max(y + 4, height // 2 + 2)), f"MAC {data['mac']}", fill="black", font=small)
    # Code 128-like 1D barcode preview (actual printer receives ^BC).
    barcode_top = int(height * 0.62)
    barcode_height = max(16, int(height * 0.22))
    pattern_bytes = hashlib.sha256(serial.encode("utf-8")).digest()
    x = 4
    for byte in pattern_bytes:
        for bit in range(8):
            width_bar = 1 + ((byte >> (7 - bit)) & 1) * 2
            if bit % 2 == 0:
                draw.rectangle(
                    [x, barcode_top, x + width_bar, barcode_top + barcode_height],
                    fill="black",
                )
            x += width_bar + 1
            if x > width - 8:
                break
        if x > width - 8:
            break
    # Data Matrix/QR placeholder block.
    qr_size = min(max(24, height // 3), width // 4)
    qx = width - qr_size - 4
    qy = 4
    cell = max(2, qr_size // 12)
    digest = hashlib.sha256(serial.encode("utf-8")).digest()
    for row in range(qr_size // cell):
        for col in range(qr_size // cell):
            if digest[(row * 12 + col) % len(digest)] & (1 << ((row + col) % 8)):
                draw.rectangle(
                    [qx + col * cell, qy + row * cell, qx + (col + 1) * cell, qy + (row + 1) * cell],
                    fill="black",
                )
    image.save(path, "PNG")


def _write_pdf(spec: LabelSpec, data: dict[str, Any], path: Path) -> None:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise LabelError("Pillow is required to render label PDFs") from exc
    png = path.with_suffix(".tmp.png")
    _draw_label_png(spec, data, png)
    with Image.open(png) as image:
        image.convert("RGB").save(path, "PDF", resolution=203.0)
    with contextlib.suppress(OSError):
        png.unlink()


def preview_bytes(spec: LabelSpec, data: dict[str, Any]) -> bytes:
    """Return PNG bytes for the UI preview without touching disk."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        name = Path(handle.name)
    try:
        _draw_label_png(spec, data, name)
        return name.read_bytes()
    finally:
        with contextlib.suppress(OSError):
            name.unlink()


def png_to_qimage(payload: bytes) -> Any:
    from PySide6.QtGui import QImage

    image = QImage()
    image.loadFromData(payload, "PNG")
    return image


def label_placeholder_names(template: str) -> list[str]:
    names: set[str] = set()
    for match in _PLACEHOLDER.finditer(template):
        names.add(match.group(1) or match.group(2))
    return sorted(names)


def label_pdf_bytes(spec: LabelSpec, data: dict[str, Any]) -> bytes:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "label.pdf"
        _write_pdf(spec, data, path)
        return path.read_bytes()


def png_bytes_to_io(payload: bytes) -> io.BytesIO:
    return io.BytesIO(payload)
