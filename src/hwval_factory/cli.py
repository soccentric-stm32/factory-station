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

import yaml

from hwval_factory import APP_NAME, __version__
from hwval_factory.auth import AuthService
from hwval_factory.config import (
    ConfigError,
    default_station,
    detect_stlink_firmware,
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
from hwval_factory.tools import ToolError


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
    parser.add_argument(
        "--scenario", type=Path, help="simulator scenario YAML (fail_tests, fault_rate, prompts)"
    )
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--list-fixtures", action="store_true")
    parser.add_argument("--detect", action="store_true", help="list ST-LINK probes and VCP ports")
    parser.add_argument(
        "--init-station", type=Path, metavar="PATH", help="write a first-run station.yaml"
    )
    parser.add_argument(
        "--import-macs", type=Path, metavar="CSV", help="load a MAC pool CSV for a work order"
    )
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

    commands = parser.add_subparsers(dest="command")
    tool_root = commands.add_parser("tool", help="run one standalone station tool")
    tool = tool_root.add_subparsers(dest="tool_command", metavar="TOOL", required=True)
    tool_help = {
        "probe": "SWD + VCP + Hello, step by step",
        "identify": "IDs, UID, option bytes, RDP, anti-rollback floor",
        "erase": "mass erase (refused at RDP > 0)",
        "program-boot": "program and verify the profile BOOT image",
        "program-factory": "program and verify the profile factory HEX",
        "load-app": "load an application image over SWD or BOOT",
        "test": "run the profile test plan (or selected tests)",
        "identity": "read, write or verify the identity record",
        "boot-check": "reset and prove the application starts",
        "protect": "apply BOR/WRP/RDP from the profile",
        "recover": "catch BOOT, end a trial, lower RDP 1 or connect-under-reset",
        "capture-log": "capture the fixture debug UART",
    }
    for name, help_text in tool_help.items():
        sub = tool.add_parser(name, help=help_text)
        sub.add_argument("--fixture", required=True)
        sub.add_argument("--profile", default="")
        sub.add_argument("--station", type=Path)
        sub.add_argument("--profiles-dir", type=Path)
        sub.add_argument("--simulate", action="store_true")
        sub.add_argument("--no-simulate", action="store_true")
        sub.add_argument("--operator", default="operator")
        sub.add_argument("--json", action="store_true")
        if name == "erase":
            sub.add_argument("--confirm", action="store_true")
        elif name == "program-factory":
            pass
        elif name == "load-app":
            sub.add_argument("image", nargs="?", default="")
            sub.add_argument("--via", choices=["boot", "swd"], default="swd")
            sub.add_argument("--no-activate", action="store_true")
        elif name == "test":
            sub.add_argument("--tests", nargs="+", default=None)
            sub.add_argument("--timeout", type=float, default=180.0)
        elif name == "identity":
            sub.add_argument("action", choices=["read", "write", "verify"])
            sub.add_argument("--serial", default="")
            sub.add_argument("--mac", default="")
            sub.add_argument("--hw-revision", default="")
        elif name == "recover":
            sub.add_argument(
                "action",
                nargs="?",
                choices=["catch", "unstick", "unlock-rdp", "connect"],
                default="catch",
            )
            sub.add_argument("--reason", default="")
            sub.add_argument("--confirm", action="store_true")
            sub.add_argument("--timeout", type=float, default=10.0)
        elif name == "capture-log":
            sub.add_argument("--port", default="")
            sub.add_argument("--seconds", type=float, default=10.0)
    return parser


def _load_config(args: argparse.Namespace) -> tuple[Any, bool]:
    if args.station:
        config = load_station_config(args.station)
    else:
        config = default_station(simulated=True, fixture_count=getattr(args, "fixtures", 1))
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


def _simulation_inputs(args: argparse.Namespace) -> tuple[set[str], float, set[str]]:
    fail_tests = set(args.fail_test)
    prompt_tests: set[str] = set()
    fault_rate = args.fault_rate
    scenario = getattr(args, "scenario", None)
    if scenario is not None:
        if not scenario.is_file():
            raise ConfigError(f"scenario not found: {scenario}")
        raw = yaml.safe_load(scenario.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError("scenario must be a mapping")
        sim_raw = raw.get("simulation")
        sim: dict[str, Any] = sim_raw if isinstance(sim_raw, dict) else raw
        for item in sim.get("fail_tests") or []:
            fail_tests.add(str(item))
        for item in sim.get("prompt_tests") or []:
            prompt_tests.add(str(item))
        if sim.get("fault_rate") is not None:
            fault_rate = float(sim["fault_rate"])
    return fail_tests, fault_rate, prompt_tests


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
    fail_tests, fault_rate, prompt_tests = _simulation_inputs(args)
    station = Station(
        config,
        db,
        profile,
        simulation=simulation,
        fail_tests=fail_tests,
        fault_rate=fault_rate,
        prompt_tests=prompt_tests,
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
        if args.import_macs is not None:
            if not args.work_order:
                print("station error: --import-macs requires --work-order", file=sys.stderr)
                return 2
            from hwval_factory.serials import format_mac, parse_mac

            macs: list[str] = []
            for line in args.import_macs.read_text(encoding="utf-8").splitlines():
                text = line.split("#", 1)[0].strip()
                if not text:
                    continue
                value = text.replace(",", " ").split()[0]
                if value.lower() in ("mac", "address"):
                    continue
                try:
                    number = parse_mac(value)
                except Exception as exc:
                    print(f"station error: {line!r}: {exc}", file=sys.stderr)
                    return 2
                if number & 0x010000000000:
                    print(f"station error: {value} is multicast", file=sys.stderr)
                    return 2
                macs.append(format_mac(number))
            if len(macs) != len(set(macs)):
                print("station error: MAC CSV contains duplicates", file=sys.stderr)
                return 2
            inserted = db.add_macs(args.work_order, macs)
            skipped = len(macs) - inserted
            print(
                f"{inserted} MAC addresses loaded for {args.work_order}"
                + (f" ({skipped} already present)" if skipped else "")
            )
            return 0
        if args.init_station:
            probes = detect_stlink_serials()
            fixture_rows: list[dict[str, Any]] = []
            for index in range(max(1, min(8, args.fixtures))):
                fixture_rows.append(
                    {
                        "name": f"F{index + 1}",
                        "probe_serial": probes[index] if index < len(probes) else "auto",
                        "serial_port": "",
                        "telnet_port": 4444 + index,
                        "position": index,
                        "power": {"driver": "none"},
                    }
                )
            data = {
                "station": {
                    "id": "STN-01",
                    "name": "HWVAL Factory station",
                    "line": "L1",
                },
                "simulation": False,
                "profiles_dir": "./profiles",
                "database": "./station-data/hwval-factory.sqlite",
                "labels_dir": "./station-data/labels",
                "certificates_dir": "./station-data/certificates",
                "fixtures": fixture_rows,
                "printer": {
                    "enabled": False,
                    "output_dir": "./station-data/labels",
                    "dpi": 203,
                },
                "mes": {"enabled": False},
                "openocd": {
                    "binary": "openocd",
                    "interface_cfg": "interface/stlink.cfg",
                    "target_cfg": "target/stm32f7x.cfg",
                    "telnet_base_port": 4444,
                },
                "security": {
                    "session_timeout_minutes": 30,
                    "retest_after_failures": 2,
                    "self_test_interval_hours": 24,
                    "maintenance_count": 10000,
                    "require_profile_signature": False,
                },
                "kiosk": False,
            }
            args.init_station.parent.mkdir(parents=True, exist_ok=True)
            args.init_station.write_text(
                yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
            )
            print(args.init_station)
            print(f"fixtures: {len(fixture_rows)}, detected probes: {len(probes)}")
            return 0
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
            firmware = detect_stlink_firmware()
            if firmware:
                print("ST-LINK firmware:")
                for serial, version in sorted(firmware.items()):
                    major = 0
                    with contextlib.suppress(ValueError):
                        major = int(version.lstrip("Vv").split(".")[0])
                    warning = " (old; update to V3J or newer)" if 0 < major < 3 else ""
                    print(f"  {serial}\t{version}{warning}")
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


def _run_tool(args: argparse.Namespace) -> int:
    from hwval_factory.tools import ToolRunner

    config, simulation = _load_config(args)
    db = Database(config.database)
    db.initialize()
    auth = AuthService(db)
    auth.ensure_default_admin()
    profile = _resolve_profile(args, config)
    station = Station(config, db, profile, simulation=simulation)
    try:
        row = db.user(args.operator)
        if row is not None:
            role = Role(str(row["role"]))
        elif args.operator in ("admin", "administrator"):
            role = Role.ADMIN
        else:
            role = Role.OPERATOR
        runner = ToolRunner(station, args.operator)
        runner.check_role(args.tool_command, role)
        name = args.tool_command
        if name == "erase":
            result = runner.erase(args.fixture, confirm=args.confirm)
        elif name == "load-app":
            result = runner.load_app(
                args.fixture,
                image=args.image,
                via=args.via,
                activate=not args.no_activate,
            )
        elif name == "test":
            result = runner.test(args.fixture, tests=args.tests, timeout=args.timeout)
        elif name == "identity":
            result = runner.identity(
                args.fixture,
                action=args.action,
                serial=args.serial,
                mac=args.mac,
                hw_revision=args.hw_revision,
            )
        elif name == "recover":
            result = runner.recover(
                args.fixture,
                action=args.action,
                reason=args.reason,
                confirm=args.confirm,
                timeout=args.timeout,
            )
        elif name == "capture-log":
            result = runner.capture_log(
                args.fixture, port=args.port, seconds=args.seconds
            )
        else:
            result = getattr(runner, name.replace("-", "_"))(args.fixture)
        if args.json:
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True, default=str))
        else:
            print(f"{'PASS' if result.ok else 'FAIL'}  {result.tool}: {result.detail}")
            if result.data:
                print(json.dumps(result.data, indent=2, sort_keys=True, default=str))
        db.audit(
            args.operator,
            "tool.finish",
            f"fixture={args.fixture} tool={name} ok={result.ok}",
        )
        return 0 if result.ok else 1
    finally:
        station.close()
        db.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if getattr(args, "tool_command", None):
        try:
            return _run_tool(args)
        except ToolError as exc:
            print(f"tool error: {exc}", file=sys.stderr)
            return 2
        except (ConfigError, ProfileError, StationError, LabelError) as exc:
            print(f"station error: {exc}", file=sys.stderr)
            return 2
    utility = (
        args.import_macs is not None
        or args.init_station is not None
        or args.list_profiles
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
