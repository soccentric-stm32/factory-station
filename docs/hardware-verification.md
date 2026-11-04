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
* Run the full `profiles/sc767-1.0.0` test plan with the harness fitted, then
  repeat the protection step and the post-protection boot check.
