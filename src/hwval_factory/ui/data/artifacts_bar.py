"""Artifacts repository bar: branch, change count, Pull, Commit, Push."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from hwval_factory.core.workspace import WorkspaceError, have_git


class ArtifactsBar(QFrame):
    def __init__(self, state: Any, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setObjectName("ArtifactsBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 6, 22, 6)
        layout.setSpacing(8)
        self.label = QLabel("Artifacts: …")
        self.label.setObjectName("Hint")
        layout.addWidget(self.label)
        layout.addStretch(1)
        self.pull_button = QPushButton("Pull")
        self.pull_button.clicked.connect(self.pull)
        self.commit_button = QPushButton("Commit…")
        self.commit_button.clicked.connect(self.commit)
        self.push_button = QPushButton("Push")
        self.push_button.clicked.connect(self.push)
        layout.addWidget(self.pull_button)
        layout.addWidget(self.commit_button)
        layout.addWidget(self.push_button)
        if not have_git():
            for button in (self.pull_button, self.commit_button, self.push_button):
                button.setEnabled(False)
            self.label.setText("Artifacts: git is not installed")
        self.refresh()

    def refresh(self) -> None:
        try:
            status = self.state.workspace.status()
        except WorkspaceError as exc:
            self.label.setText(f"Artifacts: {exc}")
            return
        self.label.setText(f"Artifacts: {status.summary()}")

    def _run(self, action, *args) -> None:
        try:
            text = action(*args)
        except WorkspaceError as exc:
            QMessageBox.warning(self, "Artifacts", str(exc))
        else:
            if text:
                QMessageBox.information(self, "Artifacts", text)
        self.refresh()

    def pull(self) -> None:
        self._run(self.state.workspace.sync)

    def commit(self) -> None:
        message, ok = QInputDialog.getText(self, "Commit artifacts", "Commit message")
        if not ok or not message.strip():
            return
        self._run(self.state.workspace.commit, message)

    def push(self) -> None:
        answer = QMessageBox.question(
            self, "Push artifacts", "Push the artifacts repository to origin?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run(self.state.workspace.push)


__all__ = ["ArtifactsBar"]
