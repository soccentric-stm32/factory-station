# Contributing to HWVAL Factory (factory-station)

Thanks for taking the time to contribute. This project is part of the
[soccentric-stm32](https://github.com/soccentric-stm32) STM32F767ZI baseline and is released under
the MIT License.

## Before you start

- Read [README.md](README.md) for what factory-station does and which tooling it needs.
- Report security issues privately, following [SECURITY.md](SECURITY.md), and not
  in a public issue.
- By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

Python 3.11+:

```bash
make venv
make install      # or: make dev
make run
```

Every page also runs against a simulated board, so no hardware is needed to try
the flow. See [README.md](README.md).

## Building and testing

| Command | What it does |
|---|---|
| `make check` | Run lint, type check and the test suite |
| `make test` | Run the test suite |
| `make lint` | Run the linter |
| `make typecheck` | Run mypy |
| `make package-linux` | Build the distributable for Linux |
| `make run` | Start the GUI |

Run `make check` (and `make test`) before you open a pull request, and add tests
when you change behaviour that is covered by the suite.

## Code style

- Follow the style of the surrounding code; do not reformat unrelated lines.
- Keep the existing formatting and lint configuration (`.editorconfig`, the `ruff`
  and `mypy` settings in `pyproject.toml`) authoritative instead of arguing about
  taste.
- Keep the Qt/PySide layer thin: logic that can live in the Qt-free core belongs
  there and should be covered by tests.
- Describe how you tested the change: against a simulated board, on real
  hardware, or both.

## Branch and commit conventions

- Branch off `master` and use a short, descriptive kebab-case name, for example
  `fix-spi-timeout` or `add-imx708-autofocus`.
- Write imperative, present-tense commit subjects (`fix:`, `feat:`, `docs:`,
  `refactor:`, `test:`, `build:`), keep them under about 72 characters and explain
  the reason for the change in the body.
- One logical change per commit.

## Pull requests

- Explain what changed and why, and how you tested it (including the board and
  toolchain you used).
- Reference the issue you are fixing (`Fixes #123`).
- Keep the pull request focused; split unrelated changes into separate ones.
- The Jenkins pipeline in the repository must be green before the change
  is merged.

## Changelog and releases

- Record user-visible changes under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md), following
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
- Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
  releases are tagged `vMAJOR.MINOR.PATCH`.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE), the same terms as the rest of the project.
