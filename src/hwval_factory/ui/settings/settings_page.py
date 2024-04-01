"""Settings: artifacts repository, images, signing key and simulation."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SettingsPage(QWidget):
    def __init__(self, state: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)
        self._form = form
        self.artifacts = self._path_row("Artifacts repository", self._choose_artifacts)
        self.scope = QLineEdit()
        self.scope.setPlaceholderText("products/<product>/<batch> (empty for the flat layout)")
        form.addRow("Scope", self.scope)
        self.unit = QLineEdit()
        form.addRow("Default unit", self.unit)
        self.key = self._path_row("Signing key", lambda: self._choose_file(self.key, "PEM (*.pem)"))
        self.boot = self._path_row("BOOT image", lambda: self._choose_file(self.boot, "ELF/HEX (*.elf *.hex)"))
        self.factory = self._path_row(
            "Factory image", lambda: self._choose_file(self.factory, "Images (*.img *.bin)")
        )
        self.simulate = QCheckBox("Start with the simulated board")
        form.addRow("", self.simulate)
        outer.addLayout(form)

        self.resolved = QLabel("")
        self.resolved.setObjectName("Hint")
        self.resolved.setWordWrap(True)
        outer.addWidget(self.resolved)
        outer.addStretch(1)

        row = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        row.addWidget(save)
        row.addStretch(1)
        outer.addLayout(row)

    def _path_row(self, label: str, browse) -> QLineEdit:
        edit = QLineEdit()
        button = QPushButton("…")
        button.setFixedWidth(32)
        button.clicked.connect(browse)
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        self._form.addRow(label, host)
        return edit

    def refresh(self) -> None:
        state = self.state
        self.artifacts.setText(str(state.artifacts_root))
        self.scope.setText(str(state.scope or ""))
        self.unit.setText(str(state.unit_id or ""))
        self.key.setText(str(state.key_path or ""))
        self.boot.setText(str(state.boot_image or ""))
        self.factory.setText(str(state.factory_image or ""))
        self.simulate.setChecked(bool(state.simulate))
        self.resolved.setText(
            f"CSV: {state.workspace.csv_dir_path}\nOutput: {state.workspace.out_dir_path}\n"
            f"Registry: {state.workspace.units_path}"
        )

    def _choose_artifacts(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Artifacts repository", self.artifacts.text())
        if path:
            self.artifacts.setText(path)

    def _choose_file(self, edit: QLineEdit, filt: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", edit.text(), filt)
        if path:
            edit.setText(path)

    def _save(self) -> None:
        state = self.state
        state.set_artifacts_root(self.artifacts.text().strip() or state.artifacts_root)
        state.set_scope(self.scope.text().strip())
        state.set_unit(self.unit.text().strip())
        for name, edit in (
            ("key_path", self.key),
            ("boot_image", self.boot),
            ("factory_image", self.factory),
        ):
            state.remember(name, edit.text().strip())
        if self.simulate.isChecked() != state.simulate:
            state.set_simulate(self.simulate.isChecked())
        self.refresh()
        QMessageBox.information(self, "Settings", "Saved")


__all__ = ["SettingsPage"]
