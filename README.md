# factory-station

HWVAL Factory is a cross-platform PySide6 bench tool for STM32F767ZI boards. It takes a blank board from the box to a unit with its bootloader, factory firmware, system info, calibration and diagnostic map, one page at a time. Every page also works against a simulated board, so you can try the whole flow without hardware.

[![HWVAL Factory: the validation page on a simulated board](https://raw.githubusercontent.com/soccentric-stm32/soccentric-stm32.github.io/main/assets/shots/factory-station.webp)](https://soccentric-stm32.github.io/#eol)

## Features

- Detect page: lists ST-LINK probes and UART ports, with a simulated board option
- Bootloader page: flashes the bootloader over SWD after setting the dual-bank option bytes
- Factory firmware page: flashes the signed factory firmware into slot A through the bootloader, with your signing key
- Device info page: reads the device and shows the state of the `.sys`, `.cal` and `.diag` blocks
- System info page: searches a CSV factory database by id and writes the compacted `.sys` file over UART
- Calibration page: writes a `.cal` file over UART, read back and compared
- Diagnostics page: writes a `.diag` file over UART, read back and compared, with an address check that protects the identity record
- Validation page: read-only check of the factory data blocks and a firmware self-test of the chip ID, flash size, clock, RAM, CRC unit, flash read and option bytes, with a saved text report
- Simulated board for every page, no hardware needed
- Sidebar, full menu bar and SVG icons in the shared SoCcentric look (one design shared with the other SoCcentric desktop apps: Inter (system sans fallback), a 22/15/13/12/11 px type scale, accent `#007AFF` (`#0A84FF` in dark), 10 px cards with hairline borders, 32 px buttons, pill status labels and a solid-accent current item in the sidebar. Light and dark follow the system colour scheme. `src/hostcore/soc_theme.py` is the shared theme file.)

## Build and run

```bash
python -m pip install -e .
hwval-factory --simulate                   # open the bench with a simulated board
hwval-factory --data-dir station-data      # folder for the factory database CSV
make dev && make test                      # install dev/test extras and run the suite
make lint                                  # ruff
make hostkit                               # re-vendor the HWLINK contract bundle
```

See [docs/bench-guide.md](docs/bench-guide.md) for the page-by-page guide.

## Related projects

- [bootloader](https://github.com/soccentric-stm32/bootloader) — A/B bootloader with UART update, CRC32 checks, trial boot and rollback
- [app-c-freertos](https://github.com/soccentric-stm32/app-c-freertos) — Demo application in C on FreeRTOS
- [app-c-zephyr](https://github.com/soccentric-stm32/app-c-zephyr) — Demo application in C on Zephyr RTOS
- [app-rust-freertos](https://github.com/soccentric-stm32/app-rust-freertos) — Demo application in Rust on Embassy
- [app-rust-zephyr](https://github.com/soccentric-stm32/app-rust-zephyr) — Demo application in Rust on Zephyr RTOS
- [validation-firmware](https://github.com/soccentric-stm32/validation-firmware) — HWVAL hardware validation firmware
- [validation-gui](https://github.com/soccentric-stm32/validation-gui) — HWVAL Studio: desktop GUI for tests, updates and flashing
- [factory-firmware](https://github.com/soccentric-stm32/factory-firmware) — First application on a fresh board: writes .sys, .cal and .diag over UART
- [soccentric-stm32.github.io](https://github.com/soccentric-stm32/soccentric-stm32.github.io) — Project website

## License

Released under the [MIT License](LICENSE) © 2026 Sandesh Ghimire. You are free
to use, modify and ship it in your own products.

Contributions are welcome. Fork the repository, make your change on a branch and
open a pull request. Bug reports, ideas and improvements are all appreciated.
