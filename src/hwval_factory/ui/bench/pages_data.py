"""Bench pages that put files on the board: System info (.sys), Calibration, Diagnostics."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

from hwval_factory.bench import csvdb, files, spec
from hwval_factory.bench.factorylink import FactoryLinkError
from hwval_factory.ui.bench.common import (
    BenchPage,
    Card,
    KeyValueTable,
    PathRow,
    chip,
    parse_address,
    set_chip,
    value_label,
)
from hwval_factory.ui.bench.state import BenchState


class WritePage(BenchPage):
    """Common part: preview a file, choose the address, write it and read it back."""

    kind = "sys"
    noun = ".sys file"

    def __init__(self, state: BenchState) -> None:
        super().__init__()
        self.state = state
        self.blob: bytes | None = None
        self.parsed: files.FactoryFile | None = None

    # -- layout pieces used by the subclasses --------------------------
    def build_preview_card(self) -> Card:
        card = Card(
            "Preview",
            f"What will be written to the board. The {self.noun} is checked before anything is sent.",
        )
        self.check_chip = chip("Nothing chosen", "idle")
        self.check_text = value_label()
        line = QHBoxLayout()
        line.setSpacing(10)
        line.addWidget(self.check_chip)
        line.addWidget(self.check_text, 1)
        card.add_row("Check", line)
        self.preview = KeyValueTable()
        card.body.addWidget(self.preview)
        self.add_card(card)
        return card

    def build_target_card(self, extra_buttons: tuple[Any, ...] = ()) -> Card:
        default = spec.KIND_ADDRESS[self.kind]
        card = Card(
            "Write to the board",
            f"The factory firmware stores the file in its block. The default is 0x{default:08X}; "
            "an address must be 4 KB aligned inside 0x08105000 to 0x08107FFF.",
        )
        self.address = QLineEdit(f"0x{default:08X}")
        self.address.setObjectName("AddressEdit")
        self.address.setFixedWidth(180)
        address_line = QHBoxLayout()
        address_line.addWidget(self.address)
        address_line.addStretch(1)
        card.add_row("Address", address_line)
        self.result_chip = chip("Not written", "idle")
        self.result_text = value_label()
        result = QHBoxLayout()
        result.setSpacing(10)
        result.addWidget(self.result_chip)
        result.addWidget(self.result_text, 1)
        card.add_row("Result", result)
        self.write_button = self.primary(f"Write {self.noun}", "flash")
        card.body.addLayout(self.button_row(self.write_button, *extra_buttons))
        self.write_button.clicked.connect(self.write)
        self.add_card(card)
        return card

    # -- state of the page ---------------------------------------------
    def set_blob(self, blob: bytes | None, problem: str = "") -> None:
        """Show the file that would be written, or why it cannot be."""
        self.blob = None
        self.parsed = None
        self.preview.show_items({})
        if blob is None:
            set_chip(
                self.check_chip,
                "Nothing chosen" if not problem else "Not usable",
                "idle" if not problem else "bad",
            )
            self.check_text.setText(problem)
            self._update_write_enabled()
            return
        try:
            parsed = files.parse_file(blob, self.kind)
        except files.FileFormatError as exc:
            set_chip(self.check_chip, "Not valid", "bad")
            self.check_text.setText(str(exc))
            self._update_write_enabled()
            return
        self.blob = blob
        self.parsed = parsed
        items = files.describe(parsed)
        items["size"] = f"{len(blob)} bytes"
        items["CRC-32"] = f"0x{files.crc32(blob):08X}"
        if parsed.bound:
            items["bound to chip"] = parsed.uid.hex().upper()
        self.preview.show_items(items)
        set_chip(self.check_chip, "Valid", "ok")
        self.check_text.setText(f"Header and CRCs are correct, revision {parsed.revision}.")
        self._update_write_enabled()

    def _update_write_enabled(self) -> None:
        self.write_button.setEnabled(self.blob is not None and not self.busy)

    def set_busy(self, busy: bool) -> None:
        self.write_button.setEnabled(self.blob is not None and not busy)

    def target_address(self) -> int:
        address = parse_address(self.address.text())
        if address % spec.BLOCK_SIZE != 0:
            raise ValueError("The address must be a multiple of 0x1000 (4 KB).")
        if not spec.WINDOW_BASE <= address <= spec.WINDOW_END - spec.BLOCK_SIZE:
            raise ValueError(
                "The address must lie in 0x08105000 to 0x08107000. The identity record below it is protected."
            )
        return address

    # -- writing ---------------------------------------------------------
    def after_write(self) -> None:
        """Hook for the subclasses once a write was verified."""

    def write(self) -> None:
        if self.blob is None:
            return
        try:
            address = self.target_address()
        except ValueError as exc:
            set_chip(self.result_chip, "Bad address", "bad")
            self.result_text.setText(str(exc))
            return
        state = self.state
        if not state.simulate and not state.port:
            QMessageBox.warning(self, "Write", "No UART port is selected. Open the Detect page.")
            return
        blob = self.blob
        noun = self.noun

        def job(log: Any, progress: Any) -> files.FactoryFile:
            link = state.open_link(timeout=1.0)
            try:
                info = link.info()
                log(f"{info.name} {info.version}, chip {info.uid_hex}")
                if not info.is_factory_firmware:
                    raise FactoryLinkError(
                        "the board is not running the factory firmware; flash it first"
                    )
                log(f"Writing {len(blob)} bytes to 0x{address:08X}")
                link.write_file(address, blob, progress)
                stored = link.read_file(address)
                if stored is None:
                    raise FactoryLinkError("the block reads back blank after the write")
                log("Read back from the board and compared: identical")
                return stored
            finally:
                link.close()

        def done(stored: object) -> None:
            assert isinstance(stored, files.FactoryFile)
            set_chip(self.result_chip, "Written and verified", "ok")
            self.result_text.setText(f"{noun} stored at 0x{address:08X} and read back identical.")
            self.after_write()

        def failed(message: str) -> None:
            set_chip(self.result_chip, "Failed", "bad")
            self.result_text.setText(message)

        set_chip(self.result_chip, "Writing…", "warn")
        self.result_text.setText("")
        self.run_job(job, done, failed)


class SysInfoPage(WritePage):
    kind = "sys"
    noun = ".sys file"
    title = "System info"
    subtitle = "Find a unit in the factory database, compact its record into a .sys file and write it to the board."

    COLUMNS = ("id", "serial", "product", "hw_version", "fw_version", "batch", "status")

    def __init__(self, state: BenchState) -> None:
        super().__init__(state)
        self._db: csvdb.FactoryCsv | None = None

        source = Card(
            "Factory database", "A CSV file with one row per unit. The id column is unique."
        )
        self.csv_row = PathRow(
            "factory.csv", "Choose the factory database", "CSV files (*.csv);;All files (*)"
        )
        source.add_row("Database", self.csv_row)
        self.db_status = value_label()
        source.add_row("Status", self.db_status)
        self.sample_button = self.secondary("Create sample database", "list")
        self.reload_button = self.secondary("Reload", "refresh")
        source.body.addLayout(self.button_row(self.sample_button, self.reload_button))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by id or serial number")
        self.search.setClearButtonEnabled(True)
        source.add_row("Find unit", self.search)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([name.replace("_", " ") for name in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(190)
        source.body.addWidget(self.table)
        self.add_card(source)

        self.build_preview_card()

        self.bind_chip = QCheckBox(
            "Bind the file to the connected chip (the board then refuses it on any other chip)"
        )
        self.mark_programmed = QCheckBox(
            "Mark the unit as programmed in the database after a verified write"
        )
        self.mark_programmed.setChecked(True)
        self.save_button = self.secondary("Save .sys…", "save")
        card = self.build_target_card((self.save_button,))
        card.body.insertWidget(card.body.count() - 1, self.bind_chip)
        card.body.insertWidget(card.body.count() - 1, self.mark_programmed)
        self.add_activity()

        self.csv_row.changed.connect(self._csv_changed)
        self.sample_button.clicked.connect(self._make_sample)
        self.reload_button.clicked.connect(self._load_db)
        self.search.textChanged.connect(self._fill_table)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.bind_chip.toggled.connect(lambda _on: self._selection_changed())
        self.save_button.clicked.connect(self.save)
        self.csv_row.set_text(state.csv_path)
        self._load_db()
        self._update_write_enabled()

    def on_shown(self) -> None:
        if self._db is None:
            self._load_db()

    def on_state_changed(self) -> None:
        self.csv_row.set_text(self.state.csv_path)

    def _csv_changed(self, path: str) -> None:
        self.state.remember("csv_path", path)
        self._load_db()

    def _make_sample(self) -> None:
        target = Path(self.state.csv_path or self.state.data_dir / "factory.csv")
        if target.exists():
            answer = QMessageBox.question(
                self,
                "Create sample database",
                f"{target.name} already exists. Replace it with sample data?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        csvdb.write_sample(target)
        self.state.remember("csv_path", str(target))
        self.csv_row.set_text(str(target))
        self._load_db()

    def _load_db(self) -> None:
        path = self.state.csv_path
        self._db = None
        if not path:
            self.db_status.setText("No database chosen. Choose a CSV or create a sample.")
        else:
            try:
                self._db = csvdb.FactoryCsv(path)
                new = sum(1 for row in self._db.rows if row.get("status", "") != "programmed")
                self.db_status.setText(f"{len(self._db.rows)} units, {new} not yet programmed.")
            except csvdb.FactoryDbError as exc:
                self.db_status.setText(str(exc))
        self._fill_table()

    def _fill_table(self) -> None:
        rows = self._db.search(self.search.text()) if self._db else []
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, name in enumerate(self.COLUMNS):
                self.table.setItem(row_index, column, QTableWidgetItem(row.get(name, "")))
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        if len(rows) == 1:
            self.table.selectRow(0)
        self._selection_changed()

    def selected_id(self) -> str | None:
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected:
            return None
        item = self.table.item(selected[0].row(), 0)
        return item.text() if item else None

    def _selection_changed(self) -> None:
        ident = self.selected_id()
        if ident is None or self._db is None:
            self.set_blob(None, "Select a unit in the list above." if self._db else "")
            return
        uid = self.state.info.uid if self.bind_chip.isChecked() and self.state.info else None
        if self.bind_chip.isChecked() and uid is None:
            self.set_blob(
                None, "Binding needs the chip UID. Read the device info first, or untick the box."
            )
            return
        try:
            self.set_blob(self._db.sys_file(ident, uid=uid))
        except csvdb.FactoryDbError as exc:
            self.set_blob(None, str(exc))

    def save(self) -> None:
        if self.blob is None:
            return
        ident = self.selected_id() or "unit"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save .sys file", f"{ident}.sys", "System info (*.sys)"
        )
        if path:
            Path(path).write_bytes(self.blob)
            self.log.line(f"Saved {path}")

    def after_write(self) -> None:
        ident = self.selected_id()
        if ident and self._db and self.mark_programmed.isChecked():
            try:
                self._db.mark_programmed(ident)
                self.log.line(f"{ident} marked as programmed in {Path(self._db.path).name}")
            except csvdb.FactoryDbError as exc:
                self.log.line(f"The write worked but the database was not updated: {exc}")
            self._load_db()


class FileWritePage(WritePage):
    """Write a .cal or .diag file the operator chooses."""

    file_filter = ""
    path_attr = ""

    def build_file_card(self, title: str, hint: str, sample_label: str) -> None:
        card = Card(title, hint)
        self.file_row = PathRow(
            f"*{spec.KIND_SUFFIX[self.kind]}",
            f"Choose a {self.noun}",
            f"{self.file_filter};;All files (*)",
        )
        card.add_row("File", self.file_row)
        self.sample_button = self.secondary(sample_label, "list")
        card.body.addLayout(self.button_row(self.sample_button))
        self.add_card(card)
        self.file_row.changed.connect(self._path_changed)
        self.sample_button.clicked.connect(self.make_sample)

    def _path_changed(self, path: str) -> None:
        self.state.remember(self.path_attr, path)
        self.load_file(path)

    def on_state_changed(self) -> None:
        self.file_row.set_text(getattr(self.state, self.path_attr))

    def on_shown(self) -> None:
        self.load_file(getattr(self.state, self.path_attr))

    def load_file(self, path: str) -> None:
        if not path:
            self.set_blob(None)
            return
        try:
            self.set_blob(Path(path).read_bytes())
        except OSError as exc:
            self.set_blob(None, f"Cannot read the file: {exc}")

    def sample_blob(self) -> bytes:
        raise NotImplementedError

    def make_sample(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Create a sample {self.noun}",
            f"sample{spec.KIND_SUFFIX[self.kind]}",
            self.file_filter,
        )
        if not path:
            return
        Path(path).write_bytes(self.sample_blob())
        self.state.remember(self.path_attr, path)
        self.file_row.set_text(path)
        self.load_file(path)
        self.log.line(f"Created {path}")


class CalibrationPage(FileWritePage):
    kind = "cal"
    noun = ".cal file"
    title = "Calibration"
    subtitle = "Load a calibration file and write it to the board."
    file_filter = "Calibration (*.cal)"
    path_attr = "cal_path"

    def __init__(self, state: BenchState) -> None:
        super().__init__(state)
        self.build_file_card(
            "Calibration file",
            "A .cal file holds the gain and offset of each channel.",
            "Create sample .cal…",
        )
        self.build_preview_card()
        self.build_target_card()
        self.add_activity()
        self.file_row.set_text(state.cal_path)
        self.load_file(state.cal_path)

    def sample_blob(self) -> bytes:
        return files.build_cal(
            [(0, 1.0, 0.0), (1, 1.0, 0.0), (2, 1.0, 0.0), (3, 1.0, 0.0)],
            temp_ref=25.0,
            date=time.strftime("%Y-%m-%d"),
            operator="sample",
        )


class DiagnosticsPage(FileWritePage):
    kind = "diag"
    noun = ".diag file"
    title = "Diagnostics"
    subtitle = (
        "Load a fault and diagnostic map, or initialize a clean one, and write it to the board."
    )
    file_filter = "Diagnostics (*.diag)"
    path_attr = "diag_path"

    def __init__(self, state: BenchState) -> None:
        super().__init__(state)
        self.build_file_card(
            "Diagnostic file",
            "A .diag file holds the fault table, the counters and the log setup.",
            "Create sample .diag…",
        )
        self.build_preview_card()
        self.init_button = self.secondary("Initialize clean map", "pulse")
        self.build_target_card((self.init_button,))
        self.add_activity()
        self.init_button.clicked.connect(self.initialize)
        self.file_row.set_text(state.diag_path)
        self.load_file(state.diag_path)

    def set_busy(self, busy: bool) -> None:
        super().set_busy(busy)
        self.init_button.setEnabled(not busy)

    def sample_blob(self) -> bytes:
        return files.build_diag(
            faults=[
                (0x0001, 1, 0, "watchdog reset"),
                (0x0002, 2, 1, "over-temperature"),
                (0x0003, 3, 1, "supply under-voltage"),
            ],
            counters=[(1, 0), (2, 0)],
        )

    def initialize(self) -> None:
        try:
            address = self.target_address()
        except ValueError as exc:
            set_chip(self.result_chip, "Bad address", "bad")
            self.result_text.setText(str(exc))
            return
        state = self.state
        if not state.simulate and not state.port:
            QMessageBox.warning(
                self, "Initialize", "No UART port is selected. Open the Detect page."
            )
            return

        def job(log: Any, progress: Any) -> bytes:
            link = state.open_link(timeout=1.0)
            try:
                info = link.info()
                if not info.is_factory_firmware:
                    raise FactoryLinkError(
                        "the board is not running the factory firmware; flash it first"
                    )
                log(f"Initializing the diagnostic map at 0x{address:08X}")
                return link.diag_init(address)
            finally:
                link.close()

        def done(stored: object) -> None:
            set_chip(self.result_chip, "Initialized", "ok")
            size = len(cast("bytes", stored))
            self.result_text.setText(f"A clean map ({size} bytes) is stored at 0x{address:08X}.")
            self.log.line("Clean diagnostic map written and read back.")

        def failed(message: str) -> None:
            set_chip(self.result_chip, "Failed", "bad")
            self.result_text.setText(message)

        set_chip(self.result_chip, "Writing…", "warn")
        self.run_job(job, done, failed)
