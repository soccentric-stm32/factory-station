"""Users, roles, PIN hashing and idle locking (ACC-001…004)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field

from hwval_factory.database import Database
from hwval_factory.models import Role, User


class AuthError(RuntimeError):
    """Authentication or authorization failure."""


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_pin(pin: str, salt: bytes | None = None) -> str:
    if not pin:
        raise AuthError("PIN cannot be empty")
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(
        pin.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def hash_badge(badge: str) -> str:
    return hashlib.sha256(badge.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class SessionInfo:
    user: User
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)

    @property
    def username(self) -> str:
        return self.user.username

    @property
    def role(self) -> Role:
        return self.user.role

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def locked(self, timeout_minutes: int) -> bool:
        return timeout_minutes > 0 and (time.monotonic() - self.last_activity) > timeout_minutes * 60


class AuthService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.session: SessionInfo | None = None

    def ensure_default_admin(self) -> bool:
        if self.db.user("admin") is not None:
            return False
        pin = os.environ.get("HWVAL_ADMIN_PIN", "1234")
        self.add_user("admin", Role.ADMIN, pin, badge="ADMIN")
        self.db.audit("system", "user.create", "default administrator created")
        return True

    def add_user(
        self,
        username: str,
        role: Role | str,
        pin: str,
        badge: str = "",
        active: bool = True,
    ) -> None:
        if not username.strip():
            raise AuthError("username is required")
        role = Role(str(role))
        self.db.add_user(
            username.strip(),
            role.value,
            hash_pin(pin) if pin else "",
            hash_badge(badge) if badge else "",
            active,
        )

    def set_pin(self, username: str, pin: str) -> None:
        row = self.db.user(username)
        if row is None:
            raise AuthError(f"unknown user {username!r}")
        self.db.add_user(
            username,
            str(row["role"]),
            hash_pin(pin),
            str(row["badge"] or ""),
            bool(row["active"]),
        )

    def _row_to_user(self, row: object) -> User:
        try:
            role = Role(str(row["role"]))  # type: ignore[index]
            return User(
                username=str(row["username"]),  # type: ignore[index]
                role=role,
                pin_hash=str(row["pin_hash"]),  # type: ignore[index]
                badge=str(row["badge"] or ""),  # type: ignore[index]
                active=bool(row["active"]),  # type: ignore[index]
            )
        except (KeyError, TypeError) as exc:
            raise AuthError("invalid user record") from exc

    def verify_credentials(self, username: str, pin: str) -> User:
        """Check a PIN without activating a session.

        Supervisor approvals must not replace the operator's signed-in
        session (ACC-002).  This helper is used by approval dialogs; the
        normal :meth:`authenticate_pin` still installs the session.
        """
        row = self.db.user(username)
        if row is None or not bool(row["active"]):
            self.db.audit(username or "unknown", "login.fail", "unknown or inactive user")
            raise AuthError("unknown user or bad PIN")
        user = self._row_to_user(row)
        if not verify_pin(pin, user.pin_hash):
            self.db.audit(username, "login.fail", "bad PIN")
            raise AuthError("unknown user or bad PIN")
        return user

    def authenticate_pin(self, username: str, pin: str) -> SessionInfo:
        user = self.verify_credentials(username, pin)
        self.session = SessionInfo(user=user)
        self.db.audit(username, "login", f"role={user.role.value}")
        return self.session

    def authenticate_badge(self, badge: str) -> SessionInfo:
        row = self.db.user_by_badge(hash_badge(badge))
        if row is None or not bool(row["active"]):
            self.db.audit("badge", "login.fail", "unknown badge")
            raise AuthError("unknown badge")
        user = self._row_to_user(row)
        self.session = SessionInfo(user=user)
        self.db.audit(user.username, "login", f"role={user.role.value} badge")
        return self.session

    def logout(self) -> None:
        if self.session is not None:
            self.db.audit(self.session.username, "logout")
        self.session = None

    def require(self, minimum: Role | str, *, touch: bool = True) -> SessionInfo:
        if self.session is None:
            raise AuthError("not logged in")
        required = Role(str(minimum))
        if self.session.role.rank < required.rank:
            raise AuthError(f"{required.value} permission required")
        if touch:
            self.session.touch()
        return self.session

    def current_user(self) -> User | None:
        return None if self.session is None else self.session.user

    def lock_if_idle(self, timeout_minutes: int) -> bool:
        if self.session is not None and self.session.locked(timeout_minutes):
            username = self.session.username
            self.session = None
            self.db.audit(username, "session.lock", "idle timeout")
            return True
        return False

    def new_secret(self) -> str:
        return secrets.token_urlsafe(24)


def require_role(session: SessionInfo | None, minimum: Role) -> SessionInfo:
    if session is None:
        raise AuthError("not logged in")
    if session.role.rank < minimum.rank:
        raise AuthError(f"{minimum.value} permission required")
    return session
