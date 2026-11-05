# factory-station

HWVAL Factory is the end-of-line test and provisioning station for STM32F767ZI boards, a cross-platform PySide6 desktop application for the production line. With one operator action per board it identifies the chip over SWD, runs the end-of-line test plan with the HWVAL firmware, programs the bootloader and application, writes a per-unit identity record, checks the product boots correctly, applies flash protection, prints the label and records everything for traceability. Up to eight fixtures run in parallel per PC, and a full simulator lets you try every step without hardware.

## Features

- One-action end-of-line flow from barcode scan to printed label
- Up to eight fixtures in parallel, each with its own worker and state machine
- Chip identification over SWD (device ID, revision, UID, option bytes)
- End-of-line test plan run through HWVAL over HWLINK with operator prompts
- Optional calibration step
- Programs bootloader, application and metadata over SWD or through the bootloader
- Per-unit provisioning record with serial number, MAC, revision and calibration
- Atomic serial and MAC allocation with Luhn check digits and pool warnings
- Boot check of identity and confirmed slot
- BOR, write and readout protection with read-back; RDP level 2 always rejected
- Versioned, SHA-256 pinned and Ed25519-signed production profiles
- ZPL label printing over TCP with preview and PDF/PNG fallback
- SQLite traceability with search, unit history, CSV/JSON export and PDF certificates
- Optional queued HTTPS uplink to an MES
- Badge or PIN login with operator, supervisor, engineer and administrator roles
- Yield dashboard with Pareto chart and cycle times
- Golden-board self-test that blocks production when due
- Simulator of the STM32F767ZI flash, protection and HWLINK device stack
- Headless CLI with pass/fail/error exit codes
- Packaging for PyInstaller, .deb, AppImage and Windows MSI

## Build and run

```bash
python -m pip install -e .
hwval-factory-ui --simulate --fixtures 4   # full flow with four simulated fixtures
make test                                  # run the test suite
```

## Related projects

- [bootloader](https://github.com/soccentric-stm32/bootloader) — A/B bootloader with UART update, CRC32 checks, trial boot and rollback
- [app-c-freertos](https://github.com/soccentric-stm32/app-c-freertos) — Demo application in C on FreeRTOS
- [app-c-zephyr](https://github.com/soccentric-stm32/app-c-zephyr) — Demo application in C on Zephyr RTOS
- [app-rust-freertos](https://github.com/soccentric-stm32/app-rust-freertos) — Demo application in Rust on Embassy
- [app-rust-zephyr](https://github.com/soccentric-stm32/app-rust-zephyr) — Demo application in Rust on Zephyr RTOS
- [validation-firmware](https://github.com/soccentric-stm32/validation-firmware) — HWVAL hardware validation firmware
- [validation-gui](https://github.com/soccentric-stm32/validation-gui) — HWVAL Studio: desktop GUI for tests, updates and flashing
- [soccentric-stm32.github.io](https://github.com/soccentric-stm32/soccentric-stm32.github.io) — Project website

## License

Released under the [MIT License](LICENSE) © 2026 Sandesh Ghimire. You are free
to use, modify and ship it in your own products.

Contributions are welcome. Fork the repository, make your change on a branch and
open a pull request. Bug reports, ideas and improvements are all appreciated.
