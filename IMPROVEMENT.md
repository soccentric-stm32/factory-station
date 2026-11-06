# HWVAL Factory — rethink and implementation brief

Author: Sandesh Ghimire <sandesh@soccentric.com>
Date: 2026-11-06
Replaces: the 2026-11-04 improvement plan (see git history). Most of that
plan was implemented; this brief starts from what the station is today and
what the rest of the workspace has become since then (signed images,
anti-rollback, protocol minor 3, the debug UART, the `bootctl` fixes).

This document is written as instructions for an LLM coding agent working in
this workspace. A human engineer can follow it the same way.

---

## 0. How to use this document (read first)

You are rebuilding the end-of-line (EOL) station for STM32F767ZI boards.
Work through the phases in §7 in order. Each task has an ID, the files
involved, the steps and a **Done when** list. A task is not done until every
item in its Done-when list is true and its tests pass.

### 0.1 Ground rules

1. **The firmware is the specification.** Never invent a constant, a message,
   a record format or an address. Read it from the firmware repository named
   in §2 and cite the file in a comment. If the firmware and this document
   disagree, the firmware wins; stop and report the difference.
2. **One source of truth per contract.** Protocol schema, image format, flash
   layout and identity format come from the bootloader repository's published
   bundle (task P0-02), never from a hand-kept copy.
3. **The station never holds a private signing key.** Images arrive signed
   from the release pipeline. The station verifies signatures with public
   keys only.
4. **RDP level 2 is never applied**, by any code path, in any mode. Keep the
   existing refusals and their tests.
5. **Never erase or write the identity sector** (bank 2 sector 13) except in
   the identity step or in an audited supervisor rework.
6. **Simulator parity.** Every behaviour change in the hardware path must
   also land in the simulator, with a test, in the same change. No
   `if simulation:` inside protocol, image or flash logic; the simulator is
   an adapter behind the same interface.
7. **Hardware actions with permanent effects** (option bytes, RDP, mass
   erase, identity writes) run only through the station's own step code,
   never from an ad-hoc script, and only on a board the engineer has
   designated for the test.
8. **Match the repository's style:** Python 3.10+, type hints, ruff clean,
   the existing docstring/comment density, `hostcore`/`hwval_factory` naming.
   Commits are authored by Sandesh Ghimire only, with no co-author lines.
9. After every task: `make test` (pytest, offscreen Qt) and `make lint` must
   pass. Hardware tasks also need the HIL check in §8.3, run and recorded.

### 0.2 Commands you will need

```bash
cd factory-station
make dev                                   # venv with dev + test deps (pytest-qt included)
QT_QPA_PLATFORM=offscreen make test        # unit, contract, simulator and UI smoke tests
make lint                                  # ruff
hwval-factory --detect                     # ST-LINK probes and VCP ports
hwval-factory --cli --station examples/station.simulator.yaml --pcb-serial TEST1
python3 ../bootloader/tools/bootctl.py --port /dev/ttyACM0 --no-enter info   # does not disturb a running app
python3 ../bootloader/tools/mkimage.py --inspect IMG --key PUBKEY.pem          # check a signed image
```

---

## 1. What the station is for

On the production line, one operator action per board takes a blank
NUCLEO-F767ZI-class board and leaves it:

1. **identified** — chip ID, revision, UID, flash size, option bytes;
2. **tested** — the profile's EOL test plan run by the HWVAL firmware, with
   limits, operator prompts and calibration;
3. **programmed** — the bootloader (BOOT) and the factory firmware, signed
   with the product key, laid out so BOOT boots it;
4. **personalised** — serial number, MAC address, hardware revision and
   calibration in the identity record, bound to the chip UID;
5. **checked** — BOOT starts the factory firmware, which confirms itself and
   reports the same identity;
6. **locked** — BOR, write protection on BOOT and the identity sector, RDP 1;
7. **labelled and recorded** — label printed, every fact stored, MES updated.

Beside the full flow the station offers **single tools** for engineers and
supervisors: probe a fixture, identify a chip, erase, program BOOT, load an
image, read or rewrite the identity, run one test, boot-check, protect,
recover a stuck or read-protected board, reprint, export. Today these are
scattered flags; in the target design every single tool is one step of the
pipeline run on its own (§5.3).

---

## 2. Ground truth: the system the station serves (as of 2026-11-06)

Verify each item against the cited file before relying on it.

### 2.1 Flash layout (`bootloader/src/common/boot_layout.h`)

| Region | Address | Notes |
|---|---|---|
| BOOT | `0x08000000`, 32 KB, bank 1 sectors 0–1 | currently 32,296 of 32,768 bytes with debug output on |
| Metadata A | `0x08008000`, bank 1 sector 2 | header at +0x000, VERIFIED +0x100, ACTIVATED +0x120, CONFIRMED +0x140, REJECTED +0x160, then TRIAL records; append-only |
| Slot A | `0x0800C000`, 976 KB, bank 1 sectors 3–11 | |
| Slot B | `0x0810C000`, bank 2 | metadata B at `0x08108000` |
| Identity | `0x08104000`, bank 2 sector 13 (`0x08004000` when banks are swapped) | BOOT never erases it |

The product needs dual-bank flash: `nDBANK = 0`, `nDBOOT = 1`,
`BOOT_ADD0 = 0x0080`, software watchdogs (`IWDG_SW = WWDG_SW = 1`).
`bootloader/tools/provision.sh` is the reference sequence.

### 2.2 BOOT behaviour that the station must respect

* **Signed images only** (BSAFE-006, `bootloader/docs/IMAGE_SIGNING.md`).
  Header version 2: `security_counter` (offset 88), `key_id` (92),
  `image_sha256` (96), ECDSA P-256 `signature` (128), `header_crc32` (252).
  Version 1 images are refused before anything is erased (`AUTH_FAILED`).
  BOOT checks the signature on `UpdateBegin`, the SHA-256 on `UpdateEnd`,
  everything again on `Activate`, and both slot headers on every boot.
* **Key:** the build embeds a 64-byte raw public key (`boot_signing_pubkey`,
  from `BOOT_SIGNING_KEY_SOURCE`) and its id (`boot_signing_key_id`). The 64
  bytes appear verbatim in `boot.bin`/`boot.hex`; the default build trusts
  the published development key, id `0xfaa7171b`.
* **Anti-rollback floor:** the highest `security_counter` among authentic,
  CONFIRMED, non-REJECTED slots. Anything below it is refused and a lower
  slot becomes "superseded". **A CONFIRMED record written by the station
  sets the floor of that board for good.**
* **Listen window (ENT-001):** for 500 ms after reset BOOT waits for a
  Hello. A Hello in that window keeps BOOT in update mode (120 s timeout).
  Any host that resets the board and then sends Hello may park BOOT by
  accident (see R4).
* **Trial boot:** an image activated over HWLINK boots on trial; it must
  write CONFIRMED within the IWDG window (4 s, kicked) and the trial limit
  is 3 starts, then BOOT rejects it and rolls back. **An active slot on
  trial blocks `UpdateBegin` (unless `force`) and blocks `Activate`
  (no override).**
* **Factory HEX:** `mkimage.py --factory-hex OUT --boot boot.hex --slot-a
  a.img --slot-b b.img` builds one HEX with BOOT, both slots and both
  metadata sectors marked CONFIRMED (both slots required today).
* **Debug output:** BOOT and every application print text lines on USART6
  TX, PG14 = Arduino D1, 115200 8N1, format `[<ms>] <TAG> <E|W|I|D> <text>`
  (tags `BOOT`, `C-FRT`, `C-ZEP`, `R-EMB`, `R-ZEP`, `HWVAL`).

### 2.3 HWLINK protocol (`bootloader/protocol/hwlink.fbs`)

* Major 1, **minor 3**. COBS, 8-byte header, CRC-32, FlatBuffers `Message`.
* Minor 2 added `BootInfo.active_valid`, `target_slot`, `boot_version`,
  `boot_build`, and the `force` flag on `UpdateBegin`.
* Minor 3 added `Error.AUTH_FAILED = 14`, `Error.ROLLBACK = 15`,
  `ImageHeader.header_crc32` and `ImageHeader.extension` (header bytes
  88–251 verbatim), `BootSlotInfo.security_counter`,
  `BootInfo.signed_images`, `key_id`, `security_floor`.
* `bootloader/tools/package_hwlink.py` (`make package-hwlink`) publishes the
  canonical schema, generated Python, `mkimage.py`, `boot_layout.h` and a
  vendored FlatBuffers runtime.

### 2.4 Host tools in `bootloader/tools`

* `bootctl.py`: `info`, `update` (`--activate --wait`, `--force`), `activate`,
  `enter`, `wait`, `catch`, `reset`, `set-baud`, `--json` event stream.
  `wait` now releases BOOT when it catches the listen window and reports a
  rollback only on evidence; `catch --for N` resends Hello every 50 ms while
  the board is reset (SWD/NRST), to reach BOOT when the application does
  not answer HWLINK. Read these two functions before writing any
  reset-then-talk logic in the station.
* `mkimage.py`: wrap, `--sign`, `--inspect [--key]`, `--genkey`,
  `--export-pubkey`, `--factory-hex`.
* `flash_gui.py` (BOOT Flasher): developer GUI. The station does **not**
  reuse its UI, only the shared host library (§5.1).

### 2.5 Firmware the station programs

* **Applications** (`app-c-freertos`, `app-c-zephyr`, `app-rust-freertos`,
  `app-rust-zephyr`): run under BOOT from slot A or B, write CONFIRMED 5 s
  after start when healthy, answer `Hello` (role APPLICATION/DEMO),
  `GetStatus` (slot, trial, health, version, commit, UID), report identity
  keys in `HelloReply.detail`, honour `EnterBootloader` and `Reset`.
* **HWVAL** (`validation-firmware`): test firmware.
  * Boots into a **text CLI**; the host must send `mode link` to switch to
    HWLINK (LINK-010 keeps it across one reset). The CLI also auto-detects a
    HWLINK frame.
  * After boot it runs its **boot test suite** unless in link mode; while
    the suite runs it does not answer HWLINK.
  * It is being ported to run **under BOOT as a signed slot image**
    (uncommitted work in progress on 2026-11-06: it fails CORE.001/002/009/
    010 and CACHE.003 and restarts several times during the suite; trials
    were seen to fail). Treat "HWVAL under BOOT" as not ready (decision D-1).

### 2.6 Where the station is today (`factory-station`)

* ~12k lines of station code plus the HWLINK stack: fixture engine with an
  11-step state machine (`src/hwval_factory/station.py`, 2,206 lines),
  profiles with SHA-256 pins and optional Ed25519 signatures, SQLite
  traceability, serial/MAC allocation, identity record (`PRV1`), labels,
  MES queue, roles and login, PySide6 UI, CLI, simulator, packaging.
* 41 tests pass; the UI smoke test needs `pytest-qt` in the environment.
* **On silicon** (`docs/hardware-verification.md`): attach, IDs, option-byte
  writes both directions, WRP/RDP, RDP 1 → 0 recovery and the golden-board
  self-test pass. **A full end-to-end run has never completed**: it stopped
  at the test step because nothing answered on the VCP.

---

## 3. Review findings (why a rethink, not a patch)

Each finding has evidence you can open. Severity: **S1** a real board cannot
pass, **S2** a board can pass wrongly or intermittently, **S3** cost,
maintainability or scale.

| # | Sev | Finding | Evidence |
|---|---|---|---|
| R1 | S1 | The station accepts only header **version 1** images and rejects every signed (v2) image. Current BOOT boots only v2. A board programmed today would sit in BOOT with "image is not signed". | `src/hostcore/layout.py:55` (`IMAGE_HEADER_VERSION = 1`), `src/hostcore/images.py:192` |
| R2 | S1 | The station speaks **protocol minor 1**; BOOT is at minor 3. The BOOT-load path (`app_load: boot`) sends `ImageHeader` without `extension`, so BOOT zero-fills the signature bytes and answers `AUTH_FAILED`, an error code the station does not even know. | `diff src/hostcore/protocol/hwlink.fbs ../bootloader/protocol/hwlink.fbs` |
| R3 | S1 | The released profile `profiles/sc767-1.0.0` pins a **pre-signing BOOT** (`boot-1.0.0.hex`, 2026-11-04) and an unsigned app. Internally consistent, but not the product. | `profiles/sc767-1.0.0/manifest.yaml` |
| R4 | S2 | **Listen-window hazard.** After a reset the boot check sends Hello every 0.2–3 s from the first moment. One can land in BOOT's 500 ms window, park BOOT in update mode, and the unit fails "BOOT did not start an application" on a good board, intermittently. The same bug was found and fixed in `bootctl wait` on 2026-11-06. | `src/hwval_factory/station.py:1790` (`_wait_hello`), `:1518` (`_step_boot_check`) |
| R5 | S2 | The station writes CONFIRMED for slot A itself but **knows nothing about `security_counter` or the floor**: it never checks the counter of what it writes, cannot explain a ROLLBACK refusal, and a rework with an older image can fail or, worse, leave a "superseded" slot. | `_step_program` (`station.py:1241`), `to_metadata_records` in `images.py` |
| R6 | S2 | **No key check.** Nothing proves the BOOT build in a profile trusts the key the app was signed with, or that a production profile does not ship the published development key. | profile tool, `profile.py` |
| R7 | S2 | **Contract tests miss the drift.** They compare the schema against `validation-firmware`'s copy (not the bootloader's), and only the image magic, not the header version or signing fields; they pass while R1–R2 are true. | `tests/test_contracts.py` |
| R8 | S2 | **Test step vs HWVAL reality.** HWVAL starts in CLI mode and runs its boot suite, during which it ignores HWLINK; the suite can reset the MCU. The hardware run stopped exactly here. | `docs/hardware-verification.md`, §2.5 |
| R9 | S3 | **Two option-byte flips and two mass erases per unit** (bench `nDBANK = 1`, then product `nDBANK = 0`). Slow, and option-byte programming is the riskiest operation on the line (power loss leaves a board in an unknown configuration). | `_step_prepare` (`station.py:1012`), `_step_program` |
| R10 | S3 | **Hand-assembled flash image** over many SWD writes (BOOT, slot, erase metadata sector, each record, read-back each) instead of programming one verified factory HEX that the bootloader's own tool builds. | `_step_program`; `mkimage.py --factory-hex` |
| R11 | S3 | **Four copies of the host stack** (HWLINK, codec, images, OpenOCD) across `factory-station`, `validation-gui`, `bootloader/tools`, plus four differing `hwlink.fbs` files. Every fix has to be found and repeated. | `src/hostcore/`, `validation-gui/src/hwval_studio/protocol/`, `bootloader/tools/` |
| R12 | S3 | **Monolithic engine.** All steps are methods of one 2,206-line class with transport branches inside them (`if app_load == "boot"`). Steps cannot run alone, which is why single tools are flags on one CLI. | `station.py`, `cli.py:41-87` |
| R13 | S3 | **No debug evidence.** BOOT and every firmware now print a boot log on USART6, but the station neither wires nor records it; a failed boot check leaves no explanation. | §2.2 |
| R14 | S3 | Developer environment drift: `make test` in a fresh venv lacks `pytest-qt`; the system Python lacks `flatbuffers`. | `pyproject.toml` |

---

## 4. Design decisions to settle first

Record each decision in `docs/decisions.md` (create it) before writing the
code that depends on it. The recommendation is what this brief assumes.

| ID | Question | Options | Recommendation |
|---|---|---|---|
| D-1 | How does HWVAL run during EOL? | **(a) Bench mode:** `nDBANK = 1`, raw HWVAL ELF at `0x08000000`, then switch to product option bytes (today). **(b) Under BOOT:** product option bytes once; HWVAL as a signed slot-B image; the product in slot A. | **(a) now, (b) as the target**, switched per profile (`test_mode: bench | under_boot`) once HWVAL under BOOT passes its own suite on a board 20 times in a row. (b) removes one option-byte flip and one mass erase per unit (R9). |
| D-2 | What is in slot B of a shipped unit? | empty; a copy of the factory image; HWVAL superseded by the floor | **Empty** (erased, no metadata) for flow (a). For flow (b), HWVAL signed with counter **0** and every product image with counter **≥ 1**: once the factory image is CONFIRMED the floor is ≥ 1 and HWVAL can never boot again. Erase slot B anyway if line time allows; the profile decides (`slot_b_after_test: erase | leave_superseded`). |
| D-3 | Who writes CONFIRMED for the factory image? | the station (SWD metadata); the firmware itself on first boot | **The station**, via the factory HEX, so the floor is set deterministically and the first boot is not a trial. The boot check then proves the image really starts. |
| D-4 | Security-counter policy | free; per product line | Factory image counter = the product line's current counter, pinned in the profile (`signing.security_counter`). The profile tool refuses a lower counter than the previous released profile of the same product. HWVAL counter 0 (D-2). |
| D-5 | Signing at the station? | sign on the station; sign in release CI | **Release CI only.** The station verifies. Product private keys live in an HSM or a CI secret; never on a line PC. |
| D-6 | Debug-UART capture | none; optional; mandatory | **Optional per fixture** (needs a USB-UART on PG14 in the fixture). When present, the boot log is stored with the run and shown on failures. |

---

## 5. Target architecture

### 5.1 Shared host library, one source of truth (fixes R2, R7, R11)

```
bootloader  ──make package-hwlink──►  dist/hwlink-bundle/   (schema, generated
                                                              Python, mkimage.py,
                                                              boot_layout.h, runtime)
                                           │
factory-station/src/hostkit/  ◄──vendored, pinned by hash────┘
   protocol/   COBS framing, codec (minor 3), generated code from the bundle
   images.py   v1 + v2 headers, signature + SHA-256 verify (public key only)
   layout.py   generated from boot_layout.h, never hand-edited
   boot.py     update / activate / wait / catch with the listen-window rules
   swd.py      OpenOCD session per fixture (existing openocd.py, cleaned)
```

* Rename `src/hostcore` to `src/hostkit` only if the rename is cheap; the
  name matters less than the rule that **generated and vendored parts are
  never edited by hand** (`scripts/update_hostkit.py` regenerates them from
  the bundle and records its hash in `src/hostkit/BUNDLE`).
* `validation-gui` and `bootloader/tools/flash_gui.py` may adopt the same
  library later; out of scope here, but do not add station-only behaviour to
  modules that belong in the shared layer.

### 5.2 Station core: a step pipeline with ports

```
Recipe (from the profile)  ─►  Pipeline  ─►  Step 1 … Step n
                                  │             │ uses only ports, never a concrete driver
                                  ▼             ▼
                               RunContext   Ports: Swd, Link, Boot, Power, Fixture,
                               (facts,      Scanner, Printer, Mes, Store, DebugLog,
                                safe state)  Clock, Operator
                                              │
                         real adapters ◄──────┴──────► simulator adapters
```

* `Step` (new `src/hwval_factory/steps/`): one class per step with `name`,
  `requires` (facts it needs in `RunContext`), `provides`, `run(ctx) ->
  StepResult`, `safe_state_after`, `retry` policy, `timeout_s`, and
  `standalone: bool` (whether it can run as a single tool).
* `Pipeline`: runs a recipe, handles retries, abort, power loss, the safe
  state record and per-step timing. It replaces the control flow in
  `Station` without changing the database schema (add columns only through
  the existing migration mechanism).
* `RunContext`: typed facts (UID, option bytes before/after, images and
  hashes, key id, floor, serial, MAC, test results, calibration, boot log)
  instead of the free-form `ctx.metadata` dict. Persist it with the run.
* Ports are `typing.Protocol` classes. Real and simulated adapters implement
  them. Transport choices (`app_load: swd | boot`, `test_mode`) select a
  step or an adapter, never an `if` inside a step.

### 5.3 Single tools are steps run alone (fixes R12)

New CLI shape (keep `--cli` working as an alias for one release):

```
hwval-factory run      --fixture F1 [--pcb-serial …]          # full recipe
hwval-factory tool probe       --fixture F1                   # SWD + VCP + Hello, step by step
hwval-factory tool identify    --fixture F1                   # IDs, UID, option bytes, RDP, floor
hwval-factory tool erase       --fixture F1 --confirm         # mass erase (refused at RDP>0)
hwval-factory tool program-boot     --fixture F1 --profile P
hwval-factory tool program-factory  --fixture F1 --profile P  # factory HEX + verify
hwval-factory tool load-app    --fixture F1 IMG [--via boot|swd]
hwval-factory tool test        --fixture F1 [--tests 'ADC.*']
hwval-factory tool identity    --fixture F1 read|write|verify
hwval-factory tool boot-check  --fixture F1
hwval-factory tool protect     --fixture F1 --profile P
hwval-factory tool recover     --fixture F1 [--catch|--unlock-rdp --reason …]   # supervisor
hwval-factory tool capture-log --fixture F1 --seconds 10      # debug UART (D-6)
hwval-factory label reprint RUN_ID | export … | certificate RUN_ID
```

* Every tool goes through the same `Step` and ports as the full run, writes
  an audit record with the operator and the role check, and has `--json`
  output with the same exit codes as today (0 pass, 1 fail, 2 error).
* The engineer UI gets a **Tools** page that lists the standalone steps per
  fixture, with the same confirmations (typed confirmation for erase,
  protect and recover).

---

## 6. Target EOL flow

### 6.1 Flow A — bench-mode test (D-1 a), the first production flow

| # | Step | Port | Success | Safe state after | On failure |
|---|---|---|---|---|---|
| 1 | **scan** PCB barcode and work order | Scanner | unit bound to fixture | `scanned` | operator re-scans |
| 2 | **identify**: DEV_ID `0x451`, REV_ID in profile, UID, flash 2048 KB, OPTCR/OPTCR1, RDP | Swd | matches profile | `identified` | FAIL (wrong part) |
| 3 | **bench config**: `nDBANK = 1`, mass erase (refuse if RDP > 0 unless rework) | Swd | read back | `bench` | ERROR, recovery tool |
| 4 | **load HWVAL** ELF at `0x08000000`, verify, reset | Swd | verify OK | `test_loaded` | retry once |
| 5 | **link up**: open VCP, send `mode link` at the CLI, wait for HWLINK `HelloReply` role HWVAL; cross-check UID | Link | same UID | `linked` | ERROR with cause (VCP missing, no CLI prompt, …) |
| 6 | **test**: `ListTests`, `Run` the plan, prompts ⇄ answers, collect results and measurements | Link + Operator | all PASS | `tested` | FAIL with test id, value, limit |
| 7 | **calibrate** from named measurements | — | values in range | — | FAIL |
| 8 | **product config**: `nDBANK = 0`, `nDBOOT = 1`, `BOOT_ADD0`, software watchdogs; mass erase | Swd | read back | `product_blank` | ERROR, recovery tool |
| 9 | **program factory HEX** (BOOT + slot A + metadata; §6.3) and verify by hash | Swd | verify OK | `programmed` | retry once |
| 10 | **identity**: allocate serial/MAC, build `PRV1`, write sector 13, read back | Swd + Store | byte match | `personalised` | FAIL, serial/MAC released |
| 11 | **boot check** (§6.4) | Swd + Link (+ DebugLog) | app CONFIRMED in A, identity, version, key id, floor | `boot_checked` | FAIL with BOOT log |
| 12 | **protect**: BOR, WRP BOOT (sectors 0–1) + identity (bank 2 sector 13), RDP 1; power-cycle; read back | Swd + Power | as profiled | `protected` | ERROR, supervisor |
| 13 | **post-protect boot check** (short form of 11) | Link | app answers | — | ERROR |
| 14 | **label** | Printer | printed or queued | — | WARN, reprint tool |
| 15 | **record** and queue for MES | Store + Mes | committed | `done` | ERROR, retry from queue |

### 6.2 Flow B — test under BOOT (D-1 b), the target once HWVAL is ready

Steps 3–4 and 8 collapse into one product configuration at the start:

```
identify → product config + mass erase (once) → program test HEX:
   BOOT + slot A = factory image (counter ≥ 1, CONFIRMED)
        + slot B = HWVAL (counter 0, ACTIVATED with a higher sequence, not confirmed)
→ reset: BOOT boots HWVAL from B on trial → link up → test → calibrate
→ HWVAL stops kicking / station resets: trial fails or the station marks B REJECTED
   (decide in P3-03), BOOT falls back to A (CONFIRMED)
→ identity → boot check → (erase slot B per D-2) → protect → label → record
```

Prerequisites: P3-01 to P3-04. Do not start Flow B work before Flow A
passes on hardware.

### 6.3 Factory image (fixes R1, R3, R5, R6, R10)

* Built **offline by the profile tool**, never on the line:
  `hwval-profile build-factory --boot boot.hex --app app.img [--hwval hwval.img]`
  calls `bootloader/tools/mkimage.py --factory-hex` from the bundle and pins
  the result's SHA-256 in the manifest.
* Profile tool checks, all of which must pass or the build is refused:
  1. the app header is **version 2**, its signature verifies with the
     profile's public key (`signing.public_key`), and its `key_id` equals the
     id of that key;
  2. the 64-byte public key is **found verbatim inside the BOOT image**
     (proves BOOT trusts it); production profiles refuse the published
     development key (id `0xfaa7171b`) unless `allow_development_key: true`
     and the profile is marked non-production;
  3. the app's `security_counter` equals `signing.security_counter` and is
     not lower than the previous released profile of the same product (D-4);
  4. load address, size and entry point fit slot A (layout from the bundle).
* On the line the station verifies the HEX hash, programs it in one
  `flash write_image erase`, verifies, and records the factory image hash,
  key id and counter in the run.

### 6.4 Boot check without the listen-window hazard (fixes R4, R13)

1. Reset over SWD.
2. **Wait 2.0 s before the first Hello** (BOOT's 500 ms window plus its image
   checks; make it a named constant shared with `bootctl.BOOT_SETTLE_S`).
3. Hello until the application answers (role APPLICATION/DEMO/APP).
4. If BOOT answers instead: read `BootInfo`. If an active slot is valid, the
   Hello caught the window: send `Reset`, wait 2.0 s, retry, at most 3 times
   (the same rule as `bootctl.wait_for_confirmation`). If no slot is valid,
   FAIL with BOOT's per-slot detail (`image is not signed`, `bad signature`,
   `superseded`, …), which is exactly what an operator needs.
5. From the application: `GetStatus` → slot A, trial CONFIRMED, UID equals
   step 2, version equals the profile; identity in `HelloReply.detail`
   equals what step 10 wrote.
6. Read identity and metadata back over SWD (existing B-14 checks).
7. Read `BootInfo` once in BOOT when the run needs it (through `catch` with
   an SWD reset, not `EnterBootloader`, so the application is not left in
   BOOT): `signed_images = true`, `key_id` equals the profile key,
   `security_floor` equals the factory counter.
8. If a debug UART is configured (D-6), attach the captured BOOT and
   application log to the step result; on failure show its last 20 lines.

---

## 7. Task list

Priority: **P0** blocks a real board passing; **P1** needed on the line;
**P2** scale, audit, comfort. Do the phases in order; inside a phase, the
listed order.

### Phase P0 — Make the current flow correct against today's firmware

| ID | Task | Files | Done when |
|---|---|---|---|
| P0-01 | **Dev environment.** Add `pytest-qt` and `flatbuffers` to the dev/test extras; `make dev` creates a venv that runs every test offscreen. | `pyproject.toml`, `Makefile` | Fresh clone: `make dev && QT_QPA_PLATFORM=offscreen make test` passes with 0 errors. |
| P0-02 | **Vendor the bootloader bundle.** Add `scripts/update_hostkit.py` that runs `make -C ../bootloader package-hwlink` (or takes a bundle path), copies schema, generated Python, `mkimage.py`, `boot_layout.h`, runtime into `src/hostcore/vendor/` and writes `BUNDLE` with the bundle's hash. Replace the hand-kept `protocol/hwlink.fbs` and generated code with the vendored ones. | `scripts/`, `src/hostcore/protocol/` | Schema in the station is byte-identical to `bootloader/protocol/hwlink.fbs`; generated code comes only from the bundle. |
| P0-03 | **Protocol minor 3.** Codec: decode `AUTH_FAILED`, `ROLLBACK`, `BootInfo` minor 2/3 fields, `BootSlotInfo.security_counter`; encode `ImageHeader.header_crc32` and `extension` (bytes 88–251) and `UpdateBegin.force`. Announce minor 3 in Hello. Map every `Nak` error to a human message (copy `bootctl.error_name` wording). | `src/hostcore/protocol/codec.py`, `hwlink.py` | Round-trip tests for every new field; a v2 image uploaded to the simulator over BOOT keeps its signature bytes intact. |
| P0-04 | **Image header v2.** `images.py` parses v1 and v2; for v2 exposes `security_counter`, `key_id`, `image_sha256`, `signature`, and verifies the signature (P-256, public key PEM, `cryptography`) and the SHA-256 of the body. Production profiles refuse v1. Keep `.hex`/`.elf`/`.bin` support for BOOT and HWVAL. | `src/hostcore/images.py`, `layout.py` | Accepts `mkimage --sign` output; rejects: v1 in a production profile, wrong key, flipped signature byte, body changed after signing, header CRC error. Test vectors come from `bootloader/tests/host/test_vectors.c` and fresh `mkimage` runs. |
| P0-05 | **Metadata v2.** Metadata records written by the station (or taken from the factory HEX) must match what BOOT expects for a v2 header; the CONFIRMED record's effect on the floor is computed and stored as `ctx.expected_floor`. | `images.py` (`to_metadata_records`), `station.py` | Simulator BOOT (C-03 already exists) selects slot A from station-written metadata of a signed image; floor reported equals the image counter. |
| P0-06 | **Listen-window-safe boot check** exactly as §6.4 steps 1–4. Extract the logic into `hostcore/boot.py` (`wait_for_application(session, swd, timeout)`), reuse it everywhere the station resets then talks (boot check, post-protect check, BOOT load path). | `station.py:1518, :1790`, new `hostcore/boot.py` | Simulator test where BOOT answers the first Hello passes after one `Reset`; a board with no valid slot fails with BOOT's slot detail. Hardware: 20 consecutive boot checks on a good board, 0 false failures. |
| P0-07 | **BOOT load path on minor 3.** `app_load: boot` sends the full v2 header with `extension`; handles `AUTH_FAILED`/`ROLLBACK` with clear text; never sets `force` in production. | `station.py` program step, `hwlink.py` | Simulator: signed image loads; unsigned refused with "image is not signed"; lower counter refused with "below the floor N". |
| P0-08 | **Contract tests that bite.** Compare against the **bootloader**: schema identical (P0-02), `PROTOCOL_MINOR`, header version 2 and offsets 88/92/96/128/252 from `mkimage.py`, layout constants, metadata offsets, identity area; fail if `validation-firmware` or an app has a schema whose minor is lower than the station's expectation for the messages it uses. | `tests/test_contracts.py` | Reverting P0-03 or P0-04 makes the contract tests fail. |
| P0-09 | **Release a real signed profile** `profiles/sc767-1.1.0`: current `boot.hex` (debug UART on or off per decision; record which), signed factory app (dev key allowed, profile marked `production: false`), HWVAL ELF for bench mode, public key PEM, key id, counter. Keep `sc767-1.0.0` for history, marked retired. | `profiles/`, profile tool | `hwval-profile verify profiles/sc767-1.1.0` passes all checks of §6.3; the simulator runs it to PASS. |
| P0-10 | **HWVAL link-up that works on silicon.** Before `mode link`: drain the CLI, wait for the prompt or the boot suite to finish (bounded), then send `mode link` and wait for `HelloReply`; if the suite is running, use `catch`-style persistence (resend `mode link` every 500 ms up to the profile's `hello_timeout_s`). Log every VCP byte received during link-up into the step result. | `station.py` test step, `hwlink.py` | On the bench board the station reaches HWLINK with HWVAL from a cold boot 10 times in a row; the log shows the CLI banner and the switch. |
| P0-11 | **First full hardware PASS (Flow A).** Run `examples/station.real.example.yaml` + `sc767-1.1.0` on the bench board from blank. Record the run in `docs/hardware-verification.md` (date, ST-LINK serial, UID, every step's duration, option bytes before/after, factory image hash, key id, floor). | docs | One recorded end-to-end PASS on silicon, then a second run on the same (now protected) board through the rework path. |

### Phase P1 — Rethink: pipeline, factory HEX, single tools

| ID | Task | Files | Done when |
|---|---|---|---|
| P1-01 | **Ports.** Define `typing.Protocol` ports (§5.2) and adapt the existing OpenOCD, HWLINK, power, inputs, scanner, printer, MES and DB code behind them without behaviour change. | new `src/hwval_factory/ports/` | All existing tests pass unchanged; no step imports a concrete adapter. |
| P1-02 | **RunContext and Step.** Typed `RunContext`; `Step` base class; move each `_step_*` method into its own module under `steps/`, one at a time, each move with its tests green. Split transport branches into separate steps or adapters (`ProgramFactoryHex`, `LoadAppViaBoot`). | `station.py` → `steps/*.py`, `pipeline.py` | `station.py` keeps only fixture/worker management and is under 600 lines; every step file has a unit test with simulated ports. |
| P1-03 | **Recipes.** The profile lists the steps (`recipe: [scan, identify, bench_config, …]`) with per-step options; the profile tool validates the recipe against step `requires/provides`. The default recipe equals Flow A. | `profile.py`, `pipeline.py` | A recipe that uses a fact before a step provides it is refused at profile load. |
| P1-04 | **Factory HEX** (§6.3): `hwval-profile build-factory`, manifest `images.factory`, step `program_factory` (one write + verify), metadata/floor facts recorded. Keep the per-record path as a fallback step for development profiles only. | `profile_tool.py`, `steps/program.py` | Program step time measured before/after and recorded; hardware PASS with the HEX path. |
| P1-05 | **Signing checks in the profile tool** (§6.3 items 1–4) and `hwval-profile verify` printing key id, counter, BOOT key match and development-key status. | `profile_tool.py`, `profile.py` | Each refusal has a test (wrong key, dev key in production, lower counter, v1 app, BOOT without the key). |
| P1-06 | **Single tools** (§5.3): `hwval-factory tool …` subcommands built on the steps, role checks, audit, `--json`, exit codes; keep the old flags as deprecated aliases for one release. | `cli.py` | Every tool in §5.3 has a CLI test against the simulator; `--help` lists them grouped by role. |
| P1-07 | **Engineer Tools page** in the UI: per fixture, run any standalone step, see its log and result; typed confirmation for erase/protect/recover. | `ui/` | pytest-qt test drives probe, identify and boot-check on a simulated fixture. |
| P1-08 | **Recovery tool**: catch BOOT (SWD reset + 50 ms Hello, like `bootctl catch`), unstick a trial (reset and wait for the trial to end, staying off the port), RDP 1 → 0 with mass erase (supervisor, reason code, audit), connect-under-reset. | `steps/recover.py` | Each recovery path tested in the simulator; the catch path verified on hardware. |
| P1-09 | **Failure text** for every step: cause, measured vs expected, suggested action, and (when D-6 is enabled) the last debug-log lines. | steps, `ui/tiles.py` | No step can end with a generic "station error" (test asserts every `StepFailure` has a cause and an action). |
| P1-10 | **Anti-rollback awareness**: identify reads the floor when BOOT is reachable; rework refuses a profile whose counter is below the unit's floor, with the reason; record floor before/after. | `steps/identify.py`, rework flow | Simulator test: reworking a floor-2 unit with a counter-1 profile is refused before erasing anything. |

### Phase P2 — Line readiness

| ID | Task | Done when |
|---|---|---|
| P2-01 | **Debug-UART capture (D-6).** Station config per fixture: `debug_uart: /dev/serial/by-id/…`. A `DebugLog` port reads it in a thread for the whole run, timestamps lines, splits them by tag, stores them with the run, shows them in the fixture detail and in failure text. | A forced boot-check failure in the simulator shows the BOOT lines that explain it; on hardware the BOOT and app start lines are stored with the run. |
| P2-02 | **Probe and fixture management**: bind ST-LINK serial ↔ VCP ↔ debug UART ↔ power port ↔ inputs per fixture, by USB serial on Linux and Windows; warn on old ST-LINK firmware; one OpenOCD process per fixture with fixed ports and restart on crash. | Eight simulated fixtures and two real ones run in parallel without port clashes. |
| P2-03 | **Power and safety**: power-cycle after option bytes and after protection through the Power port; brown-out during an option-byte write recorded as `UNKNOWN_CONFIG` and routed to the recovery tool. | Fault-injection test for each power-loss point. |
| P2-04 | **Cycle-time budget**: record per-step durations; dashboard shows median and p95 per step per fixture; profile may set a budget per step (warn above it). | Dashboard numbers match the database. |
| P2-05 | **Golden board and maintenance** (existing F-05/F-06) moved onto the pipeline: the self-test is a recipe. | Production blocked when the self-test is due or failed. |
| P2-06 | **Docs**: operator, supervisor and engineering guides rewritten for Flow A, single tools and recovery; README states what is verified on hardware. | Each guide's procedures were executed once and the date recorded. |

### Phase P3 — Flow B (test under BOOT)

| ID | Task | Done when |
|---|---|---|
| P3-01 | **HWVAL readiness gate** (in `validation-firmware`, by its owner): HWVAL as a signed slot image passes its boot suite under BOOT 20 times in a row, confirms within the trial window, answers HWLINK in link mode during the trial. | Recorded on hardware. |
| P3-02 | **`mkimage --factory-hex` with an empty slot or a non-confirmed slot** (bootloader repo): allow `--slot-b` to be omitted and allow "activated, not confirmed" for the test slot with a higher sequence. Add host tests. | Bootloader `make test` passes with the new cases. |
| P3-03 | **Ending the test image**: choose between (i) the station writes REJECTED for slot B over SWD, (ii) HWVAL reports done and stops kicking so the trial fails, (iii) erase slot B and metadata B. Prototype each on the simulator, measure on hardware, record the decision. | Decision recorded; chosen path tested. |
| P3-04 | **Flow B recipe** and step changes; per-profile switch `test_mode: under_boot`. | Hardware PASS with Flow B; per-unit time and option-byte writes reduced and recorded. |

### Phase P4 — Scale and audit (from the previous plan, still open)

* Central database for several stations sharing one serial/MAC pool
  (PostgreSQL), with the same allocation guarantees.
* Nightly hardware-in-the-loop job on a Jenkins node with a NUCLEO-F767ZI:
  Flow A, rework, protection, recovery.
* Per-unit secrets (device keys) only after a separate threat model; never
  in the DB, logs or exports.
* Localisation and kiosk mode; stack-light output.

---

## 8. Verification

### 8.1 Tests that must exist when you finish

* Unit: codec minor 3 (every new field), image v1/v2 parse and signature
  verification, metadata v2, floor computation, identity record, recipe
  validation, every step with simulated ports.
* Contract: §P0-08 against the bootloader and, when present, each firmware.
* Simulator end-to-end: Flow A PASS; one test per failure path (wrong part,
  test FAIL, signature refused, rollback refused, BOOT parked on the first
  Hello, power loss in option bytes, identity mismatch, printer offline,
  abort at every step); rework; recovery tools.
* UI: pytest-qt for the operator flow, the Tools page and prompts.

### 8.2 Simulator requirements added by this brief

The simulated BOOT must implement: v2 signature and SHA-256 checks with the
profile's public key, the floor, `AUTH_FAILED`/`ROLLBACK`, the 500 ms listen
window (a Hello within 500 ms of reset parks it), trial counting with the
3-start limit, and `BootInfo` minor 3 fields. The simulated HWVAL must start
in CLI mode, run a boot suite during which it ignores HWLINK, and accept
`mode link`.

### 8.3 Hardware check (bench board)

Board in the workspace: NUCLEO-F767ZI, ST-LINK `0671FF525171494867105524`,
UID `280029000D51313035363730`, VCP `/dev/ttyACM0`. For each hardware task,
record in `docs/hardware-verification.md`: the command, the date, the
result, per-step durations and any deviation. After the run, the board must
be left recoverable (document its final state).

---

## 9. Definition of done for the rethink

* A blank board on fixture F1 reaches PASS with one scan, using a **signed**
  factory image and a profile whose BOOT is proven to trust its key.
* After the run: BOOT boots slot A; `bootctl info` (through `catch`) shows
  `signed_images`, the profile's key id and the expected floor; the
  application reports the provisioned serial and MAC; option bytes read back
  as profiled with WRP on BOOT and identity, RDP 1.
* 20 consecutive boot checks on a good board give no false failure (R4).
* Every single tool in §5.3 works from the CLI and the Tools page with role
  checks and audit records.
* The same host code drives the simulator and the hardware; the contract
  tests fail if the bootloader's schema, image format or layout changes.
* `station.py` is reduced to fixture management; each step is its own
  tested module.
* Label, database, certificate and MES payload agree with the board.
