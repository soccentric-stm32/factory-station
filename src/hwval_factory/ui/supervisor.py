"""Supervisor, engineer and administrator dialogs (UI-014, ACC-002)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, icon
from hwval_factory.auth import AuthError, AuthService
from hwval_factory.config import StationConfig, validate_station_config
from hwval_factory.models import Role
from hwval_factory.profile import ProductProfile, import_profile, list_profiles, load_profile
from hwval_factory.station import Station


class WorkOrderDialog(QDialog):
    def __init__(
        self, station: Station, profile: ProductProfile, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.station = station
        self.profile = profile
        self.setWindowTitle("Select work order")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        title = QLabel("Work order")
        title.setObjectName("Heading")
        subtitle = QLabel(
            f"Profile {profile.profile_id} v{profile.version}. Serial format: "
            f"{profile.identity.serial_format}"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("Muted")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        form = QFormLayout()
        self._profiles = list_profiles(
            station.config.profiles_dir,
            require_signature=station.config.require_profile_signature,
            public_key=station.config.profile_public_key or None,
        )
        if not any(p.profile_id == profile.profile_id for p in self._profiles):
            self._profiles.insert(0, profile)
        self.profile_combo = QComboBox()
        for candidate in self._profiles:
            self.profile_combo.addItem(candidate.display, candidate.profile_id)
        current = next(
            (
                index
                for index, candidate in enumerate(self._profiles)
                if candidate.profile_id == profile.profile_id
            ),
            0,
        )
        self.profile_combo.setCurrentIndex(current)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. WO-2026-0042")
        if station.work_order is not None:
            self.name.setText(station.work_order.name)
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 1_000_000)
        self.quantity.setValue(station.work_order.quantity if station.work_order else 1000)
        self.serial_prefix = QLineEdit(
            station.work_order.serial_prefix if station.work_order else ""
        )
        self.serial_start = QSpinBox()
        self.serial_start.setRange(0, 99_999_999)
        self.serial_start.setValue(station.work_order.serial_start if station.work_order else 1)
        self.serial_end = QSpinBox()
        self.serial_end.setRange(0, 99_999_999)
        self.serial_end.setValue(station.work_order.serial_end if station.work_order else 0)
        self.serial_end.setSpecialValueText("auto")
        self.serial_count = QLabel("")
        self.serial_count.setObjectName("Muted")
        self.mac_start = QLineEdit(
            station.work_order.mac_start if station.work_order else ""
        )
        self.mac_start.setPlaceholderText(profile.identity.oui_block or "not enabled")
        self.mac_count = QSpinBox()
        self.mac_count.setRange(0, 1_000_000)
        self.mac_count.setValue(station.work_order.mac_count if station.work_order else 0)
        form.addRow("Profile", self.profile_combo)
        form.addRow("Work order", self.name)
        form.addRow("Quantity", self.quantity)
        form.addRow("Serial prefix", self.serial_prefix)
        form.addRow("Serial start", self.serial_start)
        form.addRow("Serial end", self.serial_end)
        form.addRow("", self.serial_count)
        if profile.identity.mac_enabled:
            form.addRow("MAC start", self.mac_start)
            form.addRow("MAC count", self.mac_count)
        layout.addLayout(form)
        self.warning = QLabel("")
        self.warning.setStyleSheet(f"color: {COLORS['warn']};")
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.textChanged.connect(self._update_count)
        self.quantity.valueChanged.connect(self._update_count)
        self.serial_start.valueChanged.connect(self._update_count)
        self.serial_end.valueChanged.connect(self._update_count)
        self._update_count()

    def _update_count(self, *_args: Any) -> None:
        start = self.serial_start.value()
        end = self.serial_end.value()
        quantity = self.quantity.value()
        count = (end - start + 1) if end >= start and end else quantity
        self.serial_count.setText(f"{max(0, count)} serial numbers will be loaded")
        self.warning.setText(self.station.serial_service.warning(self.name.text().strip()))

    def _accept(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Work order", "Enter a work order name.")
            return
        selected_profile = self._profiles[self.profile_combo.currentIndex()]
        if selected_profile.profile_id != self.profile.profile_id:
            try:
                self.station.set_profile(selected_profile)
            except Exception as exc:
                QMessageBox.critical(self, "Work order", str(exc))
                return
            self.profile = selected_profile
        try:
            self.station.select_work_order(
                name,
                profile_id=self.profile.profile_id,
                quantity=self.quantity.value(),
                serial_prefix=self.serial_prefix.text().strip(),
                serial_start=self.serial_start.value(),
                serial_end=self.serial_end.value() or self.serial_start.value() + self.quantity.value() - 1,
                mac_start=self.mac_start.text().strip(),
                mac_count=self.mac_count.value(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Work order", str(exc))
            return
        self.accept()


class FixturesDialog(QDialog):
    def __init__(self, station: Station, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.station = station
        self.config: StationConfig = station.config
        self.setWindowTitle("Fixtures")
        self.resize(980, 480)
        layout = QVBoxLayout(self)
        title = QLabel("Fixture configuration and health")
        title.setObjectName("Heading")
        layout.addWidget(title)
        self.missing = QLabel("")
        self.missing.setWordWrap(True)
        self.missing.setStyleSheet(f"color: {COLORS['warn']};")
        layout.addWidget(self.missing)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Fixture",
                "ST-LINK serial",
                "Serial port",
                "Telnet port",
                "Enabled",
                "Detected",
                "Insertions",
                "Passing self-test age",
                "Maintenance",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        refresh = QPushButton(icon("refresh", COLORS["text"], 18), "Detect probes")
        refresh.clicked.connect(self.refresh)
        save = QPushButton("Save station configuration")
        save.clicked.connect(self._save)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._detected: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        self._detected = self.station.detect_probes()
        missing = self.station.missing_probes(self._detected)
        self.missing.setText(
            "Missing probes: " + ", ".join(missing) if missing else "All configured probes detected."
        )
        self.table.setRowCount(len(self.config.fixtures))
        for row_index, fixture in enumerate(self.config.fixtures):
            stats = self.station.db.fixture_stats(fixture.name)
            age = self.station.db.self_test_age_hours(fixture.name)
            insertions = 0 if stats is None else int(stats["insertions"])
            maintenance = (
                "due"
                if insertions >= self.config.maintenance_count
                else f"{insertions}/{self.config.maintenance_count}"
            )
            values = [
                fixture.name,
                fixture.probe_serial,
                fixture.serial_port,
                str(fixture.telnet_port),
                "yes" if fixture.enabled else "no",
                "yes" if fixture.probe_serial in self._detected else "no",
                str(insertions),
                "never" if age is None else f"{age:.1f} h",
                maintenance,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in (0, 5, 6, 7, 8):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_index, col, item)

    def _save(self) -> None:
        for row_index, fixture in enumerate(self.config.fixtures):
            if row_index >= self.table.rowCount():
                break
            fixture.probe_serial = self._text(row_index, 1)
            fixture.serial_port = self._text(row_index, 2)
            try:
                fixture.telnet_port = int(self._text(row_index, 3))
            except ValueError:
                fixture.telnet_port = self.config.openocd.telnet_base_port + row_index
            fixture.enabled = self._text(row_index, 4).lower() in ("yes", "true", "1", "on")
        try:
            validate_station_config(self.config)
        except Exception as exc:
            QMessageBox.critical(self, "Fixtures", str(exc))
            return
        if self.config.config_path is None:
            QMessageBox.information(
                self, "Fixtures", "This is an in-memory simulator station; no file to save."
            )
            return
        try:
            self.config.save()
        except Exception as exc:
            QMessageBox.critical(self, "Fixtures", str(exc))
            return
        self.station.db.audit("engineer", "station.fixtures", str(self.config.config_path))
        QMessageBox.information(self, "Fixtures", f"Saved {self.config.config_path}")

    def _text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return "" if item is None else item.text().strip()


class UsersDialog(QDialog):
    def __init__(self, auth: AuthService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.setWindowTitle("Users")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        title = QLabel("Users and roles")
        title.setObjectName("Heading")
        layout.addWidget(title)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["User", "Role", "Badge", "Active"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        form = QFormLayout()
        self.username = QLineEdit()
        self.role = QComboBox()
        for role in Role:
            self.role.addItem(role.value, role.value)
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.badge = QLineEdit()
        form.addRow("User", self.username)
        form.addRow("Role", self.role)
        form.addRow("PIN", self.pin)
        form.addRow("Badge", self.badge)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        add = QPushButton("Add / update")
        add.clicked.connect(self._add)
        change = QPushButton("Change PIN")
        change.clicked.connect(self._change_pin)
        buttons.addWidget(add)
        buttons.addWidget(change)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        rows = self.auth.db.users()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for col, value in enumerate(
                [row["username"], row["role"], row["badge"] or "", "yes" if row["active"] else "no"]
            ):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(index, col, item)

    def _selected_user(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return self.username.text().strip()
        item = self.table.item(row, 0)
        return "" if item is None else item.text()

    def _add(self) -> None:
        username = self.username.text().strip()
        if not username:
            QMessageBox.warning(self, "Users", "Enter a user name.")
            return
        if len(self.pin.text()) < 4:
            QMessageBox.warning(self, "Users", "Use a PIN of at least 4 characters.")
            return
        try:
            self.auth.add_user(
                username,
                self.role.currentData(),
                self.pin.text(),
                self.badge.text().strip(),
            )
        except AuthError as exc:
            QMessageBox.critical(self, "Users", str(exc))
            return
        self.auth.db.audit(self.auth.current_user().username if self.auth.current_user() else "admin", "user.update", username)
        self.pin.clear()
        self.username.clear()
        self.badge.clear()
        self.refresh()

    def _change_pin(self) -> None:
        username = self._selected_user()
        row = self.auth.db.user(username)
        if row is None:
            QMessageBox.warning(self, "Users", "Select a user.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Change PIN for {username}")
        form = QFormLayout(dialog)
        pin = QLineEdit()
        pin.setEchoMode(QLineEdit.EchoMode.Password)
        confirm = QLineEdit()
        confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New PIN", pin)
        form.addRow("Confirm", confirm)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if pin.text() != confirm.text() or len(pin.text()) < 4:
            QMessageBox.warning(self, "Users", "PINs do not match or are too short.")
            return
        self.auth.set_pin(username, pin.text())
        self.auth.db.audit(username, "user.pin", "PIN changed")
        QMessageBox.information(self, "Users", f"PIN changed for {username}.")


class PrinterDialog(QDialog):
    def __init__(self, station: Station, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.station = station
        self.setWindowTitle("Label printer")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        title = QLabel("Label printer")
        title.setObjectName("Heading")
        layout.addWidget(title)
        form = QFormLayout()
        self.enabled = QCheckBox("Send labels to a printer")
        self.enabled.setChecked(station.config.printer.enabled)
        self.host = QLineEdit(station.config.printer.host)
        self.host.setPlaceholderText("printer host or IP")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(station.config.printer.port)
        self.queue = QLineEdit(station.config.printer.queue)
        self.queue.setPlaceholderText("or a local print queue (lp)")
        self.output_dir = QLineEdit(str(station.config.printer.output_dir or station.config.labels_dir))
        form.addRow("", self.enabled)
        form.addRow("Printer host", self.host)
        form.addRow("Port", self.port)
        form.addRow("Print queue", self.queue)
        form.addRow("Fallback output", self.output_dir)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        test = QPushButton("Test print")
        test.clicked.connect(self._test)
        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        buttons.addWidget(test)
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _apply(self) -> None:
        printer = self.station.config.printer
        printer.enabled = self.enabled.isChecked()
        printer.host = self.host.text().strip()
        printer.port = self.port.value()
        printer.queue = self.queue.text().strip()
        printer.output_dir = self.output_dir.text().strip()
        self.station.label_service.printer = printer

    def _test(self) -> None:
        self._apply()
        profile = self.station.profile
        if profile is None:
            QMessageBox.warning(self, "Label printer", "Select a profile first.")
            return
        try:
            result = self.station.label_service.test_print(profile, "supervisor")
        except Exception as exc:
            QMessageBox.critical(self, "Label printer", str(exc))
            return
        QMessageBox.information(
            self,
            "Label printer",
            result.status + (f"\n{result.error}" if result.error else ""),
        )

    def _save(self) -> None:
        self._apply()
        if self.station.config.config_path is not None:
            try:
                self.station.config.save()
            except Exception as exc:
                QMessageBox.critical(self, "Label printer", str(exc))
                return
        self.station.db.audit(
            "engineer",
            "station.printer",
            f"enabled={self.station.config.printer.enabled} "
            f"host={self.station.config.printer.host} "
            f"queue={self.station.config.printer.queue}",
        )
        self.accept()


class OperatorPromptDialog(QDialog):
    def __init__(
        self, fixture: str, payload: dict[str, Any], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.answer = "OK"
        self.setWindowTitle(f"Fixture {fixture} — operator action")
        self.setModal(True)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel(f"Fixture {fixture}: operator action")
        title.setObjectName("Title")
        self.text = QLabel(str(payload.get("text", "Operator action required")))
        self.text.setWordWrap(True)
        self.text.setStyleSheet("font-size: 22px;")
        test_id = QLabel(f"Test: {payload.get('test_id', '—')}")
        test_id.setObjectName("Muted")
        layout.addWidget(title)
        layout.addWidget(self.text)
        layout.addWidget(test_id)
        choices = payload.get("choices") or ["OK"]
        buttons = QHBoxLayout()
        for choice in choices:
            button = QPushButton(str(choice))
            button.setMinimumHeight(52)
            button.setObjectName("Primary" if str(choice) == "OK" else "")
            button.clicked.connect(lambda _checked=False, value=str(choice): self._choose(value))
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _choose(self, value: str) -> None:
        self.answer = value
        self.accept()


def import_profile_dialog(
    station: Station, parent: QWidget | None = None, *, require_signature: bool = False
) -> ProductProfile | None:
    chooser = QDialog(parent)
    chooser.setWindowTitle("Import product profile")
    chooser.setModal(True)
    layout = QVBoxLayout(chooser)
    label = QLabel(
        "Import a released profile archive or directory. The station copy is made "
        "read-only."
    )
    label.setWordWrap(True)
    layout.addWidget(label)
    archive = QPushButton(icon("database", COLORS["text"], 18), "Import archive…")
    directory = QPushButton(icon("board", COLORS["text"], 18), "Import directory…")
    cancel = QPushButton("Cancel")
    layout.addWidget(archive)
    layout.addWidget(directory)
    layout.addWidget(cancel)
    chosen: dict[str, str] = {}

    def pick_archive() -> None:
        chosen["path"] = QFileDialog.getOpenFileName(
            chooser,
            "Profile archive",
            str(station.config.profiles_dir),
            "Profile archives (*.zip *.tar.gz *.tgz);;All files (*)",
        )[0]
        chooser.accept()

    def pick_directory() -> None:
        chosen["path"] = QFileDialog.getExistingDirectory(
            chooser,
            "Profile directory",
            str(station.config.profiles_dir),
        )
        chooser.accept()

    archive.clicked.connect(pick_archive)
    directory.clicked.connect(pick_directory)
    cancel.clicked.connect(chooser.reject)
    if chooser.exec() != QDialog.DialogCode.Accepted or not chosen.get("path"):
        return None
    path = chosen["path"]
    try:
        imported = import_profile(path, station.config.profiles_dir)
        profile = load_profile(
            imported,
            require_signature=require_signature,
            public_key=station.config.profile_public_key or None,
        )
        station.set_profile(profile)
        station.db.audit("engineer", "profile.import", str(imported))
    except Exception as exc:
        QMessageBox.critical(parent, "Profile import", str(exc))
        return None
    QMessageBox.information(
        parent,
        "Profile import",
        f"Imported {profile.profile_id} v{profile.version} into the read-only profile store.",
    )
    return profile


def confirm_rework(parent: QWidget | None = None) -> bool:
    result = QMessageBox.warning(
        parent,
        "Rework approval",
        "Rework may lower readout protection and mass-erase the chip. All flash "
        "content will be lost, including the provisioning record.\n\nContinue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    return result == QMessageBox.StandardButton.Yes


__all__ = [
    "FixturesDialog",
    "OperatorPromptDialog",
    "PrinterDialog",
    "UsersDialog",
    "WorkOrderDialog",
    "confirm_rework",
    "import_profile_dialog",
]
