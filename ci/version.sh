#!/usr/bin/env bash
#
# version.sh - print the version this build reports, without a leading v
#
# Author:  Sandesh Ghimire <sandesh@soccentric.com>
#
# Used by the Jenkins Package and Release stages.  The source of truth is
# the project version in pyproject.toml; the release tag must be v<this version>.
#

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
sed -n 's/^version *= *"\([^"]*\)".*/\1/p' pyproject.toml | head -n 1
