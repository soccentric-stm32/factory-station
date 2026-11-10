# Hardware verification record

Board: NUCLEO-F767ZI, ST-LINK serial `0671FF525171494867105524`,
DEV_ID `0x451`, REV_ID `0x1001`, UID `280029000D51313035363730`.

## Confirmed on silicon

| Check | Result |
|---|---|
| OpenOCD attach, IDCODE, UID and flash size | PASS |
| OpenOCD 0.12 `mdw`/`mdh` output formats (bare and `0x` words) | fixed and re-tested |
| `stm32f2x options_write` accepts compact `user_options` | PASS |
| nDBANK 1 -> 0, nDBOOT 1, BOOT_ADD0/1 via the host wrapper | PASS, read back raw `OPTCR=0x1FFC00E5`, `OPTCR1=0x00400080` |
| Full raw WRP/RDP application through the register sequence | PASS, RDP 1 / WRP 0xFFC read back |
| `flash write_image erase` on the HWVAL ELF after a bank change | PASS, image verified |
| RDP 1 -> 0 recovery with `stm32f2x unlock 0` and connect-under-reset | PASS, mass-erased, WRP cleared |
| Golden-board self-test (`--self-test`) | PASS |

## Open items on the bench

* The ST-LINK VCP on this board did not return HWVAL CLI or HWLINK output
  during the first end-to-end attempt, so the full HIL run stopped at the test
  step. USART3 is enabled in the firmware; check the NUCLEO solder bridges,
  the VCP cable and the board's USART3 pins before treating this as a code
  failure.
* Run the full `profiles/sc767-1.1.0` test plan with the harness fitted, then
  repeat the protection step and the post-protection boot check.

## 2026-11-06 rethink (software verified, hardware run still open)

The following changes are covered by unit, contract and simulator tests but
have **not** been repeated on silicon yet (P0-11):

| Change | Evidence |
|---|---|
| Image header version 2 parsing and ECDSA P-256 verification | `tests/test_images.py`, `tests/test_contracts.py` |
| Protocol minor 3 (`AUTH_FAILED`, `ROLLBACK`, `extension`, `security_counter`, floor) | `tests/test_protocol_minor3.py`, `tests/test_contracts.py` |
| Listen-window-safe boot check (reset, 2.0 s settle, release and retry) | `tests/test_boot.py` |
| Signed profile `profiles/sc767-1.1.0` (dev key id `0xfaa7171b`, counter 1) | `tests/test_profile_signing.py`, `tests/test_station_sim.py::test_signed_profile_simulator_passes` |
| BOOT-over-VCP load of a signed image with `extension` | `tests/test_station_sim.py::test_boot_vcp_app_load_path` |
| HWVAL link-up persistence (`mode link` every 500 ms, trace stored) | `hwlink.py:HwLinkSession.connect` |

To close P0-11, run from a blank board:

```bash
make dev
QT_QPA_PLATFORM=offscreen make test
hwval-factory --detect
hwval-factory --cli --fixture F1 --profile profiles/sc767-1.1.0 \
    --pcb-serial PCB-HW-0001 --no-simulate --json
```

Record the date, ST-LINK serial, UID, per-step durations, option bytes
before/after, factory image hash, key id and floor, then repeat the run on the
now-protected board through the rework path.  The board's final state must be
recoverable and documented here.  Until this is recorded, treat the station as
simulator-verified only.

## 2026-11-06 P1/P2/P3 software close-out

Additional behaviour covered by tests but still awaiting a bench run:

| Area | Evidence |
|---|---|
| Step modules + pipeline + recipe validation | `tests/test_pipeline.py`, `tests/test_station_sim.py` |
| Standalone tools and role checks | `tests/test_tools.py`, `tests/test_ui_smoke.py` |
| Recovery: catch, unstick, RDP unlock | `tests/test_tools.py` |
| Debug-UART capture | `tests/test_debuglog.py` |
| Power-cycle/UNKNOWN_CONFIG on option-byte failure | `tests/test_station_sim.py::test_power_loss_during_option_bytes_is_unknown_config` |
| Cycle-time medians/p95 and per-step budgets | `Database.step_stats`, dashboard |
| Eight simulated fixtures in parallel | `test_eight_simulated_fixtures_run_in_parallel` |
| Flow B under BOOT (slot A factory + slot B HWVAL trial) | `test_flow_b_runs_under_boot_in_the_simulator` |
| Factory HEX with empty slot B / unconfirmed slot B | bootloader `test_bootctl.py` |

Flow B remains a simulator prototype: the validation-firmware readiness gate
(P3-01) has not been recorded on hardware, so `profiles/sc767-1.2.0-flowb` is
not released for the line.

Still open: P0-11 (full hardware PASS), P3-01 (firmware owner), and the P4
scale items (PostgreSQL central store, nightly HIL execution beyond the
optional Jenkins stage, per-unit secrets, localisation/stack-light output).

## One-command acceptance run

When the bench board is attached, run:

```bash
make dev
scripts/hardware_run.sh F1 profiles/sc767-1.1.0 PCB-HW-0001
```

It writes `station-data/hardware-<timestamp>/` with the detect output, profile
verification, full JSON run, post-protection boot check and the rework run.
Record that directory in this file with the ST-LINK serial, UID, per-step
durations, option bytes, factory image hash, key id and floor.

## Host availability note (2026-11-06)

The close-out host has no `/dev/ttyACM*`/`/dev/ttyUSB*` and `lsusb` shows no
ST-LINK, so the P0-11 and P3-01 hardware runs could not be executed here.
`scripts/hardware_run.sh` is ready for the bench and writes the evidence
directory described above. Do not mark the station hardware-verified until
that directory is recorded in this file.
