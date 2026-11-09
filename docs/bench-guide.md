# Bench guide

The bench pages take a blank NUCLEO-F767ZI from the box to a unit with its
system info, calibration and diagnostic map. They sit in the sidebar under
**Bench**, in the order you use them. The device contract (memory map, file
formats, UART commands) is
[factory-firmware/docs/FACTORY_SPEC.md](../../factory-firmware/docs/FACTORY_SPEC.md).

Every page works without hardware: tick **Use a simulated board** on the
Detect page (or **Bench > Simulated Board**, Ctrl+Shift+M).

## Before you start

- Build the bootloader (`bootloader/scripts/build.sh`) so it trusts your key.
- Build the factory firmware (`factory-firmware/scripts/build.sh`). The file
  you need is `build/factory.img`.
- Have the P-256 private key (`.pem`) the bootloader trusts. The published
  development key (id `0xFAA7171B`) is flagged on screen; use it on the bench
  only. The station never creates or stores keys.
- Install OpenOCD. Check out the `bootloader` repository next to this one, or
  set `HWVAL_BOOTLOADER` to its folder, so the station finds `bootctl.py`.

## The pages

1. **Detect** (Ctrl+4) finds the ST-LINK probe and its UART port. If more than
   one is attached, choose the right pair.
2. **Bootloader** (Ctrl+5) erases the flash, sets the dual-bank option bytes and
   writes the bootloader over SWD. **Verify BOOT key** reads the key id the
   bootloader trusts and compares it with your key.
3. **Factory firmware** (Ctrl+6) signs `factory.img` with your key, sends it
   through the bootloader into slot A, resets the board over SWD and waits for
   the firmware to answer.
4. **Device info** (Ctrl+7) resets the board and shows the firmware name,
   version, build and chip UID. The table shows what the `.sys`, `.cal` and
   `.diag` blocks hold.
5. **System info** (Ctrl+8) searches the CSV database by id or serial number,
   compacts the unit's row into a `.sys` file and writes it.
6. **Calibration** (Ctrl+9) loads a `.cal` file and writes it.
7. **Diagnostics** (Ctrl+0) loads a `.diag` file and writes it, or initializes a
   clean fault and diagnostic map.

Each write is checked on the PC first (header and both CRCs), checked again by
the firmware, then read back from the board and compared byte for byte. A page
reports success only after that comparison.

## The factory database

`factory.csv` has one row per unit:

```
id,serial,product,hw_version,fw_version,batch,mfg_date,mac,notes,status
```

`id` is unique. After a verified `.sys` write the station sets `status` to
`programmed`. **Create sample database** on the System info page makes a
starter file.

## Addresses

Each file goes to its own 4 KB block inside the identity sector:
`.sys` at `0x08105000`, `.cal` at `0x08106000`, `.diag` at `0x08107000`. The
address field accepts any 4 KB-aligned block from `0x08105000` to `0x08107000`.
The identity record at `0x08104000` is protected: the firmware refuses writes
there.

## Menus

File opens a database, `.cal` or `.diag` file. Edit routes copy and paste to the
focused field. Bench holds one entry per page, Refresh Detection (Ctrl+R) and
Simulated Board. View can hide the sidebar (Ctrl+Alt+B).
