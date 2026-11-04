# HWVAL Factory — recovery guide

Every procedure below starts by running the bring-up check:

```bash
hwval-factory --detect
hwval-factory --station station.local.yaml --probe --fixture F1
```

`--probe` reports SWD, IDCODE, flash size, UID, option bytes, VCP open,
`Hello` and the role-specific request (`GetDeviceInfo`, `GetStatus` or
`GetBootInfo`) one step at a time.

## The board is stuck after the bench configuration

The bench step sets `nDBANK = 1` (single bank) and erases the chip. If the run
was interrupted before the HWVAL test image was verified, the board is simply
blank. Start a **Rework** run with supervisor approval; the station repeats the
bench configuration and programs the test image again.

## The board reports RDP 1

RDP 1 blocks SWD reads and cannot be lowered without a mass erase. Use a
supervisor-approved **Rework** run: the station writes RDP 0, mass-erases, and
starts the normal flow. This destroys all flash content, including the identity
record. The database keeps the old record for audit.

`hwval-factory` and the profile parser refuse RDP 2; a level-2 board has no
debug recovery and must be replaced.

## Wrong option bytes

Read them first:

```bash
hwval-factory --station station.local.yaml --probe --fixture F1
```

A normal run repairs `nDBANK`, `nDBOOT`, `BOOT_ADD0` and `BOOT_ADD1` before
programming. If OpenOCD cannot write them (OPEN-B05), use STM32CubeProgrammer
to set `nDBANK = 0`, `nDBOOT = 1`, `BOOT_ADD0 = 0x0080`, keep
`BOOT_ADD1 = 0x0040`, then rerun with `tool/provision.sh --no-options` or the
factory flow.

## BOOT runs but no application starts

Check the boot metadata readback:

```bash
hwval-factory --station station.local.yaml --probe --fixture F1
```

A board with a valid BOOT but no valid slot A reports role `BOOTLOADER`. Start
a **Rework** run so the station programs the IBWH image into slot A and writes
the Appendix B `VERIFIED`, `ACTIVATED` and `CONFIRMED` records.

## Connect-under-reset

If a bad application disables the debug pins or resets continuously, hold the
board's reset button, start the run, and release reset when OpenOCD attaches.
For a NUCLEO-F767ZI, remove the CN2 jumpers to isolate the on-board ST-LINK, or
use the external ST-LINK connector.

## USB dropped during a run

The run ends as `ABORTED`/`ERROR` and the database records the last safe state
(`erased`, `test_programmed`, `app_programmed`, `provisioned`, `protected`).
Reconnect the probe and start a **Retest** or **Rework** run; the next run
detects the safe state and recovers the unit.
