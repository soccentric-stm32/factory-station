# HWVAL Factory — engineering guide

## Architecture

```
src/hostcore/            shared soccentric-stm32-hostcore implementation
  hwlink.py              real HWLINK session (COBS, CRC-32, FlatBuffers)
  protocol/              shared codec + generated FlatBuffers (from Studio)
  layout.py              one flash-layout source matching boot_layout.h
  simulator_device.py    HWLINK role simulator used by the fixture simulator
  openocd.py             OpenOCD process + telnet control
  images.py              ELF/HEX/BIN/IBWH parsing, Appendix B records
  theme.py, widgets.py   shared light theme, icons and widgets
src/hwval_factory/
  config.py              station.yaml (STN-001)
  profile.py             manifest, SHA-256 pins, Ed25519, calibration rules
  database.py            SQLite schema, WAL, run/step/test/audit persistence
  identity.py            provisioning record (Appendix A)
  serials.py             serial and MAC allocation
  protection.py          option bytes, WRP/BOR/RDP (never RDP 2)
  labels.py              ZPL, previews, PDF/PNG fallback, reprints
  mes.py                 optional queued HTTPS uplink
  station.py             one worker per fixture, declarative step engine
  simulator.py           simulated chip + OpenOCD + HWLINK firmware
  reports.py             CSV/JSON exports and PDF certificate
  cli.py                 headless mode
  profile_tool.py        profile build/sign/verify/archive tool
  ui/                    PySide6 station, dashboard, history, dialogs
```

The UI never owns hardware I/O. Each fixture runs in its own Python thread with
its own OpenOCD process, HWLINK transport and state machine. The UI reads
fixture snapshots on a timer; worker events cross the thread boundary through a
thread-safe queue. Operator prompts are handed to the UI thread and the worker
waits on an event until the operator answers.

## Station configuration

Start from `examples/station.simulator.yaml` or
`examples/station.real.example.yaml`:

```yaml
station: {id: STN-01, name: Line 1 station, line: L1}
fixtures:
  - name: F1
    probe_serial: "066BFF..."
    serial_port: ""          # empty = find VCP by probe serial
    telnet_port: 4444
    enabled: true
    golden_uid: ""           # optional golden board UID for self-test
    power: {driver: none}    # none | usb-hub | relay | supply
printer: {enabled: false, host: "", port: 9100, queue: "", output_dir: labels}
database: station-data/hwval-factory.sqlite
profiles_dir: profiles
mes: {enabled: false, endpoint: "", token: ""}
simulation: false
openocd: {binary: openocd, interface_cfg: interface/stlink.cfg,
          target_cfg: target/stm32f7x.cfg, telnet_base_port: 4444}
security:
  session_timeout_minutes: 30
  retest_after_failures: 2
  self_test_interval_hours: 24
  maintenance_count: 10000
  require_profile_signature: false
  profile_public_key: ""   # trusted Ed25519 public key (base64)
```

Paths are resolved relative to the station file. Put configuration, profiles,
the database and labels outside the installation directory.

## Profile workflow

```
hwval-profile build examples/profile-sc767-demo \
  --id sc767-demo --version 1.2.0 --product "SoCcentric demo board" \
  --hw-revisions B C --test hwval.elf --boot boot.hex --app demo.img
hwval-profile keygen keys/eng-private.pem keys/eng-public.txt
hwval-profile sign examples/profile-sc767-demo keys/eng-private.pem
hwval-profile verify examples/profile-sc767-demo --public-key keys/eng-public.txt
hwval-profile archive examples/profile-sc767-demo dist/sc767-demo.zip
```

`load_profile()` re-hashes every image, checks the format, rejects RDP 2,
checks `min_factory_version`, and optionally verifies the Ed25519 signature.
Profiles are copied into the station store by **Tools → Profiles…** and are not
edited on the station.

Manifest sections: `images`, `parts`, `test_plan`, `calibration`, `identity`,
`protection`, `label` and optional `layout`, `boot_metadata`, `provisioning`,
`boot_check`, `steps`, `otp`. Calibration rules use a safe expression subset:
`value`, `round`, `abs`, `int`, `float`, `min`, `max`, `sqrt` and arithmetic.

## EOL step engine and failure semantics

The default sequence is Scan → Identify → Prepare test → Test → Calibrate →
Program → Provision → Boot check → Protect → Label → Record. Calibrate is
omitted when there are no rules; Protect and Label can be disabled in the
manifest `steps.enabled` map. Retries come from `steps.retries` and default to
one for Identify/Prepare/Test/Program/Label and two for Boot check.

On failure the run stops, the step and reason are recorded, the measured value
and limits are in `test_results`, and the reason includes the flash state
(`erased`, `app_programmed`, `provisioned`, `protected`). The serial is
consumed at the moment the provisioning record passes read-back; before that
it is returned to the pool. Abort is checked at every step boundary and during
scan waits.

Production programming writes BOOT, the app payload into slot A, erases the
metadata sector once, then appends the 256-byte IBWH header at `+0x000` and the
`VERIFIED`, `ACTIVATED` and `CONFIRMED` records at `+0x100`, `+0x120` and
`+0x140` (Appendix B). It reads back the app CRC32 and every record.

## Provisioning record

The record is in the identity area at `provisioning.address` (bank 2 sector
13, `0x08104000`; `0x08004000` when bank 2 is swapped). The header and TLV
layout follow the identity contract: magic `PRV1`, format, revision, length,
UID and CRC-32. Calibration values are encoded as `kind (0 int32, 1 float32)`,
unit code, then four bytes; custom fields are `key\0value` under types
`0x80…0xFE`. `parse_record()` checks magic, format, TLV CRC32, header CRC32 and
optionally the chip UID.

## Simulator and CLI

The simulator is selected explicitly; the GUI refuses to start without a
station file unless `--simulate` is given:

```
hwval-factory --cli --simulate --profile examples/profile-sc767-demo --fixture F1 \
  --pcb-serial PCB-0001 --json
hwval-factory --detect
hwval-factory --station examples/station.local.yaml --probe --fixture F1
```

Exit codes: `0` PASS, `1` FAIL/ABORTED/INCOMPLETE, `2` station error. Use
`--fail-test CLK.001 --fault-rate 0.01` to exercise failure and USB-loss paths.
The simulator's UI is launched with `hwval-factory-ui --simulate --fixtures 4`.

## Protection

Profiles set `nDBANK`, `nDBOOT`, `BOR_LEV`, `nWRP` per bank and `RDP`. Factory
writes the option bytes, resets, reads them back and fails on any difference.
RDP level 2 is rejected in the profile parser, in `ProtectionSpec.validate()`,
in `apply_protection()` and in the simulator. Rework may lower RDP 1 → 0 only
after supervisor confirmation; this mass-erases the chip.

## Data and MES

SQLite uses WAL, a 30 s busy timeout and a checkpoint on run close. Runs are
searchable by identity; `run_extras` stores option bytes, calibration, image
versions, metadata and the provisioning blob. `mes_queue` is persistent;
`MesService` posts JSON over HTTPS with a bearer token, retries with back-off
and exposes the queue length. CSV/JSON exports and PDF certificates are in
`reports.py`.

## Packaging

See `packaging/README.md`. Station data stays outside the install tree.

## Verification without hardware

The simulator covers the full flow, prompts, injected test failures, USB
faults, protection read-back, boot identity and label fallback. It uses the
real HWLINK stack, the real flash sector map and erase-before-write rules.

`make test` runs the pytest suite: HWLINK frames/parser, IBWH images and
Appendix B records, identity vectors, option bytes, serial/MAC allocation,
profile/layout validation, simulated flash semantics and full PASS/FAIL runs.
These are contract tests; the hardware checklist in the README still applies.
