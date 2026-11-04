"""PySide6 application entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from hostcore.theme import apply_theme
from hwval_factory import APP_NAME, __version__
from hwval_factory.auth import AuthError, AuthService
from hwval_factory.config import ConfigError, default_station, load_station_config
from hwval_factory.database import Database
from hwval_factory.profile import ProductProfile, ProfileError, list_profiles, load_profile
from hwval_factory.station import Station
from hwval_factory.ui.login import LoginDialog
from hwval_factory.ui.main_window import StationWindow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwval-factory-ui", description=APP_NAME)
    parser.add_argument("--station", type=Path, help="station.yaml")
    parser.add_argument("--profile", help="profile directory, archive or ID")
    parser.add_argument("--fixtures", type=int, default=4, help="simulated fixture count")
    parser.add_argument("--simulate", action="store_true", help="force the simulator")
    parser.add_argument("--no-simulate", action="store_true", help="force real hardware")
    parser.add_argument("--kiosk", action="store_true", help="start in kiosk mode")
    parser.add_argument("--fail-test", action="append", default=[])
    parser.add_argument("--fault-rate", type=float, default=0.0)
    parser.add_argument("--auto-login", default="", help="username:pin (development only)")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _resolve_profile(
    args: argparse.Namespace,
    profiles_dir: Path,
    require_signature: bool = False,
    public_key: str | None = None,
) -> ProductProfile:
    if args.profile:
        candidate = Path(args.profile)
        if candidate.exists():
            return load_profile(
                candidate, require_signature=require_signature, public_key=public_key
            )
        inside = profiles_dir / args.profile
        if inside.exists():
            return load_profile(
                inside, require_signature=require_signature, public_key=public_key
            )
        raise ProfileError(f"profile {args.profile!r} not found")
    profiles = list_profiles(
        profiles_dir,
        require_signature=require_signature,
        public_key=public_key,
    )
    if profiles:
        return sorted(profiles, key=lambda p: p.version)[-1]
    demo = Path("examples/profile-sc767-demo")
    if demo.is_dir():
        return load_profile(
            demo, require_signature=require_signature, public_key=public_key
        )
    raise ProfileError(
        "no product profile found. Import one with Tools → Profiles, or pass --profile."
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_known_args(argv)[0]
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("SoCcentric")
    app.setOrganizationDomain("soccentric.com")
    apply_theme(app)
    try:
        if args.station:
            config = load_station_config(args.station)
        else:
            config = default_station(simulated=True, fixture_count=args.fixtures)
        simulation = config.simulation
        if args.simulate:
            simulation = True
        if args.no_simulate:
            simulation = False
        config.simulation = simulation
        db = Database(config.database)
        db.initialize()
        auth = AuthService(db)
        created = auth.ensure_default_admin()
        profile = _resolve_profile(
            args,
            config.profiles_dir,
            config.require_profile_signature,
            config.profile_public_key or None,
        )
        station = Station(
            config,
            db,
            profile,
            simulation=simulation,
            fail_tests=set(args.fail_test),
            fault_rate=args.fault_rate,
        )
    except (ConfigError, ProfileError, AuthError, OSError) as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 2
    if created:
        QMessageBox.information(
            None,
            APP_NAME,
            "A default administrator was created:\n\n"
            "  user: admin\n  PIN:  1234\n\n"
            "Change it in Tools → Change PIN before production use.",
        )
    session = None
    if args.auto_login:
        username, _, pin = args.auto_login.partition(":")
        try:
            session = auth.authenticate_pin(username, pin)
        except AuthError as exc:
            QMessageBox.critical(None, APP_NAME, f"Auto-login failed: {exc}")
            station.close()
            return 2
    else:
        dialog = LoginDialog(auth, None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            station.close()
            db.close()
            return 0
        session = dialog.session
    if session is None:
        station.close()
        db.close()
        return 1
    window = StationWindow(config, db, auth, station, profile, kiosk=args.kiosk)
    window.show()
    app.aboutToQuit.connect(station.close)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
