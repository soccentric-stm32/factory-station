# Software Requirements Specification — HWVAL Factory (End-of-Line Test and Provisioning)

| | |
|---|---|
| **Document** | SRS-FACTORY-F767 |
| **Version** | 0.1 (draft) |
| **Author** | Sandesh Ghimire <sandesh@soccentric.com> |
| **Date** | 2026-11-03 |
| **Status** | Draft for review |
| **Target** | NUCLEO-F767ZI (reference product) and boards based on the STM32F767ZI |
| **Software base** | Python ≥ 3.11, PySide6 (Qt 6), pyserial, flatbuffers, SQLite, OpenOCD ≥ 0.12; shared host core package (§3) |

---

## Contents

1. [Introduction](#1-introduction)
2. [Overall description](#2-overall-description)
3. [Architecture and shared core](#3-architecture-and-shared-core)
4. [Station and fixtures](#4-station-and-fixtures)
5. [Product profiles](#5-product-profiles)
6. [End-of-line flow](#6-end-of-line-flow)
7. [Unit identity and the provisioning record](#7-unit-identity-and-the-provisioning-record)
8. [Serial numbers](#8-serial-numbers)
9. [Protection and option bytes](#9-protection-and-option-bytes)
10. [Labels](#10-labels)
11. [Traceability and data](#11-traceability-and-data)
12. [User interface](#12-user-interface)
13. [Users and access](#13-users-and-access)
14. [Packaging and deployment](#14-packaging-and-deployment)
15. [Non-functional requirements](#15-non-functional-requirements)
16. [Verification matrix](#16-verification-matrix)
- [Appendix A — Provisioning record format](#appendix-a--provisioning-record-format)
- [Appendix B — Product profile example](#appendix-b--product-profile-example)
- [Appendix C — Change requests to other projects](#appendix-c--change-requests-to-other-projects)
- [Appendix D — Database schema](#appendix-d--database-schema)
- [Appendix E — Open items](#appendix-e--open-items)

---

## 1. Introduction

### 1.1 Purpose

This document specifies **HWVAL Factory** (working name), the end-of-line
(EOL) test and provisioning station for STM32F767ZI boards. On the production
line it takes a freshly assembled board and, in one operator action, tests it,
programs the production firmware, gives it a unique identity, locks it, prints
its label and records everything for traceability.

It is the production counterpart of HWVAL Studio (SRS-GUI-F767): Studio is
the engineering tool for validating and debugging boards; Factory is the
locked-down tool that operators run thousands of times a day.

### 1.2 Scope

The station shall:

- run 1 to 8 fixtures in parallel from one PC, each with its own ST-LINK;
- identify each board's chip over SWD and refuse the wrong part;
- run an EOL test plan with the HWVAL firmware over HWLINK;
- program the bootloader and the application image into the product flash
  layout over SWD, including the bootloader metadata;
- write a per-unit provisioning record: serial number, hardware revision,
  MAC address, manufacturing data and calibration values;
- verify that the production firmware boots and reports the right identity;
- apply the configured flash protections;
- print a label, and record every step, measurement and result;
- work offline, and optionally report to a manufacturing execution system
  (MES).

It does not cover functional testing that needs custom fixture electronics
beyond the HWVAL harness and instruments, secure key injection with an HSM, or
firmware development tasks (those belong to HWVAL Studio).

### 1.3 Definitions

| Term | Meaning |
|---|---|
| **Factory** | The station application specified here |
| **Studio** | HWVAL Studio, the engineering GUI (SRS-GUI-F767) |
| **EOL** | End of line: the last test and configuration step before packing |
| **Station** | One PC running Factory, with its fixtures |
| **Fixture** | One test position: a bed or cable set holding one board, with one ST-LINK (SWD + VCP) |
| **DUT / unit** | Device under test: the board in a fixture |
| **Product profile** | A versioned, locked package of everything needed to build one product: firmware images, test plan, option bytes, identity rules, label (§5) |
| **Provisioning record** | The per-unit identity and calibration data stored in flash (§7, Appendix A) |
| **Run** | One pass of one unit through the EOL flow |
| **First pass yield (FPY)** | Share of units that pass on their first run |
| **MES** | Manufacturing execution system: the factory's central production database |
| **HWLINK** | The UART protocol shared by all firmware and host tools ([LINK]) |
| **Bench / product configuration** | Single-bank flash with HWVAL / dual-bank flash with BOOT and slots ([BOOT] §2.5) |

### 1.4 Requirement conventions

**shall / should / may**; IDs `<AREA>-<NNN>`; verification methods **T**
(test, automated against the simulator or on a station), **D**
(demonstration), **I** (inspection), **A** (analysis).

### 1.5 References

| ID | Document |
|---|---|
| [GUI] | [validation-gui/SRS.md](https://github.com/soccentric-stm32/validation-gui/blob/HEAD/SRS.md) — SRS-GUI-F767, HWVAL Studio |
| [LINK] | [GUI] Appendix A — HWLINK protocol (normative) |
| [HWVAL] | [validation-firmware/SRS.md](https://github.com/soccentric-stm32/validation-firmware/blob/HEAD/SRS.md) — SRS-HWVAL-F767 |
| [BOOT] | [bootloader/SRS.md](https://github.com/soccentric-stm32/bootloader/blob/HEAD/SRS.md) — SRS-BOOT-F767 |
| [DEMO] | [app-c-freertos/SRS.md](https://github.com/soccentric-stm32/app-c-freertos/blob/HEAD/SRS.md) — SRS-DEMO-F767 (and the Rust and Zephyr variants) |
| [HW] | [Hardware.md](https://github.com/soccentric-stm32/validation-firmware/blob/HEAD/Hardware.md) |
| [RM0410] | ST, STM32F76xxx/F77xxx reference manual (option bytes, RDP, WRP, OTP) |
| [OOCD] | OpenOCD user guide (`stm32f2x` flash driver, `adapter serial`) |
| [ZPL] | Zebra Programming Language II reference |

---

## 2. Overall description

### 2.1 Product perspective

```
                          Station PC (Windows / Linux)
 +--------------------------------------------------------------------------------+
 |  HWVAL Factory (PySide6)                                                        |
 |   fixture tiles · operator login · scanner input · yield · supervisor tools    |
 |                                                                                |
 |   Station engine ── one worker per fixture ──┬─ OpenOCD (adapter serial A)    |
 |        │                                     ├─ HWLINK session (ttyACM A)     |
 |   Profile store (locked)  Serial allocator   ├─ ...                           |
 |   SQLite run database     Label printer      └─ OpenOCD / HWLINK for fixture N |
 |   MES uplink (optional, queued)                                                |
 +-------│──────────────│───────────────────────────│─────────────────│---------+
         │ USB          │ USB                       │ TCP 9100        │ HTTPS
     ST-LINK A ... ST-LINK N                    label printer      MES (optional)
         │
   DUT in fixture A  (SWD + VCP, optional harness / power control)
```

### 2.2 Users

| User | Use |
|---|---|
| Line operator | Logs in, scans, places boards, presses Start, reads PASS/FAIL, applies labels |
| Line supervisor | Selects the work order and product profile, handles reworks and retests, sees yield |
| Test / manufacturing engineer | Builds and releases product profiles, investigates failures, maintains stations |
| Quality | Reviews traceability records and audit logs |

### 2.3 Constraints

| ID | Constraint |
|---|---|
| FCON-001 | Factory shall be written in Python ≥ 3.11 with PySide6 (Qt 6), like Studio. |
| FCON-002 | Factory shall run on Windows 10/11 x64 and Linux x86-64 (reference: Ubuntu 22.04/24.04). macOS should work for development but is not a production target. |
| FCON-003 | SWD access shall go through OpenOCD, one process per fixture, selected by ST-LINK serial number (`adapter serial`). UART access shall use HWLINK through the shared core (§3). |
| FCON-004 | Factory shall reuse the shared host core package for HWLINK, OpenOCD control, image handling, theme and widgets (§3); it shall not copy that code. |
| FCON-005 | All firmware it programs shall come from a released product profile with pinned SHA-256 hashes (§5); Factory shall never program a file chosen ad hoc on the line. |

### 2.4 Assumptions

| ID | Assumption |
|---|---|
| FASM-001 | Each fixture has its own ST-LINK/V2-1 or V3 connected to the DUT's SWD, NRST and USART3 (VCP), and the VCP works in both directions. |
| FASM-002 | Boards arrive blank or with unknown contents; the flow starts with a full erase. |
| FASM-003 | The station PC has OpenOCD installed (or bundled, [GUI] OPEN-G02) and the ST-LINK USB rules (Linux). |
| FASM-004 | The HWVAL test harness wires needed by the EOL test plan (level L) are built into the fixture. |

---

## 3. Architecture and shared core

| ID | Requirement | Method |
|---|---|---|
| ARCH-001 | HWLINK codec and session, OpenOCD process control, firmware image parsing (BOOT header, ELF/HEX/BIN), the theme (colour tokens, QSS, SVG icon set) and common widgets shall live in one shared Python package, **`soccentric-stm32-hostcore`**, used by both Studio and Factory (OPEN-F01 decides its repository). | I |
| ARCH-002 | Factory shall be layered as Studio: transport, protocol, services, persistence, UI ([GUI] ARC-001). The UI shall never block on I/O. | I, T |
| ARCH-003 | Each fixture shall run in its own worker (thread or process) with its own OpenOCD process, serial port and state machine. A crash, hang or USB loss on one fixture shall not affect the others. | T |
| ARCH-004 | The EOL flow shall be a declarative sequence of steps defined by the product profile (§5, §6), executed by one step engine; steps shall be individually timed, logged and retried according to the profile. | I, T |
| ARCH-005 | Factory shall include a fixture simulator (simulated OpenOCD and HWLINK devices, built on the Studio simulator, [GUI] ARC-006) so that the whole station can be tested and demonstrated without hardware. | T |
| ARCH-006 | A command-line mode (`hwval-factory --cli --profile … --fixture …`) shall run one unit headless with the exit codes 0 pass, 1 fail, 2 station error. | T |

---

## 4. Station and fixtures

| ID | Requirement | Method |
|---|---|---|
| STN-001 | A station configuration file shall define: station ID and name, line, 1–8 fixtures (name, ST-LINK serial, serial port or USB location, optional power-control channel), label printer, database location and the MES endpoint. | I |
| STN-002 | Factory shall detect connected ST-LINKs at start-up and whenever USB devices change, match them to configured fixtures by serial number, and show unconfigured or missing probes. | T |
| STN-003 | Each fixture shall find its VCP by the ST-LINK's USB serial number (same physical probe), not by port name order. | T |
| STN-004 | Fixtures shall run independently: an operator can start, abort and unload any fixture while others run. | T |
| STN-005 | Factory may control fixture power (USB hub port power, a relay board, or a programmable supply) through a pluggable driver, to power-cycle a DUT between steps. | T |
| STN-006 | A station self-test (supervisor menu) shall check each fixture with a known-good "golden" board and record the result; the station shall warn when no passing self-test was recorded in the configured interval (default 24 h). | T |
| STN-007 | Factory shall record per fixture the number of insertions and runs, and warn when a configurable maintenance count (e.g. pogo-pin life) is reached. | T |

---

## 5. Product profiles

| ID | Requirement | Method |
|---|---|---|
| PRF-001 | A product profile shall be a directory or archive containing a manifest (YAML) and its files: the HWVAL test image, the BOOT image, one or more application images (`.img`), the EOL test plan, option-byte settings, identity rules, the label template and calibration rules (Appendix B). | I |
| PRF-002 | The manifest shall pin every file by SHA-256 and carry the profile ID, version, product name, hardware revisions it applies to, and the release author and date. Factory shall refuse a profile whose files do not match. | T |
| PRF-003 | Profiles shall be created and released by an engineer (in Studio or a CLI tool) and imported into Factory read-only. A released profile shall never be edited on the station; a change means a new profile version. | I, T |
| PRF-004 | The active profile and version shall be shown on every screen and stored with every run. | T |
| PRF-005 | Factory should verify a profile signature (Ed25519) when the station is configured to require it (OPEN-F03). | T |
| PRF-006 | A work order (scanned or entered by the supervisor) shall select the profile, the quantity, and the serial-number range or pool (§8); runs shall be counted against it. | T |

---

## 6. End-of-line flow

### 6.1 Default sequence

| Step | Action | Over | Pass condition |
|---|---|---|---|
| 1 | **Scan** board barcode (PCB serial) and optional work order | Scanner | Valid format, not already passed in this work order (unless rework) |
| 2 | **Identify** chip: DEV_ID, REV_ID, flash size, 96-bit UID, option bytes | SWD | Matches the profile's allowed parts and revisions |
| 3 | **Prepare for test:** bench configuration (`nDBANK = 1`), mass erase, program HWVAL test image, verify, reset | SWD | Programmed and verified |
| 4 | **Test:** connect over HWLINK, check `HelloReply` (role HWVAL, image hash), run the profile's EOL test plan with operator prompts | HWLINK | All required tests pass; measured values within the profile's limits |
| 5 | **Calibrate** (optional): read values measured in step 4 (e.g. ADC/VREFINT, HSI trim, RTC offset) and compute calibration data per profile rules | — | Within the allowed range |
| 6 | **Program production:** product configuration (`nDBANK = 0`, BOR level), mass erase, program BOOT, the application into slot A and its metadata records (`HEADER`, `VERIFIED`, `ACTIVATED`, `CONFIRMED`, [BOOT] Appendix B), verify | SWD | Programmed and verified; CRC32 matches |
| 7 | **Provision:** allocate the serial number (§8), build the provisioning record (§7) and write it | SWD | Written and read back |
| 8 | **Boot check:** reset, connect over HWLINK, check `HelloReply` (role, firmware name and version, slot A confirmed) and that the reported serial and UID match the record | HWLINK | Identity matches; boot within the profile's timeout |
| 9 | **Protect:** apply the profile's write protection and readout protection (§9) | SWD | Option bytes read back as configured |
| 10 | **Label:** print the label (§10) | Printer | Printer accepted the job |
| 11 | **Record:** close the run record, queue it for the MES | DB | Committed |

### 6.2 Requirements

| ID | Requirement | Method |
|---|---|---|
| EOL-001 | Factory shall execute the sequence of §6.1 as defined by the profile; profiles may disable optional steps (5, 9, 10) and add retries per step. | T |
| EOL-002 | A step failure shall stop the run, mark the unit FAIL with the step, the reason, the measured value and the limit, and leave the unit in a safe state: never between "erased" and "protected" without the record saying so. | T |
| EOL-003 | The EOL test plan shall be a list of HWVAL test IDs (Appendix B) with optional per-test limit overrides; Factory shall run it with `Run` over HWLINK ([LINK] A.3) and shall not allow destructive or level E tests unless the profile lists them. | T |
| EOL-004 | Operator prompts from the test plan ([HWVAL] CLI-014) shall appear as large dialogs on the fixture's tile, naming the fixture, with the operator's answer recorded in the run. | D, T |
| EOL-005 | Step 6 shall compose the bootloader metadata records itself from the application image header, so that the unit boots slot A as confirmed without a trial boot ([BOOT] CR-FAC-01). | T |
| EOL-006 | Steps 3 and 6 shall each verify flash contents against the image (OpenOCD `verify_image`) and against the expected CRC32. | T |
| EOL-007 | A **retest** (same PCB serial) shall be allowed only with supervisor permission after the configured number of failures (default 2), and shall be recorded as a retest. A **rework** run shall re-use the unit's existing serial number (§8). | T |
| EOL-008 | Abort (fixture button, Esc) shall stop the run at the next safe point, release the probe and record ABORTED. | T |
| EOL-009 | The run time of every step shall be recorded; the station shall show the cycle time per fixture and per profile. | T |
| EOL-010 | After a station crash or power loss, Factory shall mark interrupted runs INCOMPLETE on restart, and the unit shall be treated as untested. | T |

---

## 7. Unit identity and the provisioning record

### 7.1 Location

The record lives in flash, not in OTP, so that it can be rewritten for
rework. It uses **bank 2, sector 1** of the product flash layout (16 KB,
physical bank 2 offset `0x4000`): `0x0810 4000` when `SWP_FB = 0`, and
`0x0800 4000` when bank 2 is swapped in. This sector is today part of BOOT's
reserved area ([BOOT] §3.1); Appendix C, CR-FAC-02, narrows that reservation to
bank 2 sector 0.

### 7.2 Requirements

| ID | Requirement | Method |
|---|---|---|
| PRV-001 | The provisioning record shall follow Appendix A: a fixed header (magic, format version, length, CRC32) and type-length-value (TLV) fields. | I, T |
| PRV-002 | Mandatory fields: serial number, product ID, hardware revision, chip UID (copied from the chip, binding the record to the silicon), manufacturing date and time (UTC), station ID, fixture, profile ID and version, firmware version programmed. | T |
| PRV-003 | Optional fields defined by the profile: Ethernet MAC address (§8.2), calibration values (step 5), customer and order references, and custom key/value pairs (≤ 64 bytes each). | T |
| PRV-004 | Factory shall write the record over SWD, read it back and compare byte for byte, and check its CRC32. | T |
| PRV-005 | A rework run shall erase the sector and write a new record with a higher record revision, keeping the serial number; the old record shall be kept in the database. | T |
| PRV-006 | Firmware (HWVAL, BOOT-based applications) shall read the record through a small shared C module and report the serial number in `HelloReply`/`Status` (Appendix C, CR-FAC-03). | T |
| PRV-007 | The record shall contain no secret keys in v0.1; if a profile asks for a secret, Factory shall refuse it and point to OPEN-F04 (secure provisioning needs readout protection and a key source). | I |
| PRV-008 | OTP shall not be written by default. A profile may enable writing a single "provisioned" OTP marker (serial number hash + date) as a tamper-evident record; this needs a second confirmation by an engineer when the profile is released. | T |

---

## 8. Serial numbers

### 8.1 Allocation

| ID | Requirement | Method |
|---|---|---|
| SN-001 | Serial numbers shall follow a profile-defined format (prefix, year/week, product code, counter, optional check digit), e.g. `SC767-2645-000123-7`. | T |
| SN-002 | Each serial number shall be allocated exactly once across all fixtures and restarts. Allocation shall be atomic in the station database (one transaction), or delegated to the MES when the station is configured online. | T |
| SN-003 | A serial number is **consumed** when it is written to a unit (step 7). Serial numbers allocated for runs that fail before step 7 shall return to the pool; serial numbers written to a unit that later fails shall stay bound to that unit's UID. | T |
| SN-004 | Ranges or pools shall be loaded per work order; Factory shall warn at 90 % use and stop when a range is exhausted. | T |
| SN-005 | A rework unit is recognised by its chip UID (read in step 2); Factory shall re-use its serial number and MAC address. | T |

### 8.2 MAC addresses

| ID | Requirement | Method |
|---|---|---|
| MAC-001 | When the profile enables it, Factory shall allocate one Ethernet MAC address per unit from a configured OUI block, with the same uniqueness and consumption rules as serial numbers. | T |
| MAC-002 | Factory shall refuse multicast or locally administered addresses unless the profile explicitly allows them (development profiles). | T |

---

## 9. Protection and option bytes

| ID | Requirement | Method |
|---|---|---|
| PROT-001 | The profile shall define the final option bytes: `nDBANK`, `nDBOOT`, `BOR_LEV` (default level 3, [HW] §5.2 recommendation), write protection (`nWRP`) per sector, and readout protection level (`RDP`). | I |
| PROT-002 | Default production protections: BOR level 3; write protection on bank 1 sectors 0–1 (BOOT) and the provisioning sector; RDP level 0 for development profiles, level 1 for production profiles. | I |
| PROT-003 | Factory shall **never** set RDP level 2 (permanent, disables debug for good); the code path shall not exist. | I |
| PROT-004 | Write protection on BOOT conflicts with BOOT updating itself; this is accepted because BOOT is not field-updatable ([BOOT] OPEN-B04). The application can still confirm and update its own slots and metadata, which stay writable. | A |
| PROT-005 | Factory shall read back all option bytes after step 9 and fail the run on any difference. | T |
| PROT-006 | To rework a protected unit, a supervisor action shall lower RDP to level 0 (which mass-erases the chip, [RM0410]) and remove write protection, with a confirmation that states that all flash content is lost. | T |

---

## 10. Labels

| ID | Requirement | Method |
|---|---|---|
| LBL-001 | Labels shall be printed on a Zebra-compatible printer with ZPL II, sent raw over TCP port 9100 or to a local print queue. | T |
| LBL-002 | The label template (part of the profile) shall support text fields, a Code 128 barcode and a Data Matrix or QR code, with placeholders for serial number, MAC, product, hardware revision, date and firmware version. | T |
| LBL-003 | Factory shall render a preview of the label in the UI, and a test-print action for supervisors. | D |
| LBL-004 | A reprint shall need supervisor permission, be recorded, and print the same data. | T |
| LBL-005 | Without a printer configured, Factory shall save the label as PDF/PNG and mark the run as "label pending". | T |

---

## 11. Traceability and data

| ID | Requirement | Method |
|---|---|---|
| TRC-001 | Every run shall be stored in a local SQLite database (WAL mode, written as steps complete) with: unit identity (PCB serial, chip UID, serial number, MAC), station, fixture, operator, work order, profile and version, every step with timing and result, every test result with measured values and limits, option bytes read back, and the label data (Appendix D). | T |
| TRC-002 | Records shall be append-only: corrections are new records referring to the old one; nothing is updated in place or deleted from the station. | I, T |
| TRC-003 | Factory shall search runs by any identity field and show a unit's full history (all runs, reworks, label reprints). | D |
| TRC-004 | When an MES endpoint is configured, Factory shall send each closed run as JSON over HTTPS, queue it while offline, retry with back-off, and show the queue length. The local database stays the source of record. | T |
| TRC-005 | Export: CSV and JSON per work order or date range, and a per-unit PDF certificate of test. | T |
| TRC-006 | An audit log shall record logins, profile imports, work-order changes, retests, reworks, reprints, protection changes and configuration edits, with user and time. | T |
| TRC-007 | Results shall use the same structured measured values as HWVAL ([HWVAL] TFW-013), so that data from Studio and Factory can be compared. | I |

---

## 12. User interface

### 12.1 Look and feel

| ID | Requirement | Method |
|---|---|---|
| UI-001 | Factory shall use the same macOS-inspired light theme, SVG icon set, typography and menu structure conventions as Studio ([GUI] §4, §6), from the shared core. | D |
| UI-002 | The main screen shall be readable from 1.5 m on a 24" screen at 1920 × 1080: large fixture tiles, high-contrast PASS/FAIL, minimum 18 px body text in operator mode. | D |
| UI-003 | Colour shall never be the only signal: each state has an icon, a word and a colour (IDLE, LOAD, RUNNING, ACTION, PASS, FAIL, ERROR). | I |
| UI-004 | Factory should support full-screen kiosk mode with the window menu hidden for operators. | D |

### 12.2 Screens

| ID | Requirement | Method |
|---|---|---|
| UI-010 | **Station view:** one tile per fixture showing state, current step and progress, elapsed time, unit serial, last result and the failing step; a header with operator, work order, profile and version, counts (pass / fail / FPY) and the MES queue state. | D |
| UI-011 | **Fixture detail:** the step list with timings, the live HWLINK test results, the OpenOCD log, operator prompts and the label preview. | D |
| UI-012 | **Yield dashboard:** FPY, units per hour, cycle time and a failure Pareto by step and by test ID, per work order and per day. | D |
| UI-013 | **Unit history:** search and view all runs of a unit (TRC-003). | D |
| UI-014 | **Supervisor tools:** work order selection, retest/rework approval, label reprint, station self-test, profile import, fixture configuration. | D |
| UI-015 | **Menus:** File (Export, Print certificate, Quit), Station (Start all, Abort all, Self-test, Fixtures…), Work Order (Select…, Close), Tools (Profiles…, Label printer…, Unit history), View (Station, Dashboard, Kiosk mode), Help (User guide, About). | D |

### 12.3 Operator interaction

| ID | Requirement | Method |
|---|---|---|
| UI-020 | A barcode scanner in keyboard (HID) mode shall work without focus handling: scans go to the fixture that is waiting for a scan, in the order configured. | T |
| UI-021 | One key per fixture (F1–F8) and on-screen buttons shall start and abort fixtures. | T |
| UI-022 | Failure shall be signalled with a red tile, the failing step in words and a sound; PASS with a green tile and a different sound. | D |

---

## 13. Users and access

| ID | Requirement | Method |
|---|---|---|
| ACC-001 | Users shall log in by scanning a badge or with ID and PIN. Roles: operator, supervisor, engineer, administrator. | T |
| ACC-002 | Operators can run units; supervisors can also select work orders, approve retests and reworks, reprint labels and run self-tests; engineers can also import profiles and edit station configuration; administrators manage users. | T |
| ACC-003 | PINs shall be stored as salted hashes (scrypt or Argon2). | I |
| ACC-004 | Idle operator sessions shall lock after a configured time (default 30 min). | T |

---

## 14. Packaging and deployment

| ID | Requirement | Method |
|---|---|---|
| PKG-001 | Factory shall be packaged with PyInstaller as a Windows `.msi` and a Linux `.AppImage`/`.deb`, as Studio ([GUI] §16). | D |
| PKG-002 | Station configuration, profiles and the database shall live outside the installation directory, so that updating Factory keeps them. | I |
| PKG-003 | Factory shall check at start-up that its own version is allowed by the active profile (minimum version field). | T |
| PKG-004 | A station shall be reproducible from an installer, a station configuration file and a profile archive. | D |

---

## 15. Non-functional requirements

| ID | Requirement | Method |
|---|---|---|
| FNFR-001 | **Cycle time:** steps 2–11 for the reference profile shall take at most 120 s per unit on one fixture, excluding operator actions; with 4 fixtures the station shall reach at least 100 units per hour. | T |
| FNFR-002 | **Isolation:** a fault on one fixture (USB unplug, OpenOCD crash, DUT short) shall not stop or slow the other fixtures by more than 10 %. | T |
| FNFR-003 | **Robustness:** 1000 simulated runs across 8 fixtures with random USB and power faults shall produce no duplicate serial number and no run without a final state. | T |
| FNFR-004 | **Data safety:** no committed run record shall be lost on power loss (WAL, fsync on run close). | T |
| FNFR-005 | **Responsiveness:** the UI shall stay responsive (< 100 ms) with 8 fixtures running. | T |
| FNFR-006 | **Code quality:** type hints, `ruff` and `mypy --strict` clean, pytest + pytest-qt against the fixture simulator, ≥ 80 % coverage of non-UI code. | I, T |
| FNFR-007 | **Documentation:** operator quick guide (one page per screen), supervisor guide, engineering guide for profiles and stations. | I |

---

## 16. Verification matrix

| Area | Requirements | Main method |
|---|---|---|
| Architecture | ARCH-001…006 | I, T |
| Station | STN-001…007 | T |
| Profiles | PRF-001…006 | T, I |
| EOL flow | EOL-001…010 | T |
| Provisioning record | PRV-001…008 | T |
| Serial numbers and MAC | SN-001…005, MAC-001…002 | T |
| Protection | PROT-001…006 | T, I |
| Labels | LBL-001…005 | T, D |
| Traceability | TRC-001…007 | T |
| UI | UI-001…022 | D, T |
| Access | ACC-001…004 | T |
| Packaging | PKG-001…004 | D, T |
| Non-functional | FNFR-001…007 | T, I |

---

## Appendix A — Provisioning record format

Little-endian. The sector is erased before writing; the record starts at the
sector base.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 4 | `magic` | `0x50525631` (`"1VRP"`) |
| 4 | 2 | `format` | Record format version, `1` |
| 6 | 2 | `revision` | Record revision; +1 on every rework (PRV-005) |
| 8 | 4 | `length` | Length of the TLV area in bytes |
| 12 | 4 | `tlv_crc32` | CRC-32/ISO-HDLC of the TLV area |
| 16 | 12 | `uid` | Chip 96-bit unique ID (binds the record to the silicon) |
| 28 | 4 | `header_crc32` | CRC32 of bytes 0–27 |
| 32 | n | TLV area | `type` (1 byte), `len` (1 byte), `value` (`len` bytes), repeated; ends with type `0x00` |

| Type | Field | Value |
|---|---|---|
| `0x01` | Serial number | ASCII, ≤ 32 bytes |
| `0x02` | Product ID | ASCII, ≤ 32 bytes |
| `0x03` | Hardware revision | ASCII, ≤ 8 bytes |
| `0x04` | Manufacturing time | `uint32` Unix time (UTC) |
| `0x05` | Station ID / fixture | ASCII `STATION/FIXTURE` |
| `0x06` | Profile ID and version | ASCII |
| `0x07` | Firmware version | `uint32` as in the BOOT image header |
| `0x10` | Ethernet MAC | 6 bytes |
| `0x20`–`0x2F` | Calibration values | Profile-defined (`int32` or `float32` with a unit code) |
| `0x40`–`0x4F` | Customer / order references | ASCII |
| `0x80`–`0xFE` | Custom key/value | Profile-defined |

A reader shall reject a record whose magic, format, CRCs or UID do not match
the chip it runs on.

## Appendix B — Product profile example

```yaml
profile: sc767-demo
version: 1.2.0
product: SoCcentric demo board
hw_revisions: [B, C]
min_factory_version: 0.1.0
released: { by: "Sandesh Ghimire", at: 2026-11-03 }

parts:
  dev_id: 0x451            # STM32F76x/F77x
  rev_id: [0x1001]         # rev Z
  flash_kb: 2048

images:
  test:  { file: hwval-0.2.0.elf,   sha256: "…" }
  boot:  { file: boot-0.1.0.hex,    sha256: "…" }
  app:   { file: demo-app-0.2.0.img, sha256: "…", slot: A }

test_plan:
  tests: [DEV.*, CORE.001, CLK.001, CLK.002, RAM.*, FLS.001, GPIO.004, LED.*, BTN.001, UART.001.USART6, ETH.002]
  allow_operator: true
  limits:
    CLK.001: { min: 15840, max: 16160, unit: kHz }

calibration:
  - field: 0x20
    from: ADC.001.vrefint_mv
    rule: "round(value)"
    range: [1180, 1240]

identity:
  serial: "SC767-{yyww}-{counter:06}-{luhn}"
  mac: { enabled: true, oui_block: "02:00:00:00:00:00/24", allow_local: true }   # development block

protection:
  nDBANK: 0
  BOR_LEV: 3
  wrp_sectors: { bank1: [0, 1], bank2: [1] }
  rdp: 0                   # 1 for production profiles; 2 is never allowed

label:
  template: label-50x25.zpl
  fields: [serial, mac, product, hw_revision, date, fw_version]
```

## Appendix C — Change requests to other projects

| CR | Project | Change |
|---|---|---|
| CR-FAC-01 | BOOT | Document that metadata records may be written by the factory over SWD (`HEADER`, `VERIFIED`, `ACTIVATED` with sequence 1, `CONFIRMED`), so a new unit boots slot A as confirmed without a trial. |
| CR-FAC-02 | BOOT | Narrow the bank 2 reserved area to sector 0 (16 KB, recovery copy of BOOT, which then must fit in 16 KB) and assign bank 2 sector 1 to the provisioning record (§7.1). Add the record to the flash wrapper's allow-list as read-only for BOOT. |
| CR-FAC-03 | HWVAL, DEMO (C/Rust/Zephyr) | Add a shared `provisioning` reader (Appendix A) and report serial number, hardware revision and MAC in `HelloReply`/`Status`; DEMO uses the provisioned MAC for Ethernet when present. |
| CR-FAC-04 | HWLINK | Add an optional `identity` table to `HelloReply` (serial, product ID, hardware revision, record revision); minor version bump ([LINK] LNK-013). |
| CR-FAC-05 | HWVAL | Allow a test plan to be passed as an explicit ID list in one `Run`, and report the image hash in `HelloReply` so Factory can check it runs the profile's test image. |
| CR-FAC-06 | Studio | Move the HWLINK, OpenOCD, image, theme and widget code into `soccentric-stm32-hostcore` (ARCH-001) and add a profile editor/release tool (PRF-003). |

## Appendix D — Database schema

```sql
-- PRAGMA user_version = 1; PRAGMA journal_mode = WAL;

CREATE TABLE units (
  uid            TEXT PRIMARY KEY,        -- chip 96-bit UID, hex
  pcb_serial     TEXT,
  serial_number  TEXT UNIQUE,
  mac            TEXT UNIQUE,
  product_id     TEXT,
  hw_revision    TEXT,
  first_seen     TEXT NOT NULL,
  last_result    TEXT
);

CREATE TABLE runs (
  id             INTEGER PRIMARY KEY,
  uid            TEXT REFERENCES units(uid),
  pcb_serial     TEXT,
  station_id     TEXT NOT NULL,
  fixture        TEXT NOT NULL,
  operator       TEXT NOT NULL,
  work_order     TEXT,
  profile_id     TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  kind           TEXT NOT NULL,           -- FIRST, RETEST, REWORK
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  result         TEXT,                    -- PASS, FAIL, ABORTED, INCOMPLETE, ERROR
  failed_step    TEXT,
  reason         TEXT,
  supersedes     INTEGER REFERENCES runs(id)
);

CREATE TABLE steps (
  run_id   INTEGER NOT NULL REFERENCES runs(id),
  seq      INTEGER NOT NULL,
  name     TEXT NOT NULL,
  result   TEXT NOT NULL,
  started_at TEXT NOT NULL,
  duration_ms INTEGER,
  detail   TEXT,
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE test_results (
  run_id   INTEGER NOT NULL REFERENCES runs(id),
  test_id  TEXT NOT NULL,
  result   TEXT NOT NULL,
  measured_value REAL, measured_unit TEXT,
  limit_min REAL, limit_max REAL,
  reason   TEXT,
  operator_answer TEXT
);

CREATE TABLE serial_pool (
  serial_number TEXT PRIMARY KEY,
  work_order    TEXT NOT NULL,
  state         TEXT NOT NULL,            -- FREE, RESERVED, CONSUMED
  uid           TEXT,
  run_id        INTEGER,
  changed_at    TEXT NOT NULL
);

CREATE TABLE records (                     -- provisioning records as written
  uid TEXT NOT NULL, revision INTEGER NOT NULL, run_id INTEGER NOT NULL,
  blob BLOB NOT NULL, written_at TEXT NOT NULL,
  PRIMARY KEY (uid, revision)
);

CREATE TABLE labels (
  run_id INTEGER NOT NULL, printed_at TEXT NOT NULL, reprint INTEGER NOT NULL DEFAULT 0,
  user TEXT NOT NULL, data TEXT NOT NULL
);

CREATE TABLE mes_queue (
  run_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, sent_at TEXT
);

CREATE TABLE audit (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT
);
```

## Appendix E — Open items

| ID | Item | Status |
|---|---|---|
| OPEN-F01 | Where the shared `soccentric-stm32-hostcore` package lives: its own repository (recommended), or inside the Studio repository first. | Open |
| OPEN-F02 | Confirm OpenOCD can set `nDBANK`, `BOR_LEV`, `nWRP` and RDP level 1 on the F767 in one sequence, and measure the time; fallback STM32CubeProgrammer CLI ([GUI] OPEN-G04). | Open |
| OPEN-F03 | Profile signing: key management, who signs, and whether stations must require signatures. | Open |
| OPEN-F04 | Secure provisioning (device keys, certificates): needs RDP level 1, a key source (HSM or MES) and a firmware-side key store; out of scope for v0.1. | Deferred |
| OPEN-F05 | MES interface: confirm the customer's API (REST/JSON, or file drop) and the run payload. | Open |
| OPEN-F06 | Fixture power control hardware (USB hub with per-port power, relay board, or programmable supply). | Open |
| OPEN-F07 | Final product name (working name "HWVAL Factory"). | Open |
