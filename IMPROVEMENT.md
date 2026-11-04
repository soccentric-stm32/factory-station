# HWVAL Factory — improvement plan

Author: Sandesh Ghimire <sandesh@soccentric.com>
Date: 2026-11-04
Scope: everything needed to turn HWVAL Factory into a complete end-of-line
(EOL) station that takes a blank STM32F767ZI board and leaves it tested,
loaded with the bootloader and the factory application, given its identity
(serial number, MAC address, hardware revision, calibration), locked, labelled
and recorded.

---

## 0. Implementation status (2026-11-04)

The factory-side P0 contract work is implemented and covered by `make test`:

| Area | Status |
|---|---|
| B-01…B-03 real HWLINK framing, `mode link`, catalogue + Run events + prompts | done |
| B-04 IBWH image parser/builder | done |
| B-05 Appendix B header/VERIFIED/ACTIVATED/CONFIRMED writer | done |
| B-06/B-07 flash writes and `options_write <bank> <user> <boot0> <boot1>` | done (bench confirmation still required) |
| B-08 one layout module and profile layout validation | done |
| B-09 released profile from real BOOT/HWVAL/application builds | done (`profiles/sc767-1.0.0`) |
| B-10/B-14 boot check plus identity readback | done |
| B-11/B-12 `--detect`, `station.local.yaml`, `--probe`, GUI bring-up | done |
| B-13 application load through BOOT over VCP (`app_load: boot`) | done |
| C-01…C-03 real protocol simulator, flash layout and BOOT metadata | done |
| D-01 identity contract (`PRV1`, CRC, UID binding) | done; magic corrected from `1VRP` |
| D-02 MAC sources: OUI, imported CSV pool, UID-derived local MAC | done (external EEPROM source not enabled) |
| D-03 serial templates with standard tokens | done (pre-printed-label reservation uses the normal serial range) |
| A-01 identity area in boot_layout.h; BOOT refuses to erase sector 13 | done |
| A-03/A-04 identity reader in C, Rust/Embassy and Zephyr applications | done |
| A-05 identity reported over HWLINK (`HelloReply.detail`, PRV1 keys) | done |
| A-08 OpenOCD option-byte path confirmed on NUCLEO-F767ZI | done (see `docs/hardware-verification.md`) |
| E-01/E-02 protection order, post-protection boot check, RDP 2 blocked | done |
| E-03 supervisor RDP 1 -> 0 recovery with mass erase and audit | done |
| F-04 lid/clamp and board-present inputs (none/gpio drivers) | done |
| F-05/F-06 self-test expiry and insertion maintenance block production | done |
| F-08 HID and serial barcode scanners with prefix/suffix and fixture routing | done |
| J-01/J-02 pytest suite and cross-repo contract checks | done |
| H-02/H-04 MES idempotency, DB quick-check, backups and schema-version guard | done |
| J-03 pytest-qt UI smoke, scenario CLI and MES idempotency | done |
| J-05 ruff and mypy gates | done |

Still requiring the firmware repositories or hardware:

* A-01, A-03, A-05, A-06: bootloader `boot_layout.h` identity area, C/Rust/Zephyr
  identity readers, `GetIdentity` HWLINK message and MAC use.
* J-04 nightly hardware-in-the-loop run.
* The bench confirmation in OPEN-B05, and all real-instrument tests in F/G.

---

## 1. Where the app stands today

The station framework is in place: about 12k lines covering the fixture engine,
the 11-step state machine, the SQLite traceability database, serial/MAC
allocation, labels, MES queue, login/roles, the PySide6 UI, the CLI and
packaging. Two things run correctly today:

* `hwval-factory --cli --station examples/station.simulator.yaml ...` completes
  a unit with PASS.
* The GUI starts and runs against the simulator.

**Against a real board, none of the device-facing steps work.** The host code
and its simulator were written against an invented protocol, image format and
flash layout. They agree with each other but not with the firmware in this
workspace. The simulator therefore proves nothing about real hardware.

### 1.1 Confirmed mismatches with the real firmware

| # | Area | Factory app today | Real firmware | Where |
|---|---|---|---|---|
| M1 | HWLINK framing | `0xA5 0x5A` sync, own header, dict payloads | COBS + 0x00 delimiter, 8-byte header, CRC-32, FlatBuffers `Message` (protocol 1.1) | `src/hostcore/hwlink.py` vs `validation-firmware/src/hwlink.h`, `protocol/hwlink.fbs` |
| M2 | HWLINK messages | Invented `MessageType` set | `Hello/HelloReply`, `GetDeviceInfo`, `ListTests`, `Run`, `RunStarted`, `TestStarted`, `TestResult`, `OperatorPrompt`, `OperatorAnswer`, `RunFinished`, `GetBootInfo`, `Update*`, `Activate` | same |
| M3 | Link mode | Assumes HWLINK on connect | HWVAL boots into the text CLI. The host must send `mode link` first (LINK-010 keeps it across reset) | `validation-firmware/src/link.h` |
| M4 | Application image | `SCFW` header (`images.py`) | `IBWH` header, 256 bytes, built by `bootloader/tools/mkimage.py` | `src/hostcore/images.py:27` vs `mkimage.py:26` |
| M5 | Slot A address | `0x08020000` | `0x0800C000` (bank 1 sectors 3–11, 976 KB) | demo `manifest.yaml` vs `bootloader/src/common/boot_layout.h` |
| M6 | Boot metadata | `0x0801F000`, stride `0x100`, `BMET` records | `0x08008000` (bank 1 sector 2): header at +0x000, VERIFIED +0x100, ACTIVATED +0x120, CONFIRMED +0x140, REJECTED +0x160. 32-byte records with their own magic and a CRC over the first 28 bytes | same |
| M7 | Option bytes for product | `nDBOOT: 0` in the profile | BOOT requires `nDBANK = 0`, `nDBOOT = 1`, `BOOT_ADD0 = 0x0080`, `BOOT_ADD1` kept | `bootloader/SRS.md` BASM-001/002, `tools/provision.sh` |
| M8 | `options_write` call | `stm32f2x options_write <optcr> <optcr1>` | OpenOCD expects `stm32f2x options_write <bank> <user_options> <boot_addr0> <boot_addr1>` | `src/hostcore/openocd.py:366` vs `provision.sh` |
| M9 | Writing to flash | `write_memory()` uses `load_image`, which writes target memory, not flash, so the provisioning record and metadata are never actually programmed | Use `flash write_image` / `flash fillw` and erase the sector first | `src/hostcore/openocd.py:334` |
| M10 | Demo images | 168–320 byte placeholder files | Real builds exist: `bootloader/build/boot.hex`, `validation-firmware/build/hwval.elf`, `application/build/app.bin` | `examples/profile-sc767-demo/` |
| M11 | Calibration source | `ADC.001.vrefint_mv` | VREFINT is measured by `ADC.002` / `PWR.001` | demo `manifest.yaml` vs `TEST_CATALOGUE.md` |
| M12 | Provisioning record consumer | Writes a record at `0x08104000` | No firmware reads it. The application uses the chip UID only and has no serial number, MAC or calibration source | `application/src/comms_task.c` |
| M13 | Real-hardware defaults | GUI with no `--station` runs simulated | — | `src/hwval_factory/app.py:81` |

HWVAL Studio (`soccentric-stm32-validation-gui`) already has a working
COBS/CRC/FlatBuffers stack (`protocol/link.py`, `protocol/codec.py`,
`protocol/generated/`), an OpenOCD wrapper and a device client. Use these
instead of rewriting them.

---

## 2. Target end-of-line flow

```
 scan ─► identify ─► bench config ─► load HWVAL ─► EOL tests ─► calibrate
   │                                                               │
   └──────────────────────────────────────────────────────────────┘
                                   ▼
 product config ─► BOOT ─► factory/app image ─► boot metadata ─► identity record
                                   ▼
 boot check (APP role, identity, confirmed slot) ─► protection ─► label ─► record ─► MES
```

| # | Step | Transport | Result on success |
|---|---|---|---|
| 1 | Scan PCB serial and work order | Scanner | Unit and order bound to fixture |
| 2 | Identify chip: DEV_ID 0x451, REV_ID, UID, flash size, OPTCR/OPTCR1, RDP | SWD | Chip matches profile |
| 3 | Bench config: `nDBANK = 1`, mass erase | SWD | Single-bank flash, empty |
| 4 | Program and verify HWVAL test image | SWD | HWVAL running |
| 5 | Enter link mode, `Hello`, `GetDeviceInfo`, cross-check UID | VCP | Session up, same chip |
| 6 | Run the profile's test plan with limits and operator prompts | VCP | All results PASS |
| 7 | Compute calibration from measurements | Host | Values in range |
| 8 | Product config: `nDBANK = 0`, `nDBOOT = 1`, `BOOT_ADD0 = 0x0080`, mass erase | SWD | Dual-bank, empty |
| 9 | Program and verify BOOT at `0x08000000` | SWD | BOOT in bank 1 sectors 0–1 |
| 10 | Program the factory/application image (IBWH) into slot A | SWD or BOOT update over VCP | Image CRC verified |
| 11 | Write boot metadata so slot A is verified and active | SWD | BOOT selects slot A |
| 12 | Allocate serial number and MAC, build the identity record, write and read back | SWD | Byte-for-byte match |
| 13 | Reset, `Hello` from the application, `GetBootInfo` and identity readback | VCP | Role APP, slot A confirmed, identity matches |
| 14 | Apply BOR, WRP (BOOT + identity sectors), RDP 1; read back | SWD | Protection as specified, never RDP 2 |
| 15 | Print the label | ZPL | Label printed or queued |
| 16 | Commit the run, consume serial/MAC, queue for MES | SQLite / HTTPS | Unit PASS |

---

## 3. Task list

Priority: **P0** blocks a real board from passing; **P1** needed for
production; **P2** needed for scale or audit; **P3** improvement.
Each task lists what "done" means.

### Phase A — Firmware contracts (shared with the firmware repos)

| ID | Pri | Task | Done when |
|---|---|---|---|
| A-01 | P0 | Define the **identity/provisioning area** in `boot_layout.h`. Use bank 2 sector 13 (`0x08104000`, 16 KB, currently "reserved"). Document its address with and without bank swap (`0x08004000` when `SWP_FB = 1`). | One header names the address. BOOT never erases it, and a test confirms that. |
| A-02 | P0 | Freeze the **identity record format**: header (magic `PRV1`, format, length, UID binding, CRC-32) plus TLVs for serial, product, HW revision, manufacturing time, station/fixture, profile, firmware version, MAC, calibration (0x20–0x2F), customer (0x40–0x4F), custom (0x80–0xFE). Write it as an SRS appendix shared by all repos. | Spec merged. Host encoder and firmware decoder pass the same test vectors. |
| A-03 | P0 | Add an **identity reader** to the C application: verify CRC and UID binding, expose serial/MAC/rev/calibration, fall back safely when blank. | App reports the identity over HWLINK and uses the MAC for Ethernet. |
| A-04 | P1 | Port the identity reader to the Rust (Embassy) and Zephyr applications. | All three apps report the same identity from one board. |
| A-05 | P0 | Add an **identity query to HWLINK** (`GetIdentity` → `Identity`, or extra fields in `DeviceInfo`/`Status`). Bump the protocol minor version. Regenerate code in every repo. | Factory boot check reads the identity over VCP. |
| A-06 | P1 | Use the provisioned MAC in the application's Ethernet init (LwIP/Embassy/Zephyr). If none is provisioned, fall back to a UID-derived locally administered MAC. | Link comes up with the provisioned MAC. |
| A-07 | P1 | Decide and document the **factory image**: either the normal application or a dedicated factory build (for example a burn-in or self-test mode that confirms itself). Add a build target that produces the IBWH image with a release version. | Profile names one released IBWH file. |
| A-08 | P1 | Settle **OPEN-B05 / OPEN-F02**: confirm on the bench that `stm32f2x options_write` sets `nDBANK`/`nDBOOT` on the F767. If not, add the STM32CubeProgrammer CLI path. | Both directions (bench ⇄ product) work on a real board, logged. |
| A-09 | P2 | Decide whether **BOOT checks the identity record** (for example refusing to start an app when it is missing in production builds). | Decision in the BOOT SRS. |

### Phase B — Make one real board pass (factory app)

| ID | Pri | Task | Done when |
|---|---|---|---|
| B-01 | P0 | Replace `src/hostcore/hwlink.py` with the real stack: COBS, 8-byte header, CRC-32, FlatBuffers `Message` union, request/response by sequence number, event handling. Reuse HWVAL Studio's `link.py`/`codec.py` and generated code. Remove the invented message set. | Round-trip tests pass against the firmware's frame vectors. Talks to a real HWVAL. |
| B-02 | P0 | Enter link mode: send `mode link` over the text CLI, wait for HWLINK, handle the LINK-010 reset flag and the `EnterTextMode` fallback. | Works on a freshly flashed board and after reset. |
| B-03 | P0 | Rewrite the test step on real messages: `ListTests`, `Run` (by ID/pattern), `RunStarted`/`TestStarted`/`TestResult`/`RunFinished`, `OperatorPrompt` ⇄ `OperatorAnswer`, `Abort`, `GetResults`, `GetReport`. | Full test plan runs with prompts on a real board. Results are stored per test. |
| B-04 | P0 | Replace `images.py`'s SCFW format with **IBWH**: parse/validate the 256-byte header (magic, version, size, type, load address, entry, length, CRC, SHA, header CRC), check SP and reset vector like `mkimage.py`, and keep `.hex`/`.elf`/`.bin` support for BOOT and HWVAL. | Accepts the real `app.img`. Rejects a corrupt one. |
| B-05 | P0 | Replace the metadata writer with the **Appendix B format**: header at +0x000 and VERIFIED/ACTIVATED (and optionally CONFIRMED) at their fixed offsets, with the correct magic and CRC. Erase sector 2 first. | BOOT on a real board selects slot A without a UART update. |
| B-06 | P0 | Fix flash writes in `openocd.py`: `flash write_image erase <file> <addr> bin` (or sector erase + `flash write_image`) for metadata and identity, then `verify_image`. Never use `load_image` for flash. | Write → power cycle → read back matches. |
| B-07 | P0 | Fix `write_option_bytes` to OpenOCD's real `options_write 0 <user> <boot0> <boot1>` signature. Keep `BOOT_ADD1`, keep the register fallback, power-cycle/re-examine after an `nDBANK` change. | Bench and product configs apply and read back on hardware. |
| B-08 | P0 | Correct the profile: slot A `0x0800C000`, metadata `0x08008000`, product `nDBOOT = 1`, `BOOT_ADD0 = 0x0080`, identity area from A-01, calibration from `ADC.002`/`PWR.001`. Read layout constants from one module, not from each manifest. | Profile validation rejects any address outside the layout. |
| B-09 | P0 | Build a **real released profile** (`profiles/sc767-1.0.0/`) from the real `boot.hex`, `hwval.elf` and IBWH app image, with SHA-256 pins. Add `hwval-profile build --from-workspace` to collect the artefacts. Keep the placeholder demo for the simulator only. | Profile verifies. A real board passes with it. |
| B-10 | P0 | Boot check on real messages: reset, wait for the application `HelloReply` (role APP), `GetBootInfo` (active = A, confirmed), identity readback from A-05, version equals the profile. | Fails clearly on wrong slot, unconfirmed image or identity mismatch. |
| B-11 | P0 | Real-hardware station: `examples/station.local.yaml` with auto-detected ST-LINK serials. `hwval-factory --detect` prints probes and VCP ports. The GUI asks for a station file instead of silently simulating. | Fresh install finds the plugged-in ST-LINK/V2.1 and runs on it. |
| B-12 | P0 | `hwval-factory --probe --fixture F1` bring-up command: SWD connect, IDCODE/DEV_ID/UID, option bytes, a test-flash, VCP open, `Hello`. | One command shows what works and what fails, step by step. |
| B-13 | P1 | Second path for step 10: load the app **through BOOT over VCP** (`UpdateBegin/Data/End`, `Activate`) as `bootctl.py` does. Choose per profile (`app_load: swd | boot`). | Both paths pass. BOOT's own image check runs in the `boot` path. |
| B-14 | P1 | Read back the identity sector after reset and after protection, through SWD and HWLINK. | Both readbacks recorded in the run. |

### Phase C — Simulator that matches the hardware

| ID | Pri | Task | Done when |
|---|---|---|---|
| C-01 | P0 | Rebuild the simulated device on the real HWLINK stack (reuse HWVAL Studio's `simulator/device.py`): text CLI → `mode link`, HWVAL test catalogue, APP role with `GetBootInfo` and identity. | The same host code drives the simulator and hardware with no `if simulation` in the protocol path. |
| C-02 | P0 | Simulated flash that enforces the real layout: sector erase granularity, write-only-after-erase, `nDBANK` remap and mass erase on change, RDP/WRP effects, bank swap. | Wrong address or a missing erase fails in simulation the way it does on silicon. |
| C-03 | P1 | Simulated BOOT: reads Appendix B metadata and the IBWH header, selects a slot, rejects bad CRC, trial boot and rollback. | Corrupt metadata produces "no app" in simulation. |
| C-04 | P1 | Fault injection that covers real failures: SWD drop mid-write, VCP not enumerated, power brown-out during option-byte write, wrong board revision, prompts that time out. | Each fault ends in ABORTED/INCOMPLETE with the recorded safe state. |
| C-05 | P2 | Scripted scenarios (`--scenario yield-95.yaml`) for demos and dashboard testing. | Dashboard shows realistic yield and Pareto. |

### Phase D — Identity, serials and MAC

| ID | Pri | Task | Done when |
|---|---|---|---|
| D-01 | P0 | Align `identity.py` with A-02. Encoder/decoder test vectors are shared with the firmware. | Same bytes from host and firmware tests. |
| D-02 | P1 | MAC sources: an IEEE OUI block from the work order, a pool imported from CSV, or locally administered from the UID. Optionally read an EUI-48 from an external EEPROM over a test command. Warn on a locally administered MAC in production profiles. | Each source tested. Duplicates are impossible (unique index). |
| D-03 | P1 | Serial number formats: template tokens (`{yyww}`, `{counter}`, `{luhn}`, `{site}`, `{line}`, `{rev}`), per-product counters, and reservation for pre-printed labels. | Templates validated at profile load. |
| D-04 | P1 | Rework and retest: same UID → same serial and MAC; a new PCB on an old serial needs a supervisor; scrap releases nothing. | All three cases covered by tests. |
| D-05 | P2 | Optional per-unit secrets: device key pair or certificate generated on host (or HSM/KMS), public part recorded, private part written once and never logged. Protect it with WRP + RDP. | Off by default. Key never appears in DB, logs or exports. |
| D-06 | P2 | Customer and order fields from the MES or work order file, written as TLV 0x40–0x4F. | Values appear on label and certificate. |

### Phase E — Protection and security of the unit

| ID | Pri | Task | Done when |
|---|---|---|---|
| E-01 | P0 | Order: all SWD readbacks before RDP 1, then WRP on BOOT (sectors 0–1) and the identity sector, then BOR, then RDP 1 last. Power cycle and read back OPTCR/OPTCR1. | Protection verified after power cycle. |
| E-02 | P0 | Keep RDP 2 blocked everywhere (profile, CLI, UI, OpenOCD wrapper) with tests. | Any request for RDP 2 is refused with a clear message. |
| E-03 | P1 | Supervisor rework: RDP 1 → 0 with mass erase, audited, with a reason code. | Audit log shows who, when, why. |
| E-04 | P1 | Check that BOOT and the application still run under RDP 1 + WRP (VCP update still works, the app confirms). | Boot check runs after protection on hardware. |
| E-05 | P2 | Optional PCROP/secure-area study for keys from D-05. | Decision recorded. |

### Phase F — Station, fixtures and hardware

| ID | Pri | Task | Done when |
|---|---|---|---|
| F-01 | P0 | Probe management: list ST-LINKs (serial, firmware version), bind to fixtures, find the VCP from the same USB serial on Linux and Windows, warn on old ST-LINK firmware. | Station dialog shows each fixture's probe and port. |
| F-02 | P1 | One OpenOCD per fixture with fixed telnet/TCL/GDB ports, health check, restart on crash, adapter speed from the station config, `hla_serial`/`adapter serial` selection. | Eight fixtures run at once without port clashes. |
| F-03 | P1 | Power control drivers tested on hardware: USB hub port power (uhubctl), relay board, programmable supply (SCPI) with current limit and current measurement as a test. | Supply current is logged per unit. |
| F-04 | P1 | Fixture inputs: lid/clamp switch and board-present detection (GPIO or USB relay inputs). A run starts only when the fixture is closed. | Opening the lid aborts safely. |
| F-05 | P1 | Golden-board self-test on real hardware, with an expiry interval enforced before production starts. | Production blocked when the self-test is due or failed. |
| F-06 | P1 | Pogo pin / insertion counter with maintenance limit and supervisor reset. | Warning at limit, block at limit + margin. |
| F-07 | P2 | Optional instruments: DMM/scope over SCPI, Ethernet loopback partner for `ETH.*`, an external UART partner for `UART.*`. | Tests that need them are skipped unless configured. |
| F-08 | P2 | Barcode scanner support: HID keyboard wedge and serial scanners, prefix/suffix rules, per-fixture routing. | Scanning into any fixture works without focus. |

### Phase G — Operator and supervisor UI

| ID | Pri | Task | Done when |
|---|---|---|---|
| G-01 | P0 | Clear failure reasons on tiles: probe not found, SWD error, VCP missing, test X failed (value vs limit), identity mismatch, printer offline — each with a suggested action. | No "station error" without a cause. |
| G-02 | P0 | Hardware bring-up page (engineer role): detect probes, read chip info, option bytes, flash a file, open VCP, `Hello`, run single tests. | Engineer can diagnose a fixture without the CLI. |
| G-03 | P1 | Work order screen: load from file or MES, quantity, serial/MAC usage bar, close order. | Order progress visible live. |
| G-04 | P1 | Operator prompts from `OperatorPrompt` rendered by kind (yes/no, line entry, button press, equipment, destructive) with a timeout countdown. | All five prompt kinds work on a real board. |
| G-05 | P1 | Unit search and history: by serial, MAC, UID, PCB serial, order, date; reprint, certificate, rework start. | Any unit found in under a second at 100k units. |
| G-06 | P1 | Profile manager: import signed profiles, show the signature/hash status, release/retire profiles, choose a profile per work order. | Unsigned profile refused when signatures are required. |
| G-07 | P2 | Yield dashboard: first-pass yield, retest yield, Pareto by test, cycle time per step, per fixture, per shift. | Matches the database totals. |
| G-08 | P2 | Localisation (at least English plus one line language) and large-font kiosk mode. | All operator strings translatable. |
| G-09 | P3 | Sounds and stack-light output (USB light tower) for PASS/FAIL/attention. | Light follows fixture state. |

### Phase H — Traceability, MES and reports

| ID | Pri | Task | Done when |
|---|---|---|---|
| H-01 | P1 | Store per run: profile + all image hashes, option bytes before/after, every test result with limits, calibration, identity record bytes, operator, station, fixture, probe serial, durations. | One query reconstructs everything that was done to a unit. |
| H-02 | P1 | MES uplink tested against a mock server: retries with back-off, idempotency key per run, queue visible in the UI, alarm when the queue grows. | No duplicates after network loss. |
| H-03 | P1 | Exports: CSV/JSON per order and date range, per-unit PDF certificate, daily summary. | Exports open in Excel and match the DB. |
| H-04 | P2 | Database backup and rotation, integrity check on start, schema migrations with versioning. | Upgrading the app keeps old data. |
| H-05 | P2 | Optional central database (PostgreSQL) for several stations sharing one serial/MAC pool. | Two stations never allocate the same serial. |

### Phase I — Access and app security

| ID | Pri | Task | Done when |
|---|---|---|---|
| I-01 | P1 | Force a PIN change on the default `admin` account before production use. | Production mode refuses the default PIN. |
| I-02 | P1 | Role checks on every action (UI and CLI): rework, reprint, profile import, station settings, RDP lowering. | Unauthorised action refused and audited. |
| I-03 | P2 | Profile signing workflow: offline key, `hwval-profile sign`, trusted keys in the station config, rotation. | Tampered image file refused at load. |
| I-04 | P2 | Secrets (MES token, HSM credentials) from the OS keyring or environment, never from YAML in the repo. | No secret in config, DB or logs. |

### Phase J — Testing, CI and hardware-in-the-loop

| ID | Pri | Task | Done when |
|---|---|---|---|
| J-01 | P0 | Add a `tests/` pytest suite (already listed in `pyproject.toml`): HWLINK frames, IBWH parse, metadata records, identity record, option-byte encoding, serial/MAC allocation, profile validation. | `make test` green in CI. |
| J-02 | P0 | Cross-repo contract tests: compare the host's constants with `boot_layout.h`, `hwlink.fbs` and `mkimage.py` in CI. | Changing a firmware constant fails the factory CI. |
| J-03 | P1 | End-to-end simulator tests (pytest-qt for the UI): PASS, each fail path, abort, rework, reprint. | Every step's failure path covered. |
| J-04 | P1 | Hardware-in-the-loop job on a Jenkins node with a NUCLEO-F767ZI: full run, rework, protection, recovery. | Nightly HIL run on real silicon. |
| J-05 | P2 | Turn on type checking (`mypy --strict`) and ruff as gates instead of warnings. | CI fails on new errors. |

### Phase K — Packaging, deployment and docs

| ID | Pri | Task | Done when |
|---|---|---|---|
| K-01 | P1 | Bundle or check OpenOCD ≥ 0.12 and the ST-LINK udev rules / Windows driver in the installers. | Fresh PC runs the station after one install. |
| K-02 | P1 | Station first-run wizard: station ID, database path, printer, fixtures, profile store, admin PIN. | No YAML editing needed for a standard station. |
| K-03 | P1 | Rewrite the README to state what is verified on hardware and what is simulation only. Update the operator, supervisor and engineering guides with the real flow, bring-up and recovery. | Docs match the code. |
| K-04 | P2 | Recovery guide: board stuck in bench config, RDP 1 board, wrong option bytes, BOOT without app, connect-under-reset. | Each case has a tested procedure. |
| K-05 | P2 | Release process: versioned station builds, changelog, profile compatibility (`min_factory_version`). | Old station refuses a newer profile it can't run. |

---

## 4. Order of work

1. **A-01, A-02, A-05, B-01 … B-12, J-01, J-02** — one real board goes from
   blank to PASS, with BOOT, the IBWH application, serial and MAC written and
   read back.
2. **C-01 … C-04, A-03, A-06, D-01, E-01 … E-04** — the simulator matches the
   hardware, the application uses its identity and protection is verified.
3. **F, G-01 … G-06, H-01 … H-03, I-01, I-02, K-01 … K-03, J-03, J-04** —
   ready for the line.
4. Everything else (P2/P3) for scale and audit.

## 5. Definition of done for the station

* A blank NUCLEO-F767ZI on fixture F1 reaches PASS from the GUI with one scan.
* After the run the board boots BOOT → slot A application, the application
  reports the provisioned serial number and MAC over HWLINK and uses that MAC
  on Ethernet.
* Option bytes read back as `nDBANK = 0`, `nDBOOT = 1`, BOR as profiled, WRP on
  BOOT and the identity sector, RDP 1.
* The label shows the same serial and MAC. The database, the certificate and
  the MES payload agree with the board.
* Pulling the USB cable at any step leaves a recorded safe state, and the next
  run on that board recovers it.
* The same flow passes in the simulator and on hardware with the same host code.
