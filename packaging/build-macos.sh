#!/usr/bin/env bash
# Build the macOS .app bundle and a .dmg from packaging/hwval-factory.spec.
#
# The spec emits dist/hwval-factory.app on macOS (BUNDLE around the one-folder
# output); this script then stages that .app next to an /Applications link and
# packs the stage with hdiutil.  The bundle is unsigned and not notarised;
# release signing is done out of band by the release owner.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"
# Prefer the project virtualenv when it exists; callers can override PYTHON.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON=python3
  fi
fi
"$PYTHON" -m pip install -e . pyinstaller
"$PYTHON" -m PyInstaller --clean --noconfirm packaging/hwval-factory.spec

VERSION="$("$PYTHON" -c 'import sys; sys.path.insert(0,"src"); from hwval_factory import __version__; print(__version__)')"
APP="$ROOT/dist/hwval-factory.app"
if [ ! -d "$APP" ]; then
  echo "build-macos: $APP was not produced (the spec only builds it on macOS)" >&2
  exit 1
fi

DMG="$ROOT/dist/HWVAL-Factory-${VERSION}.dmg"
STAGE="$ROOT/build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "HWVAL Factory" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "macOS package: $DMG"
