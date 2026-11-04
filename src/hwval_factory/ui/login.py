"""Login dialog: badge scan or user ID + PIN (ACC-001)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hostcore.theme import COLORS, icon
from hwval_factory.auth import AuthError, AuthService, SessionInfo
from hwval_factory.models import Role


class LoginDialog(QDialog):
    def __init__(self, auth: AuthService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.session: SessionInfo | None = None
        self.setWindowTitle("HWVAL Factory — sign in")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        title = QLabel("HWVAL Factory")
        title.setObjectName("Title")
        subtitle = QLabel("Sign in with your badge or operator ID and PIN.")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._badge_tab(), "Badge")
        self.tabs.addTab(self._pin_tab(), "ID and PIN")
        layout.addWidget(self.tabs)

        self.message = QLabel("")
        self.message.setStyleSheet(f"color: {COLORS['fail']};")
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.badge_input.setFocus()

    def _badge_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)
        label = QLabel("Scan your badge")
        label.setObjectName("Heading")
        self.badge_input = QLineEdit()
        self.badge_input.setPlaceholderText("Badge code")
        self.badge_input.setClearButtonEnabled(True)
        self.badge_input.returnPressed.connect(self._login_badge)
        button = QPushButton(icon("user", "white", 18), "Sign in")
        button.setObjectName("Primary")
        button.clicked.connect(self._login_badge)
        layout.addWidget(label)
        layout.addWidget(self.badge_input)
        layout.addWidget(button)
        layout.addStretch(1)
        return page

    def _pin_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 18, 14, 14)
        form = QFormLayout()
        self.user_input = QComboBox()
        self.user_input.setEditable(True)
        users = self.auth.db.users()
        for row in users:
            if bool(row["active"]):
                self.user_input.addItem(str(row["username"]))
        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin_input.setPlaceholderText("PIN")
        self.pin_input.returnPressed.connect(self._login_pin)
        form.addRow("User", self.user_input)
        form.addRow("PIN", self.pin_input)
        button = QPushButton(icon("lock", "white", 18), "Sign in")
        button.setObjectName("Primary")
        button.clicked.connect(self._login_pin)
        hint = QLabel("Default administrator: admin / 1234 — change it in Tools → Users.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addLayout(form)
        layout.addWidget(button)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _login_badge(self) -> None:
        badge = self.badge_input.text().strip()
        if not badge:
            return
        try:
            self.session = self.auth.authenticate_badge(badge)
        except AuthError as exc:
            self._fail(str(exc))
            return
        self.accept()

    def _login_pin(self) -> None:
        username = self.user_input.currentText().strip()
        pin = self.pin_input.text()
        try:
            self.session = self.auth.authenticate_pin(username, pin)
        except AuthError as exc:
            self._fail(str(exc))
            return
        self.accept()

    def _fail(self, text: str) -> None:
        self.message.setText(text)
        self.pin_input.clear()
        self.badge_input.selectAll()
        QMessageBox.warning(self, "Sign in failed", text)


class SupervisorDialog(QDialog):
    """Supervisor PIN check used for retest, rework, reprint and unlocks."""

    def __init__(self, auth: AuthService, action: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.action = action
        self.approved = False
        self.setWindowTitle("Supervisor approval")
        self.setModal(True)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        title = QLabel("Supervisor approval required")
        title.setObjectName("Heading")
        text = QLabel(action)
        text.setWordWrap(True)
        self.user = QComboBox()
        self.user.setEditable(True)
        for row in auth.db.users():
            if bool(row["active"]) and Role(str(row["role"])).rank >= Role.SUPERVISOR.rank:
                self.user.addItem(str(row["username"]))
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setPlaceholderText("Supervisor PIN")
        self.pin.returnPressed.connect(self._approve)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._approve)
        buttons.rejected.connect(self.reject)
        layout.addWidget(title)
        layout.addWidget(text)
        form = QFormLayout()
        form.addRow("Supervisor", self.user)
        form.addRow("PIN", self.pin)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._message = QLabel("")
        self._message.setStyleSheet(f"color: {COLORS['fail']};")
        layout.addWidget(self._message)

    def _approve(self) -> None:
        try:
            session = self.auth.authenticate_pin(
                self.user.currentText().strip(), self.pin.text()
            )
        except AuthError as exc:
            self._message.setText(str(exc))
            return
        if session.role.rank < Role.SUPERVISOR.rank:
            self._message.setText("this user is not a supervisor")
            return
        self.approved = True
        self.accept()


class PinChangeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change PIN")
        self.setModal(True)
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("New PIN", self.pin)
        form.addRow("Confirm", self.confirm)

    def value(self) -> str:
        if self.pin.text() != self.confirm.text():
            QMessageBox.warning(self, "PIN", "PINs do not match")
            return ""
        if len(self.pin.text()) < 4:
            QMessageBox.warning(self, "PIN", "Use at least 4 characters")
            return ""
        return self.pin.text()


def ensure_session(auth: AuthService, parent: QWidget | None = None) -> SessionInfo | None:
    dialog = LoginDialog(auth, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.session


__all__ = ["LoginDialog", "PinChangeDialog", "SupervisorDialog", "ensure_session"]
