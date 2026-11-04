# soccentric-stm32-factory-provisioning

**HWVAL Factory**: the end-of-line test and provisioning station for
STM32F767ZI boards. A cross-platform PySide6 desktop app (Windows and Linux on
the line), the production counterpart of the HWVAL Studio validation GUI.

In one operator action per board it identifies the chip over SWD, runs the EOL
test plan with the HWVAL firmware, programs the bootloader and the application
into the A/B flash layout, writes a per-unit provisioning record (serial
number, MAC, hardware revision, calibration), checks that the product boots
with the right identity, applies flash protection, prints the label and records
everything for traceability. Up to 8 fixtures run in parallel per PC.

| | |
|---|---|
| Requirements | [SRS.md](SRS.md) — SRS-FACTORY-F767 (v0.1 draft) |
| Status | **Hardware contract implemented** — v0.1.0, real HWLINK/IBWH/layout; simulator and pytest suite included |
| Stack | Python 3.11+, PySide6, pyserial, FlatBuffers (HWLINK), OpenOCD, SQLite, ZPL label printers |
| Shared core | `src/hostcore` — the in-tree implementation of the planned `soccentric-stm32-hostcore` package |
| Tests | `make test` — HWLINK frames, IBWH images, Appendix B metadata, identity vectors, option bytes, serial/MAC and full simulator runs |

## Quick start (no hardware)

The simulator needs no ST-LINK, OpenOCD or printer.

```bash
python -m pip install -e .
hwval-factory-ui --station examples/station.simulator.yaml
# or headless, one unit:
hwval-factory --cli --station examples/station.simulator.yaml \
  --fixture F1 --pcb-serial PCB-0001 --json
```

The default administrator is `admin` / `1234`; change it in
**Tools → Change PIN** before production use. The demo profile is
`examples/profile-sc767-demo` with small, valid IBWH/HEX/ELF test images.

Run the complete EOL flow with four simulated fixtures:

```bash
hwval-factory-ui --simulate --fixtures 4
```

## EOL flow (default profile)

| # | Step | Over |
|---|---|---|
| 1 | Scan board and work order | Barcode scanner |
| 2 | Identify chip (DEV_ID, REV_ID, UID, option bytes) | SWD |
| 3 | Bench configuration, program the HWVAL test image | SWD |
| 4 | Run the EOL test plan, with operator prompts | HWLINK |
| 5 | Compute calibration values (optional) | — |
| 6 | Product configuration, program BOOT + application + metadata | SWD |
| 7 | Allocate serial number and MAC, write the provisioning record | SWD |
| 8 | Boot check: identity and confirmed slot | HWLINK |
| 9 | Apply BOR, write and readout protection (never RDP 2) | SWD |
| 10 | Print the label | ZPL |
| 11 | Record the run, queue it for the MES | SQLite / HTTPS |

## What is implemented

* **Fixture engine** — one worker thread, OpenOCD process, HWLINK session and
  state machine per fixture; independent start/abort; step timings, retries,
  safe-state recording and ABORTED/INCOMPLETE handling.
* **Profiles** — versioned directories or archives, SHA-256 pinned images,
  Ed25519 signatures, EOL test plan and limits, calibration rules, identity
  rules, option-byte settings and ZPL templates. `hwval-profile
  build|sign|verify|archive|keygen` is the engineer tool.
* **HWLINK** — the real COBS + CRC-32 + FlatBuffers protocol from the
  firmware (`Hello`, `GetDeviceInfo`, `ListTests`, `Run` events,
  `OperatorPrompt`/`Answer`, `GetStatus`, `GetBootInfo`), with `mode link`
  entry and request retries. The old JSON protocol is gone.
* **Images and metadata** — IBWH 256-byte header parsing/validation (magic,
  size, type, load/entry, header CRC, image CRC, SP/reset vector), Intel HEX,
  ELF and raw BIN support, and the Appendix B `VERIFIED`/`ACTIVATED`/
  `CONFIRMED` records at their fixed offsets.
* **Provisioning record** — `PRV1` header/TLV format with CRC32, chip
  UID binding, mandatory identity fields, MAC, calibration, customer/order and
  custom key/value fields; read-back is compared byte for byte.
* **Serial and MAC allocation** — atomic SQLite transactions, Luhn check
  digits, configurable ranges/pools, 90 % warning, exhaustion stop, REWORK
  reuse by chip UID.
* **Protection** — nDBANK/nDBOOT/BOR/WRP/RDP, read-back comparison after a
  reset, WRP/BOR applied before RDP, RDP level 2 rejected everywhere,
  supervisor-approved RDP lowering for rework.
* **Labels** — ZPL II over TCP 9100 or a local queue, preview, test print,
  PDF/PNG fallback with “label pending”, supervisor reprints from stored data.
* **Traceability** — WAL SQLite schema from Appendix D plus run extras, search
  by any identity field, unit history, CSV/JSON export, per-unit PDF
  certificate, append-only records, audit log, optional queued HTTPS MES
  uplink with retry/back-off and a visible queue length.
* **Station** — YAML configuration for 1–8 fixtures, ST-LINK detection and VCP
  matching by probe serial, pluggable power control, insertion/maintenance
  counters, golden-board self-test with age warning.
* **UI** — large fixture tiles (PASS/FAIL/icon/word/colour/sound), live fixture
  detail, yield dashboard with Pareto and cycle times, unit history, scanner
  routing without focus, F1–F8 shortcuts, kiosk mode and the full menu set.
* **Access** — badge or ID/PIN login, operator/supervisor/engineer/administrator
  roles, salted scrypt PINs, idle session lock.
* **Simulator** — simulated STM32F767ZI chip with the real sector map,
  erase-before-write, WRP/RDP effects and nDBANK mass erase; the same real
  HWLINK device stack, injectable test failures, operator prompts and USB
  faults. The host has no `if simulation` branch in the protocol path.
* **Packaging** — PyInstaller spec, Linux `.deb`/AppImage scripts and a Windows
  MSI (WiX) script in `packaging/`.

## Layout

```
src/hostcore/            shared HWLINK, OpenOCD, image, theme/widget code
src/hwval_factory/       station domain, persistence, UI and CLIs
  ui/                    station, dashboard, history, supervisor dialogs
examples/                simulator and production station configuration,
                         demo profile with pinned placeholder images
docs/                    operator, supervisor and engineering guides
packaging/               PyInstaller + .deb/AppImage/MSI packaging
```

## CLI

```bash
# one unit, exit code 0 PASS / 1 FAIL / 2 station error
hwval-factory --cli --simulate --profile examples/profile-sc767-demo --fixture F1 \
  --pcb-serial PCB-0001 --operator alice --json

# hardware bring-up and discovery
hwval-factory --detect
hwval-factory --station examples/station.local.yaml --probe --fixture F1

# station utilities
hwval-factory --list-profiles --profiles-dir examples
hwval-factory --self-test --station examples/station.simulator.yaml
hwval-factory --export csv --work-order WO-1 --output runs.csv
hwval-factory --certificate 42 --output certificate.pdf
hwval-factory --reprint 42 --operator supervisor
```

## Real hardware checklist

1. Install OpenOCD ≥ 0.12 and the ST-LINK udev rules (Linux).
2. Copy `examples/station.local.yaml` or `examples/station.real.example.yaml`
   outside the repository. `probe_serial: auto` is filled from the connected
   ST-LINKs; `hwval-factory --detect` prints probes and VCP ports.
3. Copy a released profile (for example `profiles/sc767-1.0.0/`) into the
   station profile store. Use `require_profile_signature: true` and a trusted
   Ed25519 public key when signatures are mandatory.
4. Run `hwval-factory --station … --probe --fixture F1` for a step-by-step SWD
   and VCP bring-up, then **Station → Self-test…** with a golden board.
5. Verify the option-byte write on the reference board (OPEN-B05). The host now
   uses `stm32f2x options_write 0 <OPTCR> <BOOT_ADD0> <BOOT_ADD1>` with a
   register fallback, but the bench result must still be recorded.

## Verified where

| Area | Simulator | Real hardware |
|---|---|---|
| HWLINK framing/messages, `mode link` | yes | needs a HWVAL/BOOT/APP board |
| IBWH parsing and metadata writing | yes | needs a board |
| Option bytes, WRP/BOR/RDP order | yes (model) | bench confirmation required |
| Identity/TLV record and readback | yes | needs a board |
| Serial/MAC allocation, labels, DB, MES queue | yes | printer/MES endpoints required |

The simulator is a contract test, not a substitute for the hardware checklist.

See [docs/engineering-guide.md](docs/engineering-guide.md) for profile and
station details, [docs/operator-guide.md](docs/operator-guide.md) for the line
procedure, [docs/recovery-guide.md](docs/recovery-guide.md) for RDP 1,
option-byte, BOOT-no-app and connect-under-reset recovery, and
[docs/release.md](docs/release.md) for the release process.

## Related projects

| Repository | Role |
|---|---|
| [soccentric-stm32-validation-firmware](https://github.com/soccentric-stm32/soccentric-stm32-validation-firmware) | HWVAL hardware validation firmware (the EOL test image) |
| [soccentric-stm32-bootloader](https://github.com/soccentric-stm32/soccentric-stm32-bootloader) | A/B bootloader (UART update, CRC32, trial boot and rollback) |
| [soccentric-stm32-application](https://github.com/soccentric-stm32/soccentric-stm32-application) | FreeRTOS demo application in C |
| [soccentric-stm32-rust-application](https://github.com/soccentric-stm32/soccentric-stm32-rust-application) | The same demo in Rust (Embassy) |
| [soccentric-stm32-zephyr-application](https://github.com/soccentric-stm32/soccentric-stm32-zephyr-application) | The same demo on Zephyr RTOS |
| [soccentric-stm32-validation-gui](https://github.com/soccentric-stm32/soccentric-stm32-validation-gui) | HWVAL Studio: engineering GUI for tests, updates and SWD flashing |
| [soccentric-stm32-factory-provisioning](https://github.com/soccentric-stm32/soccentric-stm32-factory-provisioning) | HWVAL Factory: end-of-line test and provisioning (this repository) |

## License

[MIT](LICENSE) © 2026 Sandesh Ghimire. Use it as a starting point for your own
production line. Third-party tools and libraries (PySide6, OpenOCD and others)
keep their own licences.

Author: Sandesh Ghimire <sandesh@soccentric.com>
