"""SYS, CAL and DIAG pages: one kind, four horizontal tabs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hwval_factory.bench.actions import write_block
from hwval_factory.core import generators, service, spec
from hwval_factory.core.fileformat import describe
from hwval_factory.ui.bench.common import Worker
from hwval_factory.ui.data.csv_table import CsvTableModel


class DataPage(QWidget):
    """A CSV kind with CSV data / Records / Output files / Board tabs."""

    def __init__(self, kind: str, state: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.gen = generators.GENERATORS[kind]
        self.state = state
        self.model = CsvTableModel(self.gen, self)
        self._workers: list[Worker] = []
        self._build()
        self.refresh()

    # -- construction --------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Hint")
        outer.addWidget(self.subtitle)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._csv_tab(), "CSV data")
        self.tabs.addTab(self._records_tab(), "Records")
        self.tabs.addTab(self._output_tab(), "Output files")
        self.tabs.addTab(self._board_tab(), "Board")
        outer.addWidget(self.tabs, 1)

    def _csv_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        row.addWidget(self.path_edit, 1)
        open_button = QPushButton("Open…")
        open_button.clicked.connect(self._choose_csv)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload)
        sample_button = QPushButton("Write sample CSV")
        sample_button.clicked.connect(self._write_sample)
        row.addWidget(open_button)
        row.addWidget(reload_button)
        row.addWidget(sample_button)
        layout.addLayout(row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("id, serial, MAC, product, batch")
        self.filter_edit.textChanged.connect(self.model.set_filter)
        filter_row.addWidget(self.filter_edit, 1)
        self.summary = QLabel("")
        self.summary.setObjectName("Hint")
        filter_row.addWidget(self.summary)
        layout.addLayout(filter_row)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(False)
        layout.addWidget(self.table, 1)

        problems_label = QLabel("Problems")
        problems_label.setObjectName("CardTitle")
        layout.addWidget(problems_label)
        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.problems.setMaximumHeight(110)
        layout.addWidget(self.problems)
        return page

    def _records_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Record", "Value"])
        self.tree.setColumnWidth(0, 260)
        layout.addWidget(self.tree)
        return page

    def _output_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        row = QHBoxLayout()
        selected = QPushButton("Generate selected")
        selected.clicked.connect(self._generate_selected)
        everything = QPushButton("Generate all")
        everything.clicked.connect(self._generate_all)
        row.addWidget(selected)
        row.addWidget(everything)
        row.addStretch(1)
        self.out_summary = QLabel("")
        self.out_summary.setObjectName("Hint")
        row.addWidget(self.out_summary)
        layout.addLayout(row)

        self.plan_table = QTableWidget(0, 3)
        self.plan_table.setHorizontalHeaderLabels(["file", "state", "unit"])
        self.plan_table.horizontalHeader().setStretchLastSection(True)
        self.plan_table.verticalHeader().setVisible(False)
        layout.addWidget(self.plan_table, 2)

        files_label = QLabel("Files in out/")
        files_label.setObjectName("CardTitle")
        layout.addWidget(files_label)
        self.files_list = QPlainTextEdit()
        self.files_list.setReadOnly(True)
        layout.addWidget(self.files_list, 1)
        return page

    def _board_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        row = QHBoxLayout()
        read_button = QPushButton("Read from board")
        read_button.clicked.connect(self._read_board)
        compare_button = QPushButton("Compare with file")
        compare_button.clicked.connect(self._compare_board)
        write_button = QPushButton("Write to board")
        write_button.clicked.connect(self._write_board)
        row.addWidget(read_button)
        row.addWidget(compare_button)
        row.addWidget(write_button)
        if self.kind == "diag":
            init_button = QPushButton("Initialize clean map")
            init_button.clicked.connect(self._diag_init)
            row.addWidget(init_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.board_view = QPlainTextEdit()
        self.board_view.setReadOnly(True)
        self.board_view.setPlaceholderText(
            "Read the block from the connected board to see its header and fields."
        )
        layout.addWidget(self.board_view, 1)
        return page

    # -- data ----------------------------------------------------------
    def refresh(self) -> None:
        self._fill_output()
        path = self._csv_path()
        if path is not None and path.is_file():
            self.reload()
        else:
            self.path_edit.setText(str(path or "not found"))
            self.subtitle.setText("No CSV yet; write a sample or open one.")

    def _csv_path(self) -> Path | None:
        candidate = self.state.workspace.csv_path_path(self.kind)
        if candidate.is_file():
            return candidate
        if self.kind == "sys":
            fallback = Path(self.state.csv_path)
            if fallback.is_file():
                return fallback
        return candidate

    def reload(self) -> None:
        path = Path(self.path_edit.text()) if self.path_edit.text() else self._csv_path()
        if path is None or not path.is_file():
            return
        try:
            issues = self.model.load(path)
        except Exception as exc:
            self.problems.setPlainText(str(exc))
            self.summary.setText("load failed")
            return
        self.path_edit.setText(str(path))
        self.table.resizeColumnsToContents()
        errors = sum(1 for issue in issues if issue.severity == "error")
        warnings = len(issues) - errors
        self.summary.setText(f"{self.model.rowCount()} rows · {errors} error(s) · {warnings} warning(s)")
        self.subtitle.setText(f"{self.gen.title}: {path}")
        self.problems.setPlainText(
            "\n".join(
                f"line {issue.line} [{issue.severity}] {issue.message}" for issue in issues
            )
        )
        self._fill_records()

    def _fill_records(self) -> None:
        self.tree.clear()
        data = self.model.dataset
        if data is None:
            return
        for ident in data.ids:
            top = QTreeWidgetItem([ident, ""])
            for row in data.groups.get(ident, []):
                for name, value in row.items():
                    if name and value:
                        QTreeWidgetItem(top, [name, value])
            if data.shared and self.gen.grouped:
                shared = QTreeWidgetItem(top, ["shared rows", str(len(data.shared))])
                for row in data.shared:
                    QTreeWidgetItem(shared, [row.get("type", "*"), row.get("code", row.get("channel", ""))])
            self.tree.addTopLevelItem(top)
        self.tree.expandToDepth(0)

    # -- generation ----------------------------------------------------
    def _generate_selected(self) -> None:
        unit = self.state.unit_id
        if not unit:
            QMessageBox.information(self, "Generate", "Select a unit on the Home page first.")
            return
        data = self.model.dataset
        if data is None:
            return
        try:
            blob = self.gen.build(data, unit)
            name = self.gen.filename(unit)
            written, problems = service.write(self.state.workspace.out_dir_path, {name: blob}, self.kind)
        except Exception as exc:
            QMessageBox.warning(self, "Generate", str(exc))
            return
        if problems:
            QMessageBox.warning(self, "Generate", "\n".join(problems))
        self._fill_output()
        self.out_summary.setText(f"Wrote {', '.join(written) or 'nothing'}")

    def _generate_all(self) -> None:
        data = self.model.dataset
        if data is None:
            return
        try:
            plan = service.plan(self.gen, data, self.state.workspace.out_dir_path)
            _written, problems = service.write(
                self.state.workspace.out_dir_path, plan.to_write(), self.kind
            )
        except Exception as exc:
            QMessageBox.warning(self, "Generate all", str(exc))
            return
        self.plan_table.setRowCount(0)
        for name, state in sorted(plan.state.items()):
            row = self.plan_table.rowCount()
            self.plan_table.insertRow(row)
            self.plan_table.setItem(row, 0, QTableWidgetItem(name))
            self.plan_table.setItem(row, 1, QTableWidgetItem(state))
            self.plan_table.setItem(row, 2, QTableWidgetItem(plan.owner.get(name, "")))
        self.out_summary.setText(plan.summary())
        if problems:
            QMessageBox.warning(self, "Generate all", "\n".join(problems))
        self._fill_output()

    def _fill_output(self) -> None:
        out = self.state.workspace.out_dir_path
        if out.is_dir():
            names = sorted(
                path.name for path in out.iterdir() if path.suffix in (".sys", ".cal", ".diag", ".csv")
            )
        else:
            names = []
        self.files_list.setPlainText("\n".join(names) if names else "out/ is empty")
        manifest = out / "manifest.csv"
        if manifest.is_file():
            self.out_summary.setText(f"manifest: {manifest}")
        elif not self.out_summary.text():
            self.out_summary.setText(str(out))

    # -- board ---------------------------------------------------------
    def _worker(self, function) -> None:
        def guarded(log, progress):
            with self.state.link_lock:
                return function(log, progress)

        worker = Worker(guarded, self)
        self._workers.append(worker)

        def done(result: object) -> None:
            if isinstance(result, tuple) and result and result[0] == "text":
                self.board_view.setPlainText(str(result[1]))
            self._workers.remove(worker)

        worker.succeeded.connect(done)
        worker.failed.connect(lambda text: self.board_view.setPlainText(f"failed: {text}"))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _read_board(self) -> None:
        kind = self.kind

        def job(log, progress):
            client = self.state.open_link()
            try:
                client.connect(timeout=2.0 if self.state.simulate else 8.0)
                info = client.info()
                parsed = client.read_file(spec.KIND_ADDRESS[kind])
                if parsed is None:
                    return ("text", f"{kind.upper()}: blank\nChip UID: {info.uid_hex}")
                lines = [
                    f"{kind.upper()} block @ 0x{spec.KIND_ADDRESS[kind]:08X}",
                    f"revision {parsed.revision}, {len(parsed.items)} item(s)",
                    f"bound UID: {parsed.uid.hex().upper() or 'unbound'}",
                    "",
                ]
                lines += [f"{name}: {value}" for name, value in describe(parsed)]
                return ("text", "\n".join(lines))
            finally:
                client.close()

        self._worker(job)

    def _compare_board(self) -> None:
        kind = self.kind

        def job(log, progress):
            path = self.state.unit_file(kind)
            if not path.is_file():
                return ("text", f"no file {path}")
            client = self.state.open_link()
            try:
                client.connect(timeout=2.0 if self.state.simulate else 8.0)
                parsed = client.read_file(spec.KIND_ADDRESS[kind])
            finally:
                client.close()
            if parsed is None:
                return ("text", f"{kind.upper()}: blank on the board, {path.name} on disk")
            disk = path.read_bytes()
            same = len(disk) >= spec.FILE_HEADER_SIZE and parsed.payload == disk[spec.FILE_HEADER_SIZE :]
            return (
                "text",
                f"{kind.upper()}: file {path.name}\n"
                f"revision {parsed.revision}, {len(parsed.items)} item(s)\n"
                f"{'equal to the file' if same else 'differs from the file'}",
            )

        self._worker(job)

    def _write_board(self) -> None:
        kind = self.kind

        def job(log, progress):
            result = write_block(kind, self.state, None, None)
            return ("text", result.text())

        self._worker(job)

    def _diag_init(self) -> None:
        def job(log, progress):
            client = self.state.open_link()
            try:
                client.connect(timeout=2.0 if self.state.simulate else 8.0)
                stored = client.diag_init()
                return ("text", f"Clean diagnostic map written ({len(stored)} bytes)")
            finally:
                client.close()

        self._worker(job)

    def _choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Open {self.kind}.csv", str(self.state.workspace.csv_dir_path), "CSV (*.csv)")
        if path:
            self.path_edit.setText(path)
            self.reload()

    def _write_sample(self) -> None:
        target = self.state.workspace.csv_path_path(self.kind)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            QMessageBox.information(self, "Sample CSV", f"{target} already exists")
            return
        target.write_text(self.gen.sample(), encoding="utf-8")
        self.refresh()


__all__ = ["DataPage"]
