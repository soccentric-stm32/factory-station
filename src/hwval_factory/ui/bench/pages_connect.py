"""Bench pages for the board itself: Detect, Bootloader, Factory firmware, Device info."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from hostcore import layout
from hwval_factory.bench import files, flashing, spec
from hwval_factory.bench.factorylink import FactoryInfo, FactoryLinkError
from hwval_factory.ui.bench.common import (
    BenchPage,
    Card,
    PathRow,
    chip,
    set_chip,
    value_label,
)
from hwval_factory.ui.bench.state import BenchState

DEV_KEY_ID = layout.DEVELOPMENT_KEY_ID


class DetectPage(BenchPage):
    title = "Detect"
    subtitle = "Find the ST-LINK probe and the UART port of the board on the bench."

    def __init__(self, state: BenchState) -> None:
        super().__init__()
        self.state = state
        self._signature: tuple[Any, ...] = ()

        card = Card(
            "Connection",
            "The station needs the ST-LINK to flash over SWD and its virtual COM port to talk UART.",
        )
        self.stlink_chip = chip("Not found", "idle")
        self.stlink_combo = QComboBox()
        self.stlink_combo.setMinimumWidth(260)
        stlink_row = QHBoxLayout()
        stlink_row.setSpacing(10)
        stlink_row.addWidget(self.stlink_chip)
        stlink_row.addWidget(self.stlink_combo, 1)
        card.add_row("ST-LINK probe", stlink_row)

        self.uart_chip = chip("Not found", "idle")
        self.uart_combo = QComboBox()
        self.uart_combo.setMinimumWidth(260)
        uart_row = QHBoxLayout()
        uart_row.setSpacing(10)
        uart_row.addWidget(self.uart_chip)
        uart_row.addWidget(self.uart_combo, 1)
        card.add_row("UART port", uart_row)

        self.all_ports = QCheckBox("Show every serial port, not only USB ones")
        self.simulate = QCheckBox("Use a simulated board (no hardware needed)")
        self.simulate.setChecked(state.simulate)
        options = QVBoxLayout()
        options.setSpacing(6)
        options.addWidget(self.all_ports)
        options.addWidget(self.simulate)
        card.add_row("Options", options)

        self.refresh_button = self.primary("Refresh", "refresh")
        card.body.addLayout(self.button_row(self.refresh_button))
        self.add_card(card)

        found = Card("Devices found")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Kind", "Name", "Serial", "Port"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(160)
        found.body.addWidget(self.table)
        self.add_card(found)
        self.column.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh)
        self.stlink_combo.activated.connect(self._pick_probe)
        self.uart_combo.activated.connect(self._pick_port)
        self.all_ports.toggled.connect(lambda _on: self._render())
        self.simulate.toggled.connect(self._toggle_simulate)
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self.refresh)

    # -- events -------------------------------------------------------
    def on_shown(self) -> None:
        self.refresh()
        self.timer.start()

    def hideEvent(self, event: object) -> None:
        self.timer.stop()
        super().hideEvent(event)  # type: ignore[arg-type]

    def refresh(self) -> None:
        self.state.refresh_detection()

    def _toggle_simulate(self, on: bool) -> None:
        if on != self.state.simulate:
            self.state.set_simulate(on)

    def _pick_probe(self, index: int) -> None:
        serial = self.stlink_combo.itemData(index)
        if serial is not None:
            self.state.select(probe_serial=serial)

    def _pick_port(self, index: int) -> None:
        port = self.uart_combo.itemData(index)
        if port is not None:
            self.state.select(port=port)

    def on_state_changed(self) -> None:
        if self.simulate.isChecked() != self.state.simulate:
            self.simulate.blockSignals(True)
            self.simulate.setChecked(self.state.simulate)
            self.simulate.blockSignals(False)
        self._render()

    # -- view ---------------------------------------------------------
    def _visible_ports(self) -> list[Any]:
        ports = self.state.detected.ports
        if self.all_ports.isChecked():
            return ports
        return [port for port in ports if port.vid is not None or port.is_stlink]

    def _render(self) -> None:
        probes = self.state.detected.probes
        ports = self._visible_ports()
        signature = (
            tuple((p.serial, p.port) for p in probes),
            tuple(p.device for p in ports),
            self.state.probe_serial,
            self.state.port,
        )
        if signature == self._signature:
            return
        self._signature = signature

        self.stlink_combo.blockSignals(True)
        self.stlink_combo.clear()
        for probe in probes:
            self.stlink_combo.addItem(probe.label, probe.serial)
        if self.state.probe_serial:
            index = self.stlink_combo.findData(self.state.probe_serial)
            self.stlink_combo.setCurrentIndex(max(index, 0))
        elif probes:
            self.stlink_combo.setCurrentIndex(-1)
            self.stlink_combo.setPlaceholderText("Choose a probe")
        self.stlink_combo.setEnabled(len(probes) > 1)
        self.stlink_combo.blockSignals(False)

        self.uart_combo.blockSignals(True)
        self.uart_combo.clear()
        for port in ports:
            self.uart_combo.addItem(port.label, port.device)
        if self.state.port:
            self.uart_combo.setCurrentIndex(max(self.uart_combo.findData(self.state.port), 0))
        elif ports:
            self.uart_combo.setCurrentIndex(-1)
            self.uart_combo.setPlaceholderText("Choose a port")
        self.uart_combo.setEnabled(len(ports) > 1)
        self.uart_combo.blockSignals(False)

        if not probes:
            set_chip(self.stlink_chip, "Not found", "bad")
        elif self.state.probe_serial:
            set_chip(self.stlink_chip, "Detected", "ok")
        else:
            set_chip(self.stlink_chip, f"{len(probes)} found, choose one", "warn")
        if not ports:
            set_chip(self.uart_chip, "Not found", "bad")
        elif self.state.port:
            set_chip(self.uart_chip, "Selected", "ok")
        else:
            set_chip(self.uart_chip, f"{len(ports)} found, choose one", "warn")

        rows: list[tuple[str, str, str, str]] = []
        for probe in probes:
            rows.append(
                ("ST-LINK", probe.description or "ST-LINK", probe.serial, probe.port or "—")
            )
        for port in ports:
            rows.append(
                ("UART", port.description or "serial port", port.serial or "—", port.device)
            )
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()


class BootloaderPage(BenchPage):
    title = "Bootloader"
    subtitle = "Write the A/B bootloader to a blank board over SWD. The flash is erased first."

    def __init__(self, state: BenchState) -> None:
        super().__init__()
        self.state = state

        image = Card(
            "Bootloader image",
            "Build it in the bootloader repository (scripts/build.sh) so it trusts the key below.",
        )
        self.image_row = PathRow(
            "boot.elf, boot.hex or boot.bin",
            "Choose the bootloader image",
            "Firmware (*.elf *.hex *.bin);;All files (*)",
        )
        image.add_row("Image", self.image_row)
        self.image_info = value_label()
        image.add_row("Details", self.image_info)
        self.add_card(image)

        key = Card(
            "Signing key",
            "The bootloader only starts images signed with this key. The station never creates keys.",
        )
        self.key_row = PathRow(
            "P-256 private key (.pem)", "Choose the signing key", "PEM keys (*.pem);;All files (*)"
        )
        key.add_row("Key", self.key_row)
        self.key_chip = chip("No key", "idle")
        self.key_text = value_label()
        key_line = QHBoxLayout()
        key_line.setSpacing(10)
        key_line.addWidget(self.key_chip)
        key_line.addWidget(self.key_text, 1)
        key.add_row("Key id", key_line)
        self.add_card(key)

        options = Card("Options")
        self.set_options = QCheckBox(
            "Set the dual-bank option bytes (nDBANK=0, nDBOOT=1) before writing"
        )
        self.set_options.setChecked(True)
        options.body.addWidget(self.set_options)
        self.flash_button = self.primary("Flash bootloader", "flash")
        self.verify_button = self.secondary("Verify BOOT key", "key")
        options.body.addLayout(self.button_row(self.flash_button, self.verify_button))
        self.add_card(options)
        self.add_activity()

        self.image_row.changed.connect(lambda path: state.remember("boot_image", path))
        self.key_row.changed.connect(lambda path: state.remember("key_path", path))
        self.flash_button.clicked.connect(self.flash)
        self.verify_button.clicked.connect(self.verify_key)
        self.on_state_changed()

    def set_busy(self, busy: bool) -> None:
        self.flash_button.setEnabled(not busy)
        self.verify_button.setEnabled(not busy)

    def on_state_changed(self) -> None:
        self.image_row.set_text(self.state.boot_image)
        self.key_row.set_text(self.state.key_path)
        path = Path(self.state.boot_image) if self.state.boot_image else None
        if path and path.is_file():
            self.image_info.setText(f"{path.name} · {path.stat().st_size:,} bytes")
        else:
            self.image_info.setText("No image chosen")
        self._show_key()

    def _show_key(self) -> None:
        if not self.state.key_path:
            set_chip(self.key_chip, "No key", "idle")
            self.key_text.setText("Choose the key BOOT was built to trust.")
            return
        try:
            info = flashing.inspect_key(self.state.key_path)
        except flashing.FlashError as exc:
            set_chip(self.key_chip, "Invalid", "bad")
            self.key_text.setText(str(exc))
            return
        if info.key_id == DEV_KEY_ID:
            set_chip(self.key_chip, info.key_id_text, "warn")
            self.key_text.setText(
                "Published development key. Use it on the bench only, never in production."
            )
        else:
            set_chip(self.key_chip, info.key_id_text, "ok")
            self.key_text.setText(
                "Private key" if info.private else "Public key only (cannot sign)"
            )

    # -- actions ------------------------------------------------------
    def _check_ready(self) -> str | None:
        if not self.state.boot_image or not Path(self.state.boot_image).is_file():
            return "Choose a bootloader image first."
        if not self.state.simulate and not self.state.probe_serial:
            return "No ST-LINK is selected. Open the Detect page."
        return None

    def flash(self) -> None:
        problem = self._check_ready()
        if problem:
            QMessageBox.warning(self, "Flash bootloader", problem)
            return
        answer = QMessageBox.question(
            self,
            "Flash bootloader",
            "This erases the whole flash of the board and writes the bootloader.\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        state = self.state
        image, probe, options, simulate = (
            state.boot_image,
            state.probe_serial,
            self.set_options.isChecked(),
            state.simulate,
        )

        def job(log: Any, progress: Any) -> str:
            if simulate:
                for step in (
                    "Connecting to the simulated ST-LINK",
                    "Setting dual-bank option bytes (nDBANK=0, nDBOOT=1)"
                    if options
                    else "Keeping the option bytes",
                    "Erasing the flash",
                    f"Writing {Path(image).name}",
                    "Verifying",
                ):
                    log(step)
                    time.sleep(0.25)
                state.sim_boot_flashed = True
                return "simulated"
            flashing.flash_boot(image, probe, log, set_options=options)
            return "ok"

        self.log.line("Flashing the bootloader…")

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Flash bootloader", message)

        self.run_job(job, lambda _r: self.log.line("Bootloader flashed."), failed)

    def verify_key(self) -> None:
        state = self.state
        if not state.key_path:
            QMessageBox.warning(self, "Verify BOOT key", "Choose the signing key first.")
            return
        try:
            wanted = flashing.inspect_key(state.key_path).key_id
        except flashing.FlashError as exc:
            QMessageBox.warning(self, "Verify BOOT key", str(exc))
            return
        if not state.simulate and not state.port:
            QMessageBox.warning(
                self, "Verify BOOT key", "No UART port is selected. Open the Detect page."
            )
            return
        port, simulate = state.port, state.simulate

        def job(log: Any, progress: Any) -> int | None:
            if simulate:
                log("Reading BootInfo from the simulated bootloader")
                return DEV_KEY_ID
            return flashing.boot_key_id(port, log)

        def done(found: object) -> None:
            if not isinstance(found, int):
                self.log.line("The bootloader did not report its key id.")
            elif found == wanted:
                self.log.line(f"The bootloader trusts 0x{wanted:08X}: the chosen key matches.")
            else:
                self.log.line(
                    f"MISMATCH: the bootloader trusts 0x{found:08X}, the chosen key is 0x{wanted:08X}."
                )

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Verify BOOT key", message)

        self.run_job(job, done, failed)


class FirmwarePage(BenchPage):
    title = "Factory firmware"
    subtitle = "Sign the factory firmware, send it through the bootloader into slot A, then reset the board."

    def __init__(self, state: BenchState) -> None:
        super().__init__()
        self.state = state

        image = Card(
            "Factory firmware image",
            "Build it in the factory-firmware repository (scripts/build.sh). The file is build/factory.img.",
        )
        self.image_row = PathRow(
            "factory.img",
            "Choose the factory firmware image",
            "Firmware images (*.img);;All files (*)",
        )
        image.add_row("Image", self.image_row)
        self.add_card(image)

        signing = Card("Signing")
        self.key_row = PathRow(
            "P-256 private key (.pem)", "Choose the signing key", "PEM keys (*.pem);;All files (*)"
        )
        signing.add_row("Key", self.key_row)
        self.counter = QSpinBox()
        self.counter.setRange(0, 65535)
        self.counter.setFixedWidth(120)
        counter_line = QHBoxLayout()
        counter_line.setSpacing(12)
        counter_line.addWidget(self.counter)
        hint = QLabel("Anti-rollback counter. Raise it only for security fixes.")
        hint.setObjectName("Hint")
        counter_line.addWidget(hint, 1)
        signing.add_row("Security counter", counter_line)
        self.add_card(signing)

        run = Card(
            "Flash",
            "The board must be running the bootloader (a blank board after the Bootloader page is).",
        )
        self.result_chip = chip("Not flashed in this session", "idle")
        self.result_text = value_label()
        result_line = QHBoxLayout()
        result_line.setSpacing(10)
        result_line.addWidget(self.result_chip)
        result_line.addWidget(self.result_text, 1)
        run.add_row("Result", result_line)
        self.flash_button = self.primary("Flash factory firmware", "flash")
        run.body.addLayout(self.button_row(self.flash_button))
        self.add_card(run)
        self.add_activity()

        self.image_row.changed.connect(lambda path: state.remember("factory_image", path))
        self.key_row.changed.connect(lambda path: state.remember("key_path", path))
        self.counter.valueChanged.connect(lambda value: state.remember("security_counter", value))
        self.flash_button.clicked.connect(self.flash)
        self.on_state_changed()

    def set_busy(self, busy: bool) -> None:
        self.flash_button.setEnabled(not busy)

    def on_state_changed(self) -> None:
        self.image_row.set_text(self.state.factory_image)
        self.key_row.set_text(self.state.key_path)
        if self.counter.value() != self.state.security_counter:
            self.counter.blockSignals(True)
            self.counter.setValue(self.state.security_counter)
            self.counter.blockSignals(False)

    def flash(self) -> None:
        state = self.state
        if not state.factory_image or not Path(state.factory_image).is_file():
            QMessageBox.warning(
                self, "Flash factory firmware", "Choose the factory firmware image first."
            )
            return
        if not state.key_path:
            QMessageBox.warning(self, "Flash factory firmware", "Choose the signing key first.")
            return
        if not state.simulate and (not state.port or not state.probe_serial):
            QMessageBox.warning(
                self, "Flash factory firmware", "Detect the ST-LINK and the UART port first."
            )
            return
        image, key, counter = state.factory_image, state.key_path, state.security_counter
        port, probe, simulate = state.port, state.probe_serial, state.simulate
        out_dir = Path(tempfile.mkdtemp(prefix="hwval-factory-"))

        def job(log: Any, progress: Any) -> FactoryInfo:
            log("Signing the image")
            signed = flashing.sign_image(
                image, key, out_dir / "factory-signed.img", log, security_counter=counter
            )
            if simulate:
                size = signed.stat().st_size
                log(f"Sending {size:,} bytes through the simulated bootloader into slot A")
                for sent in range(0, size + 1, max(size // 8, 1)):
                    progress(min(sent, size), size)
                    time.sleep(0.05)
                log("Activating slot A and resetting")
                state.sim_device.resets += 1
                time.sleep(0.3)
                info = state.sim_device_info()
                log(f"{info.name} {info.version}, build {info.build}, chip {info.uid_hex}")
                return info
            log("Sending the image through the bootloader (slot A)")
            flashing.flash_application(signed, port, log)
            log("Resetting the board over SWD")
            flashing.swd_reset(probe, log)
            return flashing.wait_for_factory_firmware(port, log)

        def done(info: object) -> None:
            assert isinstance(info, FactoryInfo)
            state.set_info(info)
            set_chip(self.result_chip, "Flashed", "ok")
            self.result_text.setText(f"{info.name} {info.version} is running.")

        def failed(message: str) -> None:
            set_chip(self.result_chip, "Failed", "bad")
            self.result_text.setText(message)

        self.log.line("Flashing the factory firmware…")
        self.run_job(job, done, failed)


class DeviceInfoPage(BenchPage):
    title = "Device info"
    subtitle = "Reset the board and read what the factory firmware reports."

    BLOCKS = (
        ("System info (.sys)", "sys"),
        ("Calibration (.cal)", "cal"),
        ("Diagnostics (.diag)", "diag"),
    )

    def __init__(self, state: BenchState) -> None:
        super().__init__()
        self.state = state

        firmware = Card("Firmware on the board")
        self.status_chip = chip("Not read", "idle")
        self.status_text = value_label()
        status_line = QHBoxLayout()
        status_line.setSpacing(10)
        status_line.addWidget(self.status_chip)
        status_line.addWidget(self.status_text, 1)
        firmware.add_row("Status", status_line)
        self.name_value = value_label()
        self.version_value = value_label()
        self.build_value = value_label()
        self.uid_value = value_label()
        firmware.add_row("Name", self.name_value)
        firmware.add_row("Version", self.version_value)
        firmware.add_row("Build", self.build_value)
        firmware.add_row("Chip UID", self.uid_value)
        self.read_button = self.primary("Reset and read", "reset")
        self.only_read_button = self.secondary("Read only", "info")
        firmware.body.addLayout(self.button_row(self.read_button, self.only_read_button))
        self.add_card(firmware)

        blocks = Card(
            "Factory data on the board", "What each block of the identity sector holds right now."
        )
        self.blocks = QTableWidget(len(self.BLOCKS), 4)
        self.blocks.setHorizontalHeaderLabels(["Block", "Address", "State", "Content"])
        self.blocks.verticalHeader().setVisible(False)
        self.blocks.horizontalHeader().setStretchLastSection(True)
        self.blocks.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.blocks.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.blocks.setAlternatingRowColors(True)
        self.blocks.setMinimumHeight(150)
        for row, (label, kind) in enumerate(self.BLOCKS):
            self.blocks.setItem(row, 0, QTableWidgetItem(label))
            self.blocks.setItem(row, 1, QTableWidgetItem(f"0x{spec.KIND_ADDRESS[kind]:08X}"))
            self.blocks.setItem(row, 2, QTableWidgetItem("—"))
            self.blocks.setItem(row, 3, QTableWidgetItem(""))
        self.blocks.resizeColumnsToContents()
        blocks.body.addWidget(self.blocks)
        self.add_card(blocks)
        self.add_activity()

        self.read_button.clicked.connect(lambda: self.read(reset=True))
        self.only_read_button.clicked.connect(lambda: self.read(reset=False))
        self.on_state_changed()

    def set_busy(self, busy: bool) -> None:
        self.read_button.setEnabled(not busy)
        self.only_read_button.setEnabled(not busy)

    def on_state_changed(self) -> None:
        info = self.state.info
        if info is None:
            return
        self.name_value.setText(info.name)
        self.version_value.setText(info.version)
        self.build_value.setText(info.build)
        self.uid_value.setText(info.uid_hex)
        if info.is_factory_firmware:
            set_chip(self.status_chip, "Factory firmware", "ok")
            self.status_text.setText("The board runs the factory firmware.")
        else:
            set_chip(self.status_chip, "Other firmware", "warn")
            self.status_text.setText(
                "This is not the factory firmware; it cannot take factory data."
            )

    def read(self, *, reset: bool) -> None:
        state = self.state
        if not state.simulate and not state.port:
            QMessageBox.warning(
                self, "Device info", "No UART port is selected. Open the Detect page."
            )
            return
        if reset and not state.simulate and not state.probe_serial:
            QMessageBox.warning(
                self, "Device info", "No ST-LINK is selected, so the board cannot be reset."
            )
            return
        port, probe, simulate = state.port, state.probe_serial, state.simulate

        def job(log: Any, progress: Any) -> tuple[FactoryInfo, list[tuple[str, str]]]:
            if reset:
                log("Resetting the board")
                if simulate:
                    state.sim_device.resets += 1
                    state.sim_device.session = None
                else:
                    flashing.swd_reset(probe, log)
            if simulate:
                link = state.open_link()
                try:
                    info = link.info()
                finally:
                    link.close()
            else:
                info = flashing.wait_for_factory_firmware(port, log, timeout=25.0 if reset else 4.0)
            log(f"{info.name} {info.version}, build {info.build}, chip {info.uid_hex}")
            return info, self._read_blocks(info, log)

        def done(result: object) -> None:
            info, found = cast("tuple[FactoryInfo, list[tuple[str, str]]]", result)
            state.set_info(info)
            for row, (status, content) in enumerate(found):
                for column, text in ((2, status), (3, content)):
                    item = self.blocks.item(row, column)
                    if item is not None:
                        item.setText(text)
            self.blocks.resizeColumnsToContents()

        def failed(message: str) -> None:
            set_chip(self.status_chip, "No answer", "bad")
            self.status_text.setText(message)

        self.run_job(job, done, failed)

    def _read_blocks(self, info: FactoryInfo, log: Any) -> list[tuple[str, str]]:
        if not info.is_factory_firmware:
            return [("unavailable", "")] * len(self.BLOCKS)
        link = self.state.open_link(timeout=1.0)
        out: list[tuple[str, str]] = []
        try:
            for _label, kind in self.BLOCKS:
                try:
                    stored = link.read_file(spec.KIND_ADDRESS[kind])
                except (files.FileFormatError, FactoryLinkError) as exc:
                    out.append(("invalid", str(exc)))
                    continue
                if stored is None:
                    out.append(("blank", ""))
                    continue
                described = files.describe(stored)
                headline = described.get("serial") or "; ".join(
                    f"{k}: {v}" for k, v in list(described.items())[:2]
                )
                out.append(("written", headline))
        finally:
            link.close()
        return out
