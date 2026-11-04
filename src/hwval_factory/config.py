"""Station configuration (STN-001)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Invalid station configuration."""


@dataclass(slots=True)
class FixtureConfig:
    name: str
    probe_serial: str = ""
    serial_port: str = ""  # empty = locate by probe serial (STN-003)
    telnet_port: int = 4444
    enabled: bool = True
    position: int = 0
    golden_uid: str = ""
    power: dict[str, Any] = field(default_factory=dict)

    @property
    def has_probe(self) -> bool:
        return bool(self.probe_serial)


@dataclass(slots=True)
class PrinterConfig:
    enabled: bool = False
    host: str = ""
    port: int = 9100
    queue: str = ""
    template: str = ""
    output_dir: str = ""
    dpi: int = 203


@dataclass(slots=True)
class MesConfig:
    enabled: bool = False
    endpoint: str = ""
    token: str = ""
    timeout_s: float = 10.0
    max_attempts: int = 10
    backoff_s: float = 5.0


@dataclass(slots=True)
class ScannerConfig:
    mode: str = "hid"          # hid | serial
    serial_port: str = ""
    baudrate: int = 9600
    prefix: str = ""
    suffix: str = ""
    fixture: str = ""          # optional per-fixture routing


@dataclass(slots=True)
class OpenOcdConfig:
    binary: str = "openocd"
    interface_cfg: str = "interface/stlink.cfg"
    target_cfg: str = "target/stm32f7x.cfg"
    telnet_base_port: int = 4444


@dataclass(slots=True)
class StationConfig:
    station_id: str
    station_name: str
    line: str = ""
    fixtures: list[FixtureConfig] = field(default_factory=list)
    printer: PrinterConfig = field(default_factory=PrinterConfig)
    database: Path = Path("hwval-factory.sqlite")
    mes: MesConfig = field(default_factory=MesConfig)
    simulation: bool = False
    profiles_dir: Path = Path("profiles")
    labels_dir: Path = Path("labels")
    certificates_dir: Path = Path("certificates")
    openocd: OpenOcdConfig = field(default_factory=OpenOcdConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    session_timeout_minutes: int = 30
    retest_after_failures: int = 2
    self_test_interval_hours: int = 24
    maintenance_count: int = 10000
    maintenance_margin: int = 1000
    require_profile_signature: bool = False
    profile_public_key: str = ""
    kiosk: bool = False
    label_template: str = ""
    config_path: Path | None = None

    @property
    def fixture_count(self) -> int:
        return len(self.fixtures)

    def fixture(self, name: str) -> FixtureConfig:
        for fixture in self.fixtures:
            if fixture.name == name:
                return fixture
        raise ConfigError(f"unknown fixture {name!r}")

    def resolve_paths(self) -> None:
        base = self.config_path.parent if self.config_path else Path.cwd()
        for attr in ("database", "profiles_dir", "labels_dir", "certificates_dir"):
            value = getattr(self, attr)
            if not value.is_absolute():
                setattr(self, attr, (base / value).resolve())
        if self.printer.output_dir and not Path(self.printer.output_dir).is_absolute():
            self.printer.output_dir = str((base / self.printer.output_dir).resolve())

    def to_dict(self) -> dict[str, Any]:
        return {
            "station": {"id": self.station_id, "name": self.station_name, "line": self.line},
            "fixtures": [
                {
                    "name": f.name,
                    "probe_serial": f.probe_serial,
                    "serial_port": f.serial_port,
                    "telnet_port": f.telnet_port,
                    "enabled": f.enabled,
                    "position": f.position,
                    "golden_uid": f.golden_uid,
                    "power": f.power,
                }
                for f in self.fixtures
            ],
            "printer": {
                "enabled": self.printer.enabled,
                "host": self.printer.host,
                "port": self.printer.port,
                "queue": self.printer.queue,
                "template": self.printer.template,
                "output_dir": self.printer.output_dir,
            },
            "database": str(self.database),
            "mes": {
                "enabled": self.mes.enabled,
                "endpoint": self.mes.endpoint,
                "token": self.mes.token,
                "timeout_s": self.mes.timeout_s,
            },
            "simulation": self.simulation,
            "profiles_dir": str(self.profiles_dir),
            "labels_dir": str(self.labels_dir),
            "certificates_dir": str(self.certificates_dir),
            "openocd": {
                "binary": self.openocd.binary,
                "interface_cfg": self.openocd.interface_cfg,
                "target_cfg": self.openocd.target_cfg,
                "telnet_base_port": self.openocd.telnet_base_port,
            },
            "scanner": {
                "mode": self.scanner.mode,
                "serial_port": self.scanner.serial_port,
                "baudrate": self.scanner.baudrate,
                "prefix": self.scanner.prefix,
                "suffix": self.scanner.suffix,
                "fixture": self.scanner.fixture,
            },
            "security": {
                "session_timeout_minutes": self.session_timeout_minutes,
                "retest_after_failures": self.retest_after_failures,
                "self_test_interval_hours": self.self_test_interval_hours,
                "maintenance_count": self.maintenance_count,
                "maintenance_margin": self.maintenance_margin,
                "require_profile_signature": self.require_profile_signature,
                "profile_public_key": self.profile_public_key,
            },
            "kiosk": self.kiosk,
        }

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.config_path
        if target is None:
            raise ConfigError("no path for station configuration")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return target


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def detect_stlink_serials() -> list[str]:
    """Return the serial numbers of connected ST-LINK probes."""
    probes: set[str] = set()
    try:
        from serial.tools import list_ports

        for info in list_ports.comports():
            haystack = " ".join(
                str(x) for x in (info.serial_number, info.hwid, info.description)
            ).lower()
            if ("st-link" in haystack or "stlink" in haystack) and info.serial_number:
                probes.add(str(info.serial_number))
    except Exception:
        pass
    return sorted(probes)


def detect_vcp_ports() -> list[dict[str, str]]:
    """Return serial ports that look like an ST-LINK virtual COM port."""
    result: list[dict[str, str]] = []
    try:
        from serial.tools import list_ports

        for info in list_ports.comports():
            haystack = " ".join(
                str(x) for x in (info.serial_number, info.hwid, info.description)
            ).lower()
            if "st-link" in haystack or "stlink" in haystack or "stmicroelectronics" in haystack:
                result.append(
                    {
                        "device": str(info.device),
                        "serial": str(info.serial_number or ""),
                        "description": str(info.description or ""),
                    }
                )
    except Exception:
        pass
    return result


def _auto_assign_probes(fixtures: list[FixtureConfig]) -> None:
    missing = [fixture for fixture in fixtures if fixture.probe_serial in ("", "auto", "*")]
    if not missing:
        return
    probes = detect_stlink_serials()
    if not probes:
        return
    used = {fixture.probe_serial for fixture in fixtures if fixture.probe_serial not in ("", "auto", "*")}
    available = [probe for probe in probes if probe not in used]
    for fixture, probe in zip(missing, available, strict=False):
        fixture.probe_serial = probe


def _fixture_from_dict(raw: dict[str, Any], index: int, base_port: int) -> FixtureConfig:
    return FixtureConfig(
        name=str(raw.get("name") or f"F{index + 1}"),
        probe_serial=str(raw.get("probe_serial") or raw.get("stlink") or ""),
        serial_port=str(raw.get("serial_port") or ""),
        telnet_port=_as_int(raw.get("telnet_port"), base_port + index),
        enabled=_as_bool(raw.get("enabled"), True),
        position=_as_int(raw.get("position"), index),
        golden_uid=str(raw.get("golden_uid") or ""),
        power=dict(raw.get("power") or {}),
    )


def load_station_config(path: str | Path | None) -> StationConfig:
    if path is None:
        return default_station()
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"station configuration not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid station YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("station configuration must be a mapping")
    station = raw.get("station") or {}
    if not isinstance(station, dict):
        raise ConfigError("[station] must be a mapping")
    openocd_raw = raw.get("openocd") or {}
    if not isinstance(openocd_raw, dict):
        raise ConfigError("[openocd] must be a mapping")
    openocd = OpenOcdConfig(
        binary=str(openocd_raw.get("binary", "openocd")),
        interface_cfg=str(openocd_raw.get("interface_cfg", "interface/stlink.cfg")),
        target_cfg=str(openocd_raw.get("target_cfg", "target/stm32f7x.cfg")),
        telnet_base_port=_as_int(openocd_raw.get("telnet_base_port"), 4444),
    )
    fixture_raw = raw.get("fixtures") or []
    if not isinstance(fixture_raw, list):
        raise ConfigError("[fixtures] must be a list")
    fixtures = [
        _fixture_from_dict(f, index, openocd.telnet_base_port)
        for index, f in enumerate(fixture_raw)
        if isinstance(f, dict)
    ]
    if not _as_bool(raw.get("simulation"), False):
        _auto_assign_probes(fixtures)
    printer_raw = raw.get("printer") or {}
    mes_raw = raw.get("mes") or {}
    security = raw.get("security") or {}
    scanner_raw = raw.get("scanner") or {}
    if (
        not isinstance(printer_raw, dict)
        or not isinstance(mes_raw, dict)
        or not isinstance(security, dict)
        or not isinstance(scanner_raw, dict)
    ):
        raise ConfigError("[printer], [mes] and [security] must be mappings")
    config = StationConfig(
        station_id=str(station.get("id") or station.get("station_id") or "STN-01"),
        station_name=str(station.get("name") or "HWVAL Factory station"),
        line=str(station.get("line") or ""),
        fixtures=fixtures,
        printer=PrinterConfig(
            enabled=_as_bool(printer_raw.get("enabled"), False),
            host=str(printer_raw.get("host") or ""),
            port=_as_int(printer_raw.get("port"), 9100),
            queue=str(printer_raw.get("queue") or ""),
            template=str(printer_raw.get("template") or ""),
            output_dir=str(printer_raw.get("output_dir") or ""),
            dpi=_as_int(printer_raw.get("dpi"), 203),
        ),
        database=Path(str(raw.get("database") or "hwval-factory.sqlite")),
        mes=MesConfig(
            enabled=_as_bool(mes_raw.get("enabled"), False),
            endpoint=str(mes_raw.get("endpoint") or ""),
            token=str(os.environ.get("HWVAL_MES_TOKEN", mes_raw.get("token") or "")),
            timeout_s=float(mes_raw.get("timeout_s") or 10.0),
            max_attempts=_as_int(mes_raw.get("max_attempts"), 10),
            backoff_s=float(mes_raw.get("backoff_s") or 5.0),
        ),
        simulation=_as_bool(raw.get("simulation"), False),
        profiles_dir=Path(str(raw.get("profiles_dir") or "profiles")),
        labels_dir=Path(str(raw.get("labels_dir") or "labels")),
        certificates_dir=Path(str(raw.get("certificates_dir") or "certificates")),
        openocd=openocd,
        scanner=ScannerConfig(
            mode=str(scanner_raw.get("mode") or "hid").strip().lower(),
            serial_port=str(scanner_raw.get("serial_port") or ""),
            baudrate=_as_int(scanner_raw.get("baudrate"), 9600),
            prefix=str(scanner_raw.get("prefix") or ""),
            suffix=str(scanner_raw.get("suffix") or ""),
            fixture=str(scanner_raw.get("fixture") or ""),
        ),
        session_timeout_minutes=_as_int(security.get("session_timeout_minutes"), 30),
        retest_after_failures=_as_int(security.get("retest_after_failures"), 2),
        self_test_interval_hours=_as_int(security.get("self_test_interval_hours"), 24),
        maintenance_count=_as_int(security.get("maintenance_count"), 10000),
        maintenance_margin=_as_int(security.get("maintenance_margin"), 1000),
        require_profile_signature=_as_bool(security.get("require_profile_signature"), False),
        profile_public_key=str(security.get("profile_public_key") or ""),
        kiosk=_as_bool(raw.get("kiosk"), False),
        config_path=p.resolve(),
    )
    config.resolve_paths()
    validate_station_config(config)
    return config


def validate_station_config(config: StationConfig) -> None:
    if not config.station_id.strip():
        raise ConfigError("station.id is required")
    if not 1 <= len(config.fixtures) <= 8:
        raise ConfigError("a station must define between 1 and 8 fixtures")
    names = [f.name for f in config.fixtures]
    if len(set(names)) != len(names):
        raise ConfigError("fixture names must be unique")
    probes = [f.probe_serial for f in config.fixtures if f.probe_serial]
    if len(set(probes)) != len(probes):
        raise ConfigError("ST-LINK serial numbers must be unique")
    if not config.simulation:
        missing = [f.name for f in config.fixtures if not f.probe_serial]
        if missing:
            raise ConfigError(f"fixtures without ST-LINK serial: {', '.join(missing)}")
    ports = [f.telnet_port for f in config.fixtures]
    if len(set(ports)) != len(ports):
        raise ConfigError("fixture telnet ports must be unique")
    if config.session_timeout_minutes < 0:
        raise ConfigError("session timeout cannot be negative")
    if config.scanner.mode not in ("hid", "serial", "off"):
        raise ConfigError("scanner.mode must be hid, serial or off")
    if config.scanner.mode == "serial" and not config.scanner.serial_port:
        raise ConfigError("scanner.serial_port is required in serial mode")


def default_station(simulated: bool = True, fixture_count: int = 4) -> StationConfig:
    count = max(1, min(8, fixture_count))
    fixtures = [
        FixtureConfig(
            name=f"F{i + 1}",
            probe_serial=f"SIM-STLINK-{i + 1:02d}" if simulated else "",
            telnet_port=4444 + i,
            position=i,
        )
        for i in range(count)
    ]
    config = StationConfig(
        station_id="STN-SIM",
        station_name="HWVAL Factory simulator",
        line="SIM",
        fixtures=fixtures,
        simulation=simulated,
        database=Path("station-data/hwval-factory.sqlite"),
        profiles_dir=Path("profiles"),
        labels_dir=Path("station-data/labels"),
        certificates_dir=Path("station-data/certificates"),
        printer=PrinterConfig(
            enabled=False,
            output_dir="station-data/labels",
        ),
        config_path=None,
    )
    config.resolve_paths()
    return config
