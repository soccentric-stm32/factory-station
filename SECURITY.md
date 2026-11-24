# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | :white_check_mark: |
| < 1.0 | :x: |

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do not report security issues in public issues, pull requests or
discussions. Report them privately instead:

- Open a private advisory at
  <https://github.com/soccentric-stm32/factory-station/security/advisories/new>, or
- email <sandesh@soccentric.com>.

Please include:

- the affected version, tag or commit,
- the impact and the attack surface you can reach,
- the steps to reproduce, or a proof of concept,
- any suggested fix, if you have one.

We will acknowledge the report, keep you informed about the progress and credit
you in the advisory unless you prefer otherwise. Please give us a reasonable
amount of time to ship a fix before you disclose the issue publicly.

## Scope

The packaging scripts, the CSV import paths and the serial/HWLINK layers are
in scope as well.

Reports about the Python packages in `requirements.txt` should go to their
upstream projects; tell us as well if the issue affects this project.
