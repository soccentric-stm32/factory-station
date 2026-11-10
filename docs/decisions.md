# Design decisions

Recorded for the 2026-11-06 rethink (`IMPROVEMENT.md` §4). Each decision is
binding for the code that depends on it; change it here before changing the
implementation.

| ID | Decision | Status | Where it lives |
|---|---|---|---|
| D-1 | **HWVAL runs in bench mode for Flow A** (`nDBANK = 1`, raw HWVAL ELF at `0x08000000`), then the part is configured for the product. Flow B (HWVAL as a signed slot-B image under BOOT) is the target once it passes its own suite 20 times on hardware. The profile selects the mode with `test_mode: bench | under_boot` (default `bench`). | Adopted. Flow A is the production flow; Flow B is implemented and simulator-tested (`profiles/sc767-1.2.0-flowb`) but stays unreleased until validation-firmware passes on hardware (P3-01). | `profiles/*/manifest.yaml`, `hwval_factory/profile.py`, `steps/prepare.py`, `steps/flowb.py` |
| D-2 | **Slot B is empty on a shipped Flow-A unit.** Flow B signs HWVAL with counter 0 and every product image with counter ≥ 1, so the CONFIRMED factory image sets a floor that HWVAL can never pass; the profile decides whether slot B is erased (`slot_b_after_test: erase`) or left superseded. | Adopted. `slot_b_after_test` is validated and defaults to `erase`; `steps/flowb.py:_step_end_test` implements it. | `profile.py`, `steps/flowb.py` |
| D-3 | **The station writes CONFIRMED for the factory image** (through the metadata sector / factory HEX), so the anti-rollback floor is deterministic and the first boot is not a trial. The boot check then proves the image starts. | Adopted. `to_metadata_records` writes VERIFIED/ACTIVATED/CONFIRMED; `ctx.expected_floor` records the effect. | `hostcore/images.py`, `station.py:_step_program` |
| D-4 | **Security counter is per product line, pinned in the profile** (`signing.security_counter`). The profile tool refuses a lower counter than the previous released profile of the same product. | Adopted. `hwval-profile verify` compares against sibling profiles of the same `profile:` id. | `profile.py`, `profile_tool.py` |
| D-5 | **Signing happens in release CI only.** The station verifies with public keys and never holds a private key. | Adopted. `images.py` has public-key verification only; the profile bundles a public-key PEM. | `hostcore/images.py`, `profile.py` |
| D-6 | **Debug-UART capture is optional per fixture** (a USB-UART on PG14). When present the boot log is stored with the run and shown on failures. | Deferred to P2-01. The simulator and station do not yet open a debug UART. | future `ports/debuglog.py` |

## Consequences recorded with the decisions

- D-1 bench mode still costs one option-byte flip and one mass erase per unit
  (R9); Flow B removes them.
- D-2 for Flow A means the factory image is in slot A and slot B has no
  metadata; the anti-rollback floor therefore comes from slot A alone.
- D-3 makes the CONFIRMED record an input, not an outcome, of the boot check.
- D-4's previous-counter check needs the released profiles in the same
  directory; profiles in the station store are read-only and kept forever
  (`sc767-1.0.0` remains, marked `retired`).
- D-5 means a production profile must ship `signing.public_key`, and the
  profile loader refuses a production profile whose BOOT image does not
  contain that 64-byte public key.
- D-6 is the only decision without code today; the failure text already has
  a place to attach the last log lines.

## Contract composition note (P0-02)

The bootloader's published bundle (`make package-hwlink`) is the canonical
**BOOT** contract, but it deliberately does not carry HWVAL's test tables.
`scripts/update_hostkit.py` vendors the bundle unchanged and composes the
station schema from:

1. the bootloader schema up to its append-only `union Body`, and
2. the HWVAL section of `validation-firmware/protocol/hwlink.fbs` (its tables
   plus the union members appended after BOOT's).

`tests/test_contracts.py` enforces both halves: the BOOT prefix must be
byte-identical to the bootloader, and the HWVAL tables must be byte-identical
to validation-firmware. No hand-kept copy of either is allowed.

## Factory HEX timing

`hwval-profile build-factory` is implemented and calls the bootloader's
`mkimage.py --factory-hex`. It is not used to pin an image in
`profiles/sc767-1.1.0` yet: the current bootloader tool requires a slot-B
image and confirms both slots, which contradicts D-2 (empty slot B). The
station therefore keeps the per-record program path until bootloader task
P3-02 allows an empty or non-confirmed slot B.

## P3-03: ending the Flow B test image

**Decision: erase slot B and metadata B after the test** (`slot_b_after_test:
erase`, the default). The station resets to BOOT, erases both B regions over
SWD and resets again, so BOOT boots the CONFIRMED factory image in slot A.
This matches D-2 (empty slot B on a shipped unit) and removes any chance of
the HWVAL trial image being selected later. The profile may choose
`leave_superseded`, in which case the station leaves the trial record alone;
the factory image's higher counter already supersedes slot B.

The other two options were prototyped in the simulator:

- writing REJECTED for slot B over SWD is deterministic but leaves the image
  in flash and needs a record-aware erase later;
- stopping the IWDG kick makes the trial fail by itself, but it depends on
  HWVAL behaviour and leaves the board in an unknown state for a few seconds.

Flow B is simulator-only today. `validation-firmware` still fails its own
boot suite under BOOT (P3-01), so `profiles/sc767-1.2.0-flowb` is a
prototype and is not released for the line. Flow A remains the production
flow until P3-01 is recorded on hardware.

## P4 (still open)

The scale items are explicitly **not** part of the 2026-11-06 software
close-out:

- **Central database**: the **serial/MAC allocation pool** now supports a
  shared PostgreSQL (`central_pool.url`, `hwval_factory/central.py`,
  `CentralPool`), using `FOR UPDATE SKIP LOCKED` so two stations cannot hand
  out the same number. The run/traceability database remains SQLite per
  station; moving the whole store to PostgreSQL still needs a connection/DDL
  abstraction and a migration plan.
- **Nightly HIL**: `Jenkinsfile` now has an optional `HIL` stage on the
  `hil-nucleo-f767zi` label; scheduling it nightly and enforcing it as a gate
  remains a Jenkins configuration task.
- **Per-unit secrets**: not implemented. A device-key design needs its own
  threat model and a decision about HSM/CI custody (D-5 still stands).
- **Localisation and stack-light output**: kiosk mode exists; translations
  and a reduced stack display are deferred.
