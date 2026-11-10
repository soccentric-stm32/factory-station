"""PySide6 application entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from hostcore.theme import apply_theme
from hwval_factory import APP_NAME, __version__
from hwval_factory.ui.main_window import BenchWindow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwval-factory", description=APP_NAME)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("station-data"),
        help="folder for the factory database CSV and other bench files",
    )
    parser.add_argument("--simulate", action="store_true", help="start with a simulated board")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SoCcentric")
    app.setOrganizationDomain("soccentric.com")
    apply_theme(app)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    window = BenchWindow(args.data_dir.resolve(), simulate=args.simulate)
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
