# soccentric-stm32-factory-provisioning

**HWVAL Factory**: the end-of-line test and provisioning station for
STM32F767ZI boards. A cross-platform PySide6 desktop app (Windows and Linux
on the line), the production counterpart of the HWVAL Studio validation GUI.

In one operator action per board it identifies the chip over SWD, runs the
EOL test plan with the HWVAL firmware, programs the bootloader and the
application into the A/B flash layout, writes a per-unit provisioning record
(serial number, MAC, hardware revision, calibration), checks that the product
boots with the right identity, applies flash protection, prints the label and
records everything for traceability. Up to 8 fixtures run in parallel per PC.

| | |
|---|---|
| Requirements | [SRS.md](SRS.md) — SRS-FACTORY-F767 (v0.1 draft) |
| Status | Specified; implementation not started |
| Stack | Python 3.11+, PySide6, pyserial, FlatBuffers (HWLINK), OpenOCD, SQLite, ZPL label printers |
| Shares code with | [HWVAL Studio](https://github.com/soccentric-stm32/soccentric-stm32-validation-gui) through the planned `soccentric-stm32-hostcore` package |

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
