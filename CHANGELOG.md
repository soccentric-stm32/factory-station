# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-19

### Added

- HWVAL Factory: cross-platform PySide6 bench tool that takes a blank board
  to a provisioned unit, one page at a time
- Home page with the eight bench actions (detect, load BOOT, load the factory
  firmware, device info, SYS, CAL, DIAG, validate) and an optional run sequence
- SYS / CAL / DIAG editors with CSV data, records, output files and board views
- Every page works against a simulated board, so the flow can be tried
  without hardware

[Unreleased]: https://github.com/soccentric-stm32/factory-station/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/soccentric-stm32/factory-station/releases/tag/v1.0.0
