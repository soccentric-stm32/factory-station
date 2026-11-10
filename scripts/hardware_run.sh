#!/usr/bin/env bash
# Run the hardware acceptance sequence from docs/hardware-verification.md.
#
# Usage: scripts/hardware_run.sh [FIXTURE] [PROFILE] [PCB_SERIAL]
# Requires a NUCLEO-F767ZI with the ST-LINK and VCP attached.  The script
# never signs an image and never applies RDP level 2.
set -euo pipefail

FIXTURE="${1:-F1}"
PROFILE="${2:-profiles/sc767-1.1.0}"
PCB_SERIAL="${3:-PCB-HW-$(date +%Y%m%d%H%M%S)}"
OUT="${OUT:-station-data/hardware-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "error: run 'make dev' first" >&2
    exit 2
fi

echo "== detect =="
"$PY" -m hwval_factory.cli --detect | tee "$OUT/detect.txt"

echo "== profile =="
"$PY" -m hwval_factory.profile_tool verify "$PROFILE" | tee "$OUT/profile.txt"

echo "== full run =="
"$PY" -m hwval_factory.cli --cli --no-simulate --fixture "$FIXTURE" \
    --profile "$PROFILE" --pcb-serial "$PCB_SERIAL" --operator admin \
    --json | tee "$OUT/run.json"

echo "== boot check =="
"$PY" -m hwval_factory.cli tool boot-check --fixture "$FIXTURE" \
    --profile "$PROFILE" --no-simulate --operator admin --json \
    | tee "$OUT/boot-check.json"

echo "== rework (same board, now protected) =="
"$PY" -m hwval_factory.cli --cli --no-simulate --fixture "$FIXTURE" \
    --profile "$PROFILE" --pcb-serial "$PCB_SERIAL" --operator admin \
    --kind rework --approve-rework --json | tee "$OUT/rework.json"

echo
echo "Record $OUT in docs/hardware-verification.md (date, ST-LINK serial, UID,"
echo "per-step durations, option bytes, factory image hash, key id, floor)."
