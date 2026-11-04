"""Command-line entry point (ARCH-006).

``hwval-factory --cli --profile … --fixture …`` runs one unit headless with
exit code 0 PASS, 1 FAIL/ABORTED/INCOMPLETE and 2 station error.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import signal
import sys
from pathlib import Path
from typing import Any

from hwval_factory import APP_NAME, __version__
from hwval_factory.auth import AuthService
from hwval_factory.config import (
    ConfigError,
    default_station,
    detect_stlink_serials,
    detect_vcp_ports,
    load_station_config,
)
from hwval_factory.database import Database, row_dict
from hwval_factory.labels import LabelError
from hwval_factory.models import Role, RunKind
from hwval_factory.profile import ProductProfile, ProfileError, list_profiles, load_profile
from hwval_factory.reports import certificate_pdf, export_csv, export_json
from hwval_factory.station import Station, StationError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hwval-factory",
        description=f"{APP_NAME} — STM32F767ZI end-of-line test and provisioning",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--cli", action="store_true", help="run one fixture headless")
    parser.add_argument("--station", type=Path, help="station.yaml")
    parser.add_argument("--profile", help="profile directory, archive or ID")
    parser.add_argument("--profiles-dir", type=Path, help="override profile store")
    parser.add_argument("--fixture", default="", help="fixture name (e.g. F1)")
    parser.add_argument("--pcb-serial", default="", help="board barcode (otherwise stdin)")
    parser.add_argument("--operator", default="operator")
    parser.add_argument("--work-order", default="")
    parser.add_argument("--work-order-quantity", type=int, default=10000)
    parser.add_argument("--serial-start", type=int, default=1)
    parser.add_argument("--serial-end", type=int, default=0)
    parser.add_argument("--kind", choices=[k.value.lower() for k in RunKind], default="first")
    parser.add_argument("--approve-retest", action="store_true")
    parser.add_argument("--approve-rework", action="store_true")
    parser.add_argument("--hw-revision", default="")
    parser.add_argument("--simulate", action="store_true", help="force the fixture simulator")
    parser.add_argument("--no-simulate", action="store_true", help="force real hardware")
    parser.add_argument(
        "--fail-test", action="append", default=[], help="simulate a failing test ID"
    )
    parser.add_argument("--fault-rate", type=float, default=0.0, help="simulated USB fault rate")
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--list-fixtures", action="store_true")
    parser.add_argument("--detect", action="store_true", help="list ST-LINK probes and VCP ports")
    parser.add_argument(
        "--probe", action="store_true", help="hardware bring-up: SWD + VCP checks for one fixture"
    )
    parser.add_argument("--self-test", action="store_true", help="run station self-test")
    parser.add_argument("--reprint", type=int, metavar="RUN_ID", help="reprint a label")
    parser.add_argument("--export", choices=["csv", "json"], help="export runs and exit")
    parser.add_argument("--certificate", type=int, metavar="RUN_ID")
    parser.add_argument("--output", type=Path, help="export/certificate output path")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--json", action="store_true", help="JSON result on stdout")
    parser.add_argument("--kiosk", action="store_true", help="start the UI in kiosk mode")
    parser.add_argument("--fixtures", type=int, default=4, help="simulated fixture count")
    return parser


def _load_config(args: argparse.Namespace) -> tuple[Any, bool]:
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
    if args.profiles_dir:
        config.profiles_dir = args.profiles_dir
    return config, simulation


def _resolve_profile(args: argparse.Namespace, config: Any) -> ProductProfile:
    if args.profile:
        candidate = Path(args.profile)
        if candidate.exists():
            return load_profile(
                candidate,
                require_signature=config.require_profile_signature,
                public_key=config.profile_public_key or None,
            )
        inside = config.profiles_dir / args.profile
        if inside.exists():
            return load_profile(
                inside,
                require_signature=config.require_profile_signature,
                public_key=config.profile_public_key or None,
            )
        profiles = list_profiles(
            config.profiles_dir,
            require_signature=config.require_profile_signature,
            public_key=config.profile_public_key or None,
        )
        matches = [
            p
            for p in profiles
            if p.profile_id == args.profile
            or f"{p.profile_id}@{p.version}" == args.profile
            or p.path.name == args.profile
        ]
        if len(matches) == 1:
            return matches[0]
        raise ProfileError(f"profile {args.profile!r} not found")
    profiles = list_profiles(
        config.profiles_dir,
        require_signature=config.require_profile_signature,
        public_key=config.profile_public_key or None,
    )
    if profiles:
        return sorted(profiles, key=lambda p: p.version)[-1]
    demo = Path("examples/profile-sc767-demo")
    if demo.is_dir():
        return load_profile(demo)
    raise ProfileError("no profile selected and no profile found in the profile store")


def _print_result(db: Database, run_id: int, fixture: str) -> None:
    run = db.run(run_id)
    if run is None:
        print(f"run {run_id} not found", file=sys.stderr)
        return
    unit = db.get_unit(str(run["uid"])) if run["uid"] else None
    payload: dict[str, Any] = {
        "run": row_dict(run),
        "unit": None if unit is None else row_dict(unit),
        "steps": [row_dict(row) for row in db.steps(run_id)],
        "tests": [row_dict(row) for row in db.tests(run_id)],
        "records": [
            {
                "uid": row["uid"],
                "revision": row["revision"],
                "written_at": row["written_at"],
                "blob_hex": bytes(row["blob"]).hex(),
            }
            for row in db.records_for_uid(str(run["uid"]))
        ]
        if run["uid"]
        else [],
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _run_one(args: argparse.Namespace) -> int:
    config, simulation = _load_config(args)
    db = Database(config.database)
    db.initialize()
    auth = AuthService(db)
    auth.ensure_default_admin()
    profile = _resolve_profile(args, config)
    fixture = args.fixture or next(f.name for f in config.fixtures if f.enabled)
    if not any(f.name == fixture for f in config.fixtures):
        raise StationError(f"fixture {fixture!r} is not configured")
    station = Station(
        config,
        db,
        profile,
        simulation=simulation,
        fail_tests=set(args.fail_test),
        fault_rate=args.fault_rate,
    )
    try:
        if args.work_order:
            station.select_work_order(
                args.work_order,
                quantity=args.work_order_quantity,
                serial_start=args.serial_start,
                serial_end=args.serial_end,
                user=args.operator,
            )
        else:
            station.auto_work_order(quantity=args.work_order_quantity, user=args.operator)
        kind = RunKind(args.kind.upper())
        run_id = station.start_run(
            fixture,
            pcb_serial=args.pcb_serial,
            operator=args.operator,
            kind=kind,
            retest_approved=args.approve_retest,
            rework_approved=args.approve_rework,
            hw_revision=args.hw_revision,
        )
        if not args.pcb_serial:
            print("scan board barcode: ", end="", file=sys.stderr, flush=True)
            scanned = input().strip()
            if scanned:
                station.submit_scan(scanned, fixture)
        old_handler = signal.getsignal(signal.SIGINT)

        def _on_sigint(signum: int, frame: object) -> None:
            station.abort(fixture)

        signal.signal(signal.SIGINT, _on_sigint)
        try:
            result = station.wait(fixture)
        finally:
            signal.signal(signal.SIGINT, old_handler)
        if args.json:
            _print_result(db, run_id, fixture)
        else:
            run = db.run(run_id)
            print(
                f"{result}: run {run_id}, fixture {fixture}, "
                f"serial {(run['uid'] if run else '') or ''}"
            )
            if run is not None and run["reason"]:
                print(f"reason: {run['reason']}")
            for step in db.steps(run_id):
                duration = "" if step["duration_ms"] is None else f" {int(step['duration_ms'])} ms"
                print(f"  {int(step['seq']):02d} {step['name']}: {step['result']}{duration}")
            for test in db.tests(run_id):
                value = ""
                if test["measured_value"] is not None:
                    value = f" {test['measured_value']} {test['measured_unit'] or ''}"
                print(f"     {test['test_id']}: {test['result']}{value}")
        return 0 if result == "PASS" else 1
    finally:
        station.close()
        db.close()


def _run_probe(config: Any, fixture_name: str) -> int:
    from hostcore.hwlink import HwLinkError, HwLinkSession, SerialTransport
    from hostcore.openocd import OpenOcdError, OpenOcdProcess, option_bytes_to_dict

    fixture = config.fixture(fixture_name)
    failures = 0

    def report(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures += 1

    report("probe configured", bool(fixture.probe_serial), fixture.probe_serial or "missing")
    backend = OpenOcdProcess(
        fixture.probe_serial,
        openocd=config.openocd.binary,
        interface_cfg=config.openocd.interface_cfg,
        target_cfg=config.openocd.target_cfg,
        telnet_port=fixture.telnet_port,
    )
    try:
        backend.start()
        report("OpenOCD start", True, f"telnet {fixture.telnet_port}")
        backend.halt()
        idcode = backend.read_u32(0xE0042000)
        dev_id = idcode & 0xFFF
        rev_id = (idcode >> 16) & 0xFFFF
        report("IDCODE", dev_id != 0, f"DEV_ID=0x{dev_id:03X} REV_ID=0x{rev_id:04X}")
        flash_kb = backend.read_u16(0x1FF0F442)
        report("flash size", flash_kb > 0, f"{flash_kb} KB")
        uid = backend.read_memory(0x1FF0F420, 12)
        report("UID", len(uid) == 12, uid.hex().upper())
        options = backend.read_option_bytes()
        report("option bytes", True, str(option_bytes_to_dict(options)))
        backend.stop()
    except (OpenOcdError, OSError) as exc:
        report("SWD", False, str(exc))
        with contextlib.suppress(Exception):
            backend.stop()
    try:
        transport = (
            SerialTransport(fixture.serial_port)
            if fixture.serial_port
            else SerialTransport.find_by_probe_serial(fixture.probe_serial)
        )
    except HwLinkError as exc:
        report("VCP open", False, str(exc))
        return 1 if failures else 0
    session = HwLinkSession(transport, timeout=5.0)
    try:
        hello = session.connect(timeout=10.0)
        report(
            "HWLINK Hello",
            True,
            f"role={hello.get('role')} fw={hello.get('firmware_name')} "
            f"version={hello.get('version')} uid={hello.get('uid_hex')}",
        )
        if str(hello.get("role")) == "HWVAL":
            info = session.device_info()
            report("GetDeviceInfo", True, f"uid={info.get('uid')} optcr=0x{info.get('optcr', 0):08X}")
        elif str(hello.get("role")) in ("DEMO", "APP"):
            status = session.status()
            report(
                "GetStatus",
                True,
                f"slot={status.get('slot_name')} trial={status.get('trial_name')}",
            )
        else:
            boot = session.boot_info()
            report("GetBootInfo", True, f"active={boot.get('active_slot_name')}")
    except HwLinkError as exc:
        report("HWLINK Hello", False, str(exc))
    finally:
        session.close()
    return 0 if failures == 0 else 1


def _run_utility(args: argparse.Namespace) -> int:
    config, _simulation = _load_config(args)
    db = Database(config.database)
    db.initialize(mark_interrupted=False)
    try:
        if args.list_profiles:
            profiles = list_profiles(
                config.profiles_dir,
                require_signature=config.require_profile_signature,
                public_key=config.profile_public_key or None,
            )
            for profile in profiles:
                print(f"{profile.profile_id}\t{profile.version}\t{profile.product}\t{profile.path}")
            return 0
        if args.list_fixtures:
            for fixture in config.fixtures:
                print(f"{fixture.name}\t{fixture.probe_serial}\t{fixture.serial_port}")
            return 0
        if args.detect:
            print("ST-LINK probes:")
            probes = detect_stlink_serials()
            for probe in probes:
                print(f"  {probe}")
            if not probes:
                print("  (none)")
            print("VCP ports:")
            ports = detect_vcp_ports()
            for port in ports:
                print(f"  {port['device']}\t{port['serial']}\t{port['description']}")
            if not ports:
                print("  (none)")
            return 0
        if args.probe:
            fixture_name = args.fixture or next(f.name for f in config.fixtures if f.enabled)
            return _run_probe(config, fixture_name)
        if args.export:
            target = args.output or Path(f"export-{args.work_order or 'all'}.{args.export}")
            if args.export == "csv":
                print(
                    export_csv(
                        db,
                        target,
                        work_order=args.work_order,
                        date_from=args.date_from,
                        date_to=args.date_to,
                    )
                )
            else:
                print(
                    export_json(
                        db,
                        target,
                        work_order=args.work_order,
                        date_from=args.date_from,
                        date_to=args.date_to,
                    )
                )
            return 0
        if args.certificate is not None:
            target = args.output or Path(f"certificate-{args.certificate}.pdf")
            print(certificate_pdf(db, args.certificate, target))
            return 0
        if args.reprint is not None:
            from hwval_factory.labels import LabelService

            row = db.user(args.operator)
            if row is None or Role(str(row["role"])).rank < Role.SUPERVISOR.rank:
                print(
                    "station error: reprinting a label requires a supervisor user "
                    "(LBL-004); pass --operator <supervisor-or-admin>",
                    file=sys.stderr,
                )
                return 2
            profile = _resolve_profile(args, config)
            service = LabelService(db, config.printer, labels_dir=config.labels_dir)
            result = service.reprint(run_id=args.reprint, user=args.operator)
            print(result.status)
            for path in result.output_paths:
                print(path)
            return 0
        if args.self_test:
            profile = _resolve_profile(args, config)
            station = Station(config, db, profile, simulation=config.simulation)
            try:
                fixtures = [args.fixture] if args.fixture else station.fixture_names()
                ok = all(station.run_self_test(name, args.operator) for name in fixtures)
            finally:
                station.close()
            return 0 if ok else 1
        return -1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    utility = (
        args.list_profiles
        or args.list_fixtures
        or args.detect
        or args.probe
        or args.export
        or args.certificate is not None
        or args.reprint is not None
        or args.self_test
    )
    try:
        if utility:
            code = _run_utility(args)
            if code >= 0:
                return code
        if not args.cli and not utility:
            from hwval_factory.app import main as ui_main

            return int(ui_main(argv))
        return _run_one(args)
    except (ConfigError, ProfileError, StationError, LabelError) as exc:
        print(f"station error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"station error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
