"""Relative paths for the GUI.

The app is launched from its own folder, so every path it creates, stores or
shows is kept relative to that folder.  A relative path is resolved against
:data:`APP_DIR`, not against whatever directory the process happens to be
started from.  Paths outside the app folder are still made relative (they get
``..`` components), so settings, recent files and the output log never contain
an absolute path.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The factory-station project root (src/hwval_factory/core is three levels deep).
APP_DIR = Path(__file__).resolve().parents[3]


def resolve(path: str | Path, base: str | Path | None = None) -> Path:
    """Turn a relative path into an absolute one for file and git access."""
    value = Path(path).expanduser()
    if value.is_absolute():
        return Path(os.path.abspath(value))
    root = Path(base) if base is not None else APP_DIR
    return Path(os.path.abspath(root / value))


def _absolute(path: str | Path) -> Path:
    value = Path(path).expanduser()
    return value if value.is_absolute() else resolve(value)


def relative(path: str | Path, base: str | Path | None = None) -> Path:
    """Return ``path`` relative to the app folder (or ``base``)."""
    value = _absolute(path)
    root = _absolute(base) if base is not None else APP_DIR
    try:
        return value.relative_to(root)
    except ValueError:
        return Path(os.path.relpath(value, root))


def display(path: str | Path, base: str | Path | None = None) -> str:
    """The relative form shown in the UI and written to QSettings."""
    shown = relative(path, base).as_posix()
    return shown or "."


def shown(path: str | Path, base: str | Path | None = None) -> Path:
    """A relative ``Path`` for UI fields; use :func:`resolve` before I/O."""
    return relative(path, base)


def message(error: BaseException | str, *known: str | Path) -> str:
    """Turn absolute paths in an OS or git error into relative ones."""
    text = str(error)
    for path in known:
        absolute = str(resolve(path))
        if absolute in text:
            text = text.replace(absolute, display(path))
    return text
