"""Unit history and traceability search/export (UI-013, TRC-003, TRC-005)."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, icon
from hostcore.widgets import DataTable, SearchBox
from hwval_factory.database import Database
from hwval_factory.reports import certificate_pdf, export_csv, export_json


class HistoryView(QWidget):
    reprint_requested = Signal(int)

    def __init__(
        self,
        db: Database,
        *,
        certificates_dir: str | Path,
        labels_dir: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.certificates_dir = Path(certificates_dir)
        self.labels_dir = Path(labels_dir)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        title = QLabel("Unit history")
        title.setObjectName("Heading")
        root.addWidget(title)

        filters = QHBoxLayout()
        self.search = SearchBox("Search PCB serial, UID, serial number, MAC or run")
        self.search.returnPressed.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.work_order = QComboBox()
        self.work_order.addItem("All work orders", "")
        self.work_order.currentIndexChanged.connect(self.refresh)
        search_button = QPushButton(icon("search", COLORS["text"], 18), "Search")
        search_button.clicked.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(QLabel("Work order"))
        filters.addWidget(self.work_order)
        filters.addWidget(search_button)
        root.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.runs = DataTable(
            ["Run", "Started", "PCB serial", "UID", "Serial", "MAC", "Fixture", "Kind", "Result", "Step"]
        )
        self.runs.itemSelectionChanged.connect(self._selected)
        self.runs.doubleClicked.connect(self._selected)
        splitter.addWidget(self.runs)
        bottom = QTabWidget()
        self.detail = DataTable(
            ["Run", "Started", "Kind", "Result", "Fixture", "Profile", "Operator", "Reason"]
        )
        bottom.addTab(self.detail, "All runs of the unit")
        self.labels = DataTable(
            ["Run", "Printed", "Reprint", "User", "Status", "Output"]
        )
        bottom.addTab(self.labels, "Labels and reprints")
        splitter.addWidget(bottom)
        splitter.setSizes([420, 240])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.unit_label = QLabel("Select a run to see the unit's history.")
        self.unit_label.setObjectName("Muted")
        self.export_csv_button = QPushButton("Export CSV")
        self.export_csv_button.clicked.connect(lambda: self._export("csv"))
        self.export_json_button = QPushButton("Export JSON")
        self.export_json_button.clicked.connect(lambda: self._export("json"))
        self.certificate_button = QPushButton("Certificate PDF")
        self.certificate_button.clicked.connect(self._certificate)
        self.reprint_button = QPushButton(icon("printer", COLORS["text"], 18), "Reprint label")
        self.reprint_button.clicked.connect(self._reprint)
        self.audit_button = QPushButton("Audit log")
        self.audit_button.clicked.connect(self._audit)
        buttons.addWidget(self.unit_label, 1)
        for button in (
            self.export_csv_button,
            self.export_json_button,
            self.certificate_button,
            self.reprint_button,
            self.audit_button,
        ):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self._selected_run: int | None = None

    def selected_run(self) -> int | None:
        return self._selected_run

    def refresh(self) -> None:
        current_work_order = str(self.work_order.currentData() or "")
        known = {str(self.work_order.itemData(i) or "") for i in range(self.work_order.count())}
        for row in self.db.work_orders():
            name = str(row["name"])
            if name not in known:
                self.work_order.addItem(name, name)
                known.add(name)
        query = self.search.text().strip()
        rows = self.db.search_runs(query, work_order=current_work_order, limit=1000)
        self.runs.set_rows(
            [
                [
                    row["id"],
                    str(row["started_at"])[0:19].replace("T", " "),
                    row["pcb_serial"] or row["unit_pcb"] or "",
                    row["uid"] or "",
                    row["serial_number"] or "",
                    row["mac"] or "",
                    row["fixture"],
                    row["kind"],
                    row["result"] or "",
                    row["failed_step"] or "",
                ]
                for row in rows
            ]
        )
        if self._selected_run is not None:
            self._show_unit(self._selected_run)

    def _selected(self) -> None:
        run_id = self.runs.selected_key()
        if run_id is None:
            return
        self._selected_run = int(run_id)
        self._show_unit(self._selected_run)

    def _show_unit(self, run_id: int) -> None:
        run = self.db.run(run_id)
        if run is None:
            return
        uid = str(run["uid"] or "")
        if not uid:
            self.detail.set_rows([])
            self.unit_label.setText(f"Run {run_id} has no chip UID.")
            return
        unit = self.db.get_unit(uid)
        if unit is not None:
            self.unit_label.setText(
                f"{unit['serial_number'] or 'no serial'} · UID {unit['uid']} · "
                f"{unit['pcb_serial'] or 'no PCB serial'} · {unit['last_result'] or ''}"
            )
        history = self.db.runs_for_unit(uid)
        self.detail.set_rows(
            [
                [
                    row["id"],
                    str(row["started_at"])[0:19].replace("T", " "),
                    row["kind"],
                    row["result"] or "",
                    row["fixture"],
                    f"{row['profile_id']} v{row['profile_version']}",
                    row["operator"],
                    row["reason"] or "",
                ]
                for row in history
            ]
        )
        label_rows: list[list[object]] = []
        for run_row in history:
            for label in self.db.labels_for_run(int(run_row["id"])):
                try:
                    payload = json.loads(str(label["data"]))
                except json.JSONDecodeError:
                    payload = {}
                label_rows.append(
                    [
                        run_row["id"],
                        str(label["printed_at"])[0:19].replace("T", " "),
                        int(label["reprint"]),
                        label["user"],
                        payload.get("_status", ""),
                        ", ".join(payload.get("_paths", [])) or payload.get("_error", ""),
                    ]
                )
        self.labels.set_rows(label_rows)

    def _export(self, kind: str) -> None:
        work_order = str(self.work_order.currentData() or "")
        default = f"runs-{work_order or 'all'}.{kind}"
        path, _ = QFileDialog.getSaveFileName(self, f"Export {kind.upper()}", default)
        if not path:
            return
        try:
            if kind == "csv":
                written = export_csv(self.db, path, work_order=work_order)
            else:
                written = export_json(self.db, path, work_order=work_order)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export", f"Written to {written}")

    def _certificate(self) -> None:
        if self._selected_run is None:
            QMessageBox.information(self, "Certificate", "Select a run first.")
            return
        self.certificates_dir.mkdir(parents=True, exist_ok=True)
        default = str(self.certificates_dir / f"certificate-{self._selected_run}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Certificate of test", default, "PDF (*.pdf)")
        if not path:
            return
        try:
            written = certificate_pdf(self.db, self._selected_run, path)
        except Exception as exc:
            QMessageBox.critical(self, "Certificate failed", str(exc))
            return
        QMessageBox.information(self, "Certificate", f"Written to {written}")

    def _reprint(self) -> None:
        if self._selected_run is None:
            QMessageBox.information(self, "Reprint", "Select a run first.")
            return
        self.reprint_requested.emit(self._selected_run)

    def _audit(self) -> None:
        rows = self.db.audit_entries(limit=500)
        text = "\n".join(
            f"{row['at']}  {row['user']:<16} {row['action']:<22} {row['detail'] or ''}"
            for row in rows
        )
        box = QMessageBox(self)
        box.setWindowTitle("Audit log")
        box.setText("Audit log (latest 500 entries)")
        box.setDetailedText(text or "No audit entries")
        box.exec()


__all__ = ["HistoryView"]
